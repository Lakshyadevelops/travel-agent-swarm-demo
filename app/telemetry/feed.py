"""Live state feed for the Under the Hood tab.

Two small pieces, both used only by UI runs (benchmarks and load tests pass no
queue, so none of this runs there):

* `RunStream` is the per-run SSE queue. It stamps every event with a sequence
  number and a run-relative time when it is emitted, so agent steps, blackboard
  signals and store operations share one ordered timeline.
* `StateFeed` is bound as the run's op listener (see instrument.py). It turns
  each recorded state operation into one `state_op` event carrying the field
  and value, so the UI can rebuild the session and scratchpad at any step.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import orjson

from app.telemetry.instrument import OpBox
from app.telemetry.metrics import StateOp


class RunStream(asyncio.Queue):
    """SSE queue that stamps `seq` and `t_ms` on every dict event it receives."""

    def __init__(self) -> None:
        super().__init__()
        self._t0 = time.perf_counter()
        self._seq = 0

    def put_nowait(self, item: Any) -> None:  # Queue.put() delegates here too
        if isinstance(item, dict) and "seq" not in item:
            self._seq += 1
            item["seq"] = self._seq
            item["t_ms"] = round((time.perf_counter() - self._t0) * 1000.0, 1)
        super().put_nowait(item)


def _text(k: Any) -> str:
    return k.decode() if isinstance(k, (bytes, bytearray)) else str(k)


def _jsonable(op_type: str, value: Any) -> Any:
    """Snapshot a store-provided value as plain JSON types, at emission time.

    Copying here (rather than streaming the live object) means the UI shows
    exactly what was written, even if the caller mutates the object later.
    """
    if value is None:
        return None
    if callable(value):  # lazy value: computed only now, after timing stopped
        value = value()
    if op_type == "scratch_read_all":
        return [_text(k) for k in value]  # field names only
    if hasattr(value, "model_dump"):  # ADK Event
        return value.model_dump(mode="json", exclude_none=True)
    return orjson.loads(orjson.dumps(value, default=str))


class StateFeed:
    """Op listener that streams one `state_op` event per recorded operation."""

    def __init__(self, queue: asyncio.Queue, round_fn: Callable[[], int] | None = None):
        self._queue = queue
        self._round = round_fn

    def __call__(self, op: StateOp, box: OpBox) -> None:
        prefix, _, verb = op.op_type.partition("_")
        event: dict[str, Any] = {
            "type": "state_op",
            "store": "session" if prefix == "session" else "scratchpad",
            "op": verb,
            "op_type": op.op_type,
            "agent": op.agent_role,
            "backend": op.backend,
            "key": op.key,
            "field": box.field,
            "bytes": op.payload_bytes,
            "round_trips": op.round_trips,
            "duration_ms": round(op.duration_ms, 3),
            "round": self._round() if self._round else 0,
            # Class name only: exception messages can carry connection details.
            "error": op.error.split(":", 1)[0] if op.error else None,
        }
        if op.cache_hit is not None:
            event["cache_hit"] = op.cache_hit
        value = _jsonable(op.op_type, box.value)
        if value is not None:
            event["value"] = value
        self._queue.put_nowait(event)
