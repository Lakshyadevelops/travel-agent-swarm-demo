"""FastAPI app for the travel concierge demo.

Endpoints
  GET  /                     the single-page UI (Experience + Under the Hood tabs)
  GET  /api/config           what the UI needs: stores, model, destination catalog
  POST /api/run              plan a trip; Server-Sent Events stream of the run
  GET  /api/inspect/{run_id} raw, uninstrumented contents of a run's store
  GET  /healthz              store liveness

Benchmarks are not served here. They run from the shell (scripts/bench.sh and
scripts/load_campaign.sh) against the same code.

TODO(security): this is a customer demo, not a production deployment (per
project direction), so it has no authentication, no rate limiting and serves
plain HTTP. Before exposing it beyond a trusted network add authentication
(e.g. IAP or OAuth), per-client rate limits on /api/run (each run makes ~15
paid model calls), TLS and HSTS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.datastructures import MutableHeaders

from app.agents.orchestrator import run_swarm
from app.agents.swarm import PLAN_LOOP_MAX_ROUNDS
from app.config import settings
from app.providers.data import match_destination, supported_destinations
from app.state.inspector import describe_layout, snapshot
from app.state.registry import BACKEND_SPECS, UI_STORES, backends
from app.telemetry.feed import RunStream

log = logging.getLogger("travel_swarm.api")

STATIC_DIR = Path(__file__).parent / "static"

# UI runs always use the live model. The scripted model is only for benchmarks
# and tests; tests override this constant. There is deliberately no request
# parameter to switch it.
UI_LLM_MODE = "gemini"

MAX_TRIP_NIGHTS = 21
STORE_LABELS = {"valkey": "Valkey", "postgres": "PostgreSQL"}
RUN_ID_RE = re.compile(rf"^({'|'.join(map(re.escape, UI_STORES))})-[0-9a-f]{{8}}$")

ERROR_MESSAGES = {
    "config": "The concierge isn't available right now: the server has no model "
              "API key configured.",
    "model_busy": "Our planning model is very busy right now. Please try again in "
                  "a minute.",
    "timeout": "Planning took longer than expected. Please try again.",
    "internal": "Something went wrong while planning your trip. Please try again.",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await backends.startup()
    yield
    await backends.shutdown()


# No /docs or /openapi.json: nothing in the demo needs them, and the Swagger
# page would load scripts from a CDN that the CSP below forbids anyway.
app = FastAPI(title="Travel Concierge", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- headers
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeaders:
    """Adds security headers to every response.

    Pure ASGI rather than BaseHTTPMiddleware, so the SSE stream is passed
    through chunk by chunk instead of being buffered.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        no_store = path.startswith("/api/") or path == "/healthz"

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers[name] = value
                if no_store:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)


# ---------------------------------------------------------------- models
class Brief(BaseModel):
    """A trip request. Every field is bounded and unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    destination: str = Field("Lisbon", min_length=1, max_length=80)
    origin: str = Field("SFO", min_length=2, max_length=40)
    start_date: date = date(2026, 10, 10)
    end_date: date = date(2026, 10, 14)
    travelers: int = Field(2, ge=1, le=12)
    budget_total: float = Field(4000, ge=0, le=1_000_000, allow_inf_nan=False)
    nuance: str = Field("", max_length=1000)
    backend: Literal["valkey", "postgres"] = "valkey"

    @model_validator(mode="after")
    def _check(self) -> "Brief":
        nights = (self.end_date - self.start_date).days
        if nights < 1:
            raise ValueError("end_date must be after start_date")
        if nights > MAX_TRIP_NIGHTS:
            raise ValueError(f"trips are limited to {MAX_TRIP_NIGHTS} nights")
        city = match_destination(self.destination)
        if city is None:
            raise ValueError("destination is not in the demo catalog")
        self.destination = city  # canonical name, e.g. "nyc" -> "New York"
        return self


def require_same_origin_json(request: Request) -> None:
    """Stop other sites from starting runs (each one spends model quota).

    A JSON content type forces a CORS preflight, which this app never grants,
    and a present Origin header must match the Host the page was served from.
    """
    ctype = request.headers.get("content-type", "")
    if not ctype.lower().startswith("application/json"):
        raise HTTPException(status_code=415, detail="Expected application/json.")
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="Cross-origin requests are not allowed.")


def _error_category(exc: BaseException) -> str:
    """Map a failure to a client-safe category. Details stay in the server log."""
    from google.genai import errors as genai_errors
    import httpx

    if isinstance(exc, genai_errors.APIError) and getattr(exc, "code", None) in (
        429, 500, 502, 503, 504
    ):
        return "model_busy"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return "timeout"
    return "internal"


def _error_event(category: str) -> dict[str, Any]:
    return {"type": "error", "category": category, "message": ERROR_MESSAGES[category]}


# ---------------------------------------------------------------- routes
@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    stores = await backends.health()
    return {"stores": {k: v for k, v in stores.items() if k in UI_STORES}}


@app.get("/api/config")
async def config() -> dict[str, Any]:
    return {
        "stores": [
            {"id": s, "label": STORE_LABELS[s], "detail": BACKEND_SPECS[s].label}
            for s in UI_STORES
        ],
        "default_store": UI_STORES[0],
        "model": settings.gemini_model,
        "has_api_key": bool(settings.google_api_key),  # never the key itself
        "destinations": supported_destinations(),
        "max_nights": MAX_TRIP_NIGHTS,
        "max_rounds": PLAN_LOOP_MAX_ROUNDS,
        "scratchpad_ttl_s": settings.scratchpad_ttl_seconds,
    }


@app.post("/api/run", dependencies=[Depends(require_same_origin_json)])
async def run(brief: Brief) -> StreamingResponse:
    """Plan a trip, streaming agent steps, blackboard signals and store ops."""
    run_id = f"{brief.backend}-{uuid.uuid4().hex[:8]}"
    queue = RunStream()
    payload = brief.model_dump(mode="json", exclude={"backend"})
    queue.put_nowait({
        "type": "started",
        "run_id": run_id,
        "backend": brief.backend,
        "llm_mode": UI_LLM_MODE,
        "model": settings.gemini_model if UI_LLM_MODE == "gemini" else "scripted",
        "max_rounds": PLAN_LOOP_MAX_ROUNDS,
        "layout": describe_layout(brief.backend, run_id),
    })

    async def execute() -> None:
        try:
            if UI_LLM_MODE == "gemini" and not settings.google_api_key:
                queue.put_nowait(_error_event("config"))
                return
            result = await run_swarm(
                payload,
                brief.backend,
                llm_mode=UI_LLM_MODE,
                run_id=run_id,
                step_queue=queue,
            )
            queue.put_nowait({"type": "complete", "result": result})
        except Exception as exc:  # noqa: BLE001 - reported to the client generically
            log.exception("run %s failed", run_id)
            queue.put_nowait(_error_event(_error_category(exc)))
        finally:
            queue.put_nowait(None)

    async def stream():
        task = asyncio.create_task(execute())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item, default=str)}\n\n"
            await task
        finally:
            # The client went away mid-run: stop spending model calls on it.
            if not task.done():
                task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


@app.get("/api/inspect/{run_id}")
async def inspect_run(run_id: str) -> dict[str, Any]:
    """What is physically in the store for this run right now."""
    if not RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="Unknown run.")
    backend_id = run_id.rsplit("-", 1)[0]
    try:
        snap = await snapshot(backend_id, run_id)
    except Exception:  # noqa: BLE001 - details stay in the server log
        log.exception("inspect %s failed", run_id)
        raise HTTPException(status_code=500, detail="Could not read the store.") from None
    if snap is None:
        raise HTTPException(status_code=404, detail="Unknown run.")
    snap["layout"] = describe_layout(backend_id, run_id)
    return snap


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
