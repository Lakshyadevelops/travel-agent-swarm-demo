"""Instrumentation wrapper.

Every state-layer read and write passes through `timed()`. Agent role, the active
telemetry collector, and the current sweep cell travel via contextvars so store
implementations never have to thread them through their signatures.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator

from app.telemetry.metrics import RunTelemetry, StateOp
from app.telemetry.oplog import OpLog

CURRENT_AGENT: ContextVar[str] = ContextVar("current_agent", default="runner")
CURRENT_RUN: ContextVar[RunTelemetry | None] = ContextVar("current_run", default=None)
CURRENT_OPLOG: ContextVar[OpLog | None] = ContextVar("current_oplog", default=None)
CURRENT_VARIANT: ContextVar[str] = ContextVar("current_variant", default="")
CURRENT_ITERATION: ContextVar[int] = ContextVar("current_iteration", default=-1)


class OpBox:
    """Mutable handle letting the wrapped block report op details back."""

    __slots__ = ("payload_bytes", "round_trips", "cache_hit")

    def __init__(self) -> None:
        self.payload_bytes: int = 0
        self.round_trips: int = 1
        self.cache_hit: bool | None = None


@asynccontextmanager
async def timed(
    op_type: str, backend: str, key: str = ""
) -> AsyncIterator[OpBox]:
    """Time one state operation and record it.

    Errors are recorded with their message rather than swallowed or dropped, then
    re-raised. A backend that fails fast would otherwise look artificially quick.
    """
    box = OpBox()
    start = time.perf_counter()
    error: str | None = None
    try:
        yield box
    except Exception as exc:  # noqa: BLE001 - recorded then re-raised
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        op = StateOp(
            op_type=op_type,
            agent_role=CURRENT_AGENT.get(),
            backend=backend,
            duration_ms=duration_ms,
            key=key,
            payload_bytes=box.payload_bytes,
            started_at=start,
            variant_id=CURRENT_VARIANT.get(),
            iteration=CURRENT_ITERATION.get(),
            error=error,
            round_trips=box.round_trips,
            cache_hit=box.cache_hit,
        )
        run = CURRENT_RUN.get()
        if run is not None:
            run.record(op)
        oplog = CURRENT_OPLOG.get()
        if oplog is not None:
            oplog.submit(op)
