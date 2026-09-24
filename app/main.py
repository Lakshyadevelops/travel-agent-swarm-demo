"""FastAPI application: SSE run stream, metrics, scratchpad inspector, benchmarks."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agents.orchestrator import run_swarm
from app.bench.runner import run_benchmark
from app.bench.sweeps import concurrency_sweep, write_frequency_sweep
from app.config import settings
from app.llm.gemini_probe import probe_gemini_latency
from app.state.registry import BACKEND_SPECS, backends

STATIC_DIR = Path(__file__).parent / "static"

# Last result per run, so Tab 2 can inspect a completed run.
RESULTS: dict[str, dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await backends.startup()
    yield
    await backends.shutdown()


app = FastAPI(title="Travel Concierge Swarm", lifespan=lifespan)


class Brief(BaseModel):
    destination: str = "Lisbon"
    origin: str = "SFO"
    start_date: str = "2026-10-10"
    end_date: str = "2026-10-14"
    travelers: int = 2
    budget_total: float = 4000
    nuance: str = ""
    backend: str = "valkey"
    llm_mode: str | None = None
    writes_per_step: int | None = None


class BenchRequest(BaseModel):
    brief: Brief = Field(default_factory=Brief)
    arms: list[str] = Field(default_factory=lambda: ["valkey", "postgres", "postgres_cached"])
    repeats: int = 30
    warmups: int = 3
    writes_per_step: int = 1
    baseline: str = "postgres"


class SweepRequest(BaseModel):
    brief: Brief = Field(default_factory=Brief)
    arms: list[str] = Field(default_factory=lambda: ["valkey", "postgres", "postgres_cached"])
    repeats: int = 30
    warmups: int = 3
    frequencies: list[int] = Field(default_factory=lambda: [1, 10, 100])
    baseline: str = "postgres"


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"stores": await backends.health(), "llm_mode": settings.llm_mode}


@app.get("/api/backends")
async def list_backends() -> dict[str, Any]:
    return {
        "backends": [
            {"id": s.id, "label": s.label, "notes": s.notes, "is_durable": s.is_durable}
            for s in BACKEND_SPECS.values()
        ],
        "defaults": {
            "llm_mode": settings.llm_mode,
            "gemini_model": settings.gemini_model,
            "has_api_key": bool(settings.google_api_key),
            "container_caps": "cpus=1.0, memory=256M per store",
        },
    }


@app.post("/api/run")
async def run(brief: Brief) -> StreamingResponse:
    """Run the swarm, streaming agent steps as they happen."""
    run_id = f"{brief.backend}-{uuid.uuid4().hex[:8]}"
    queue: asyncio.Queue = asyncio.Queue()
    payload = brief.model_dump()

    async def execute() -> None:
        try:
            result = await run_swarm(
                payload,
                brief.backend,
                llm_mode=brief.llm_mode or settings.llm_mode,
                writes_per_step=brief.writes_per_step,
                run_id=run_id,
                step_queue=queue,
            )
            RESULTS[run_id] = result
            await queue.put({"type": "complete", "result": result})
        except Exception as exc:  # noqa: BLE001 - surface failures to the UI
            await queue.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            await queue.put(None)

    async def stream():
        task = asyncio.create_task(execute())
        yield f"data: {json.dumps({'type': 'started', 'run_id': run_id})}\n\n"
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, default=str)}\n\n"
        await task

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/metrics/{run_id}")
async def metrics(run_id: str) -> dict[str, Any]:
    result = RESULTS.get(run_id)
    if result is None:
        return {"error": "unknown run_id"}
    return {k: v for k, v in result.items() if k != "scratchpad"}


@app.get("/api/scratchpad/{run_id}")
async def scratchpad(run_id: str) -> dict[str, Any]:
    result = RESULTS.get(run_id)
    if result is not None:
        return {"run_id": run_id, "state": result.get("scratchpad", {})}
    # Fall back to a live read for a run still in flight.
    backend_id = run_id.rsplit("-", 1)[0]
    if backend_id not in BACKEND_SPECS:
        return {"error": "unknown run_id"}
    pad = backends.scratchpad(backend_id)
    return {"run_id": run_id, "state": await pad.read_all(run_id)}


@app.post("/api/benchmark")
async def benchmark(req: BenchRequest) -> dict[str, Any]:
    return await run_benchmark(
        req.brief.model_dump(),
        req.arms,
        repeats=req.repeats,
        warmups=req.warmups,
        writes_per_step=req.writes_per_step,
        baseline=req.baseline,
    )


@app.post("/api/sweep/write-frequency")
async def sweep_writes(req: SweepRequest) -> dict[str, Any]:
    return await write_frequency_sweep(
        req.brief.model_dump(),
        req.arms,
        repeats=req.repeats,
        warmups=req.warmups,
        frequencies=tuple(req.frequencies),
        baseline=req.baseline,
    )


@app.post("/api/sweep/concurrency")
async def sweep_concurrency(req: SweepRequest) -> dict[str, Any]:
    return await concurrency_sweep(req.brief.model_dump(), req.arms)


@app.get("/api/gemini-latency")
async def gemini_latency(samples: int = 30) -> dict[str, Any]:
    return await probe_gemini_latency(samples=samples)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
