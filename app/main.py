"""FastAPI app for the travel concierge demo.

Endpoints
  GET  /                     the single-page UI (Experience + Under the Hood tabs)
  GET  /api/config           what the UI needs: stores, model, limits
  POST /api/run              plan a trip; Server-Sent Events stream of the run
  GET  /api/inspect/{run_id} raw, uninstrumented contents of a run's store
  GET  /api/search-suggestions/{run_id}/{kind}
                             Google's Search Suggestions for a run's grounded
                             research, framed by the page (Google's terms
                             require showing them with grounded results)
  GET  /healthz              store liveness

Benchmarks are not served here. They run from the shell (scripts/bench.sh and
scripts/load_campaign.sh) against the same code.

TODO(security): this is a customer demo, not a production deployment (per
project direction), so it has no authentication, no rate limiting and serves
plain HTTP. Before exposing it beyond a trusted network add authentication
(e.g. IAP or OAuth), per-client rate limits on /api/run (each run makes ~15
paid model calls plus ~7 Google-Search-grounded research calls), TLS and HSTS.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
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
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.datastructures import MutableHeaders

from app.agents.orchestrator import run_swarm
from app.agents.swarm import PLAN_LOOP_MAX_ROUNDS
from app.config import settings
from app.providers import research
from app.state.inspector import describe_layout, snapshot
from app.state.registry import BACKEND_SPECS, UI_STORES, backends
from app.telemetry.feed import RunStream

log = logging.getLogger("travel_swarm.api")

STATIC_DIR = Path(__file__).parent / "static"
# The page's script and stylesheet. index.html requests them with a content
# hash in the URL (see asset_version), so a browser never pairs a new page with
# an older copy of either that it cached earlier.
UI_ASSETS = ("app.js", "styles.css")

# While a model call is in flight the run stream can be quiet for a long time.
# A comment line this often keeps proxies from idling it out, and lets the page
# tell a slow model from a dead connection (app.js STALL_TIMEOUT_MS).
HEARTBEAT_S = 15.0

# UI runs always use the live model. The scripted model is only for benchmarks
# and tests; tests override this constant. There is deliberately no request
# parameter to switch it.
UI_LLM_MODE = "gemini"

MAX_TRIP_NIGHTS = 21
STORE_LABELS = {"valkey": "Valkey", "postgres": "PostgreSQL"}
RUN_ID_RE = re.compile(rf"^({'|'.join(map(re.escape, UI_STORES))})-[0-9a-f]{{8}}$")
SUGGESTIONS_PATH = "/api/search-suggestions/"

# Any destination or origin: the live model identifies it (research.py), so
# the API only checks that the text looks like a place name. Letters in any
# script, digits, spaces and a little punctuation; at least one letter.
_PLACE_CHARS = re.compile(r"[\w .,'’()&/\-]+")
_HAS_LETTER = re.compile(r"[^\W\d_]")

ERROR_MESSAGES = {
    "config": "The concierge isn't available right now: the server has no model "
              "API key configured.",
    "model_busy": "Our planning model is very busy right now. Please try again in "
                  "a minute.",
    "timeout": "Planning took longer than expected. Please try again.",
    "unknown_destination": "We couldn't find that destination. Check the spelling, or "
                           "try a city, island or region.",
    "unknown_origin": "We couldn't find the place you're departing from. Try a city "
                      "name or an airport code.",
    "research_failed": "We couldn't research this trip just now. Please try again in "
                       "a minute.",
    "internal": "Something went wrong while planning your trip. Please try again.",
}
# Caused by what the traveler typed, not by a fault: logged as one line.
TRAVELER_ERRORS = frozenset({"unknown_destination", "unknown_origin"})


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
        # Without an explicit policy browsers cache the page and its assets
        # heuristically (from Last-Modified), and after a deploy can run a new
        # page with an old cached app.js. Always revalidate instead: for static
        # files that is a cheap 304 via their ETag.
        revalidate = path == "/" or path.startswith("/static/")
        framed_doc = path.startswith(SUGGESTIONS_PATH)

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # The suggestions document brings its own, stricter CSP and may
                # be framed by this page (see search_suggestions). Its error
                # responses carry no CSP, so they get the defaults.
                own_policy = framed_doc and "content-security-policy" in headers
                for name, value in _SECURITY_HEADERS.items():
                    if own_policy and name in ("Content-Security-Policy", "X-Frame-Options"):
                        continue
                    headers[name] = value
                if no_store:
                    headers["Cache-Control"] = "no-store"
                elif revalidate:
                    headers["Cache-Control"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)


# ---------------------------------------------------------------- models
class Brief(BaseModel):
    """A trip request. Every field is bounded and unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    destination: str = Field("Lisbon", min_length=2, max_length=80)
    origin: str = Field("SFO", min_length=2, max_length=60)
    start_date: date = date(2026, 10, 10)
    end_date: date = date(2026, 10, 14)
    travelers: int = Field(2, ge=1, le=12)
    budget_total: float = Field(4000, ge=0, le=1_000_000, allow_inf_nan=False)
    nuance: str = Field("", max_length=1000)
    backend: Literal["valkey", "postgres"] = "valkey"

    @field_validator("destination", "origin")
    @classmethod
    def _place_name(cls, value: str) -> str:
        # Whether the place exists is the live model's call (research.py); an
        # unknown one ends the run with a friendly "couldn't find" message.
        text = " ".join(value.split())
        if "_" in text or not (_PLACE_CHARS.fullmatch(text) and _HAS_LETTER.search(text)):
            raise ValueError("should be a place name, like Lisbon or Bali")
        return text

    @model_validator(mode="after")
    def _check(self) -> "Brief":
        nights = (self.end_date - self.start_date).days
        if nights < 1:
            raise ValueError("end_date must be after start_date")
        if nights > MAX_TRIP_NIGHTS:
            raise ValueError(f"trips are limited to {MAX_TRIP_NIGHTS} nights")
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


def _causes(exc: BaseException) -> list[BaseException]:
    """The exception and everything it wraps: group members, causes, contexts.

    A research failure inside the parallel fan-out reaches the API wrapped in
    an ExceptionGroup, and sometimes re-raised by the agent framework.
    """
    out: list[BaseException] = []
    stack = [exc]
    while stack and len(out) < 64:
        e = stack.pop()
        if any(e is seen for seen in out):
            continue
        out.append(e)
        if isinstance(e, BaseExceptionGroup):
            stack.extend(e.exceptions)
        stack.extend(x for x in (e.__cause__, e.__context__) if x is not None)
    return out


def _error_category(exc: BaseException) -> str:
    """Map a failure to a client-safe category. Details stay in the server log."""
    from google.genai import errors as genai_errors
    import httpx

    causes = _causes(exc)
    for e in causes:
        if isinstance(e, research.ResearchError) and e.category in ERROR_MESSAGES:
            return e.category
    for e in causes:
        if isinstance(e, genai_errors.APIError) and getattr(e, "code", None) in (
            429, 500, 502, 503, 504
        ):
            return "model_busy"
        if isinstance(e, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
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
            category = _error_category(exc)
            if category in TRAVELER_ERRORS:
                log.warning("run %s: %s", run_id, category)  # the input, not a fault
            else:
                log.exception("run %s failed (%s)", run_id, category)
            queue.put_nowait(_error_event(category))
        finally:
            queue.put_nowait(None)

    async def stream():
        task = asyncio.create_task(execute())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    # An SSE comment: the page ignores it, but it proves the
                    # connection is alive while the model is thinking.
                    yield ": keep-alive\n\n"
                    continue
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


_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.DOTALL | re.IGNORECASE)
_FRAME_STYLE = "<style>body{margin:0}</style>"


def _style_hashes(page: str) -> str:
    """CSP source list allowing exactly the page's own <style> blocks."""
    return " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(css.encode("utf-8")).digest()).decode() + "'"
        for css in _STYLE_BLOCK.findall(page)
    ) or "'none'"


@app.get(SUGGESTIONS_PATH + "{run_id}/{kind}", include_in_schema=False)
async def search_suggestions(run_id: str, kind: str) -> HTMLResponse:
    """Google's Search Suggestions for one grounded research step.

    Google renders this HTML, and its terms require showing it, unmodified,
    wherever grounded results are shown. It is served as its own document so
    the page never parses it: the page frames it with a sandbox, and this CSP
    sandboxes it again (for direct visits), allows no scripts at all, and only
    the inline style blocks it arrived with, by hash. Links open in a new tab.
    """
    if not RUN_ID_RE.fullmatch(run_id) or kind not in research.KINDS:
        raise HTTPException(status_code=404, detail="Unknown run.")
    html = research.suggestions.get(run_id, kind)
    if html is None:
        raise HTTPException(status_code=404, detail="No search suggestions for this run.")
    # The HTML parser folds CR LF to LF before a style block is hashed.
    html = html.replace("\r\n", "\n").replace("\r", "\n")
    page = ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="referrer" content="no-referrer"><base target="_blank">'
            f"{_FRAME_STYLE}</head><body>{html}</body></html>")
    csp = ("sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
           f"style-src {_style_hashes(page)}; img-src https: data:; base-uri 'none'; "
           "form-action 'none'; frame-ancestors 'self'")
    return HTMLResponse(page, headers={"Content-Security-Policy": csp,
                                       "X-Frame-Options": "SAMEORIGIN"})


def asset_version(static_dir: Path = STATIC_DIR) -> str:
    """Short content hash of UI_ASSETS; changes whenever either file does."""
    digest = hashlib.sha256()
    for name in UI_ASSETS:
        digest.update((static_dir / name).read_bytes())
    return digest.hexdigest()[:12]


@app.get("/", include_in_schema=False)
async def index() -> HTMLResponse:
    # A new URL per asset version: a browser that cached an older app.js under
    # the plain URL (before no-cache was sent) cannot reuse it with this page.
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(page.replace("__ASSET_VERSION__", asset_version()))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
