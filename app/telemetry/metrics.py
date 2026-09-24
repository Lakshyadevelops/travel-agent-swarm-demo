"""Telemetry primitives: the StateOp record and per-run aggregation.

Two design decisions worth calling out:

1. The headline metric is END-TO-END LATENCY, not I/O overhead ratios. A user
   feels wall-clock time to itinerary; they do not feel `io_ms / wall_ms`.

2. `io_busy_ms` is a UNION OF BUSY INTERVALS, not a sum of durations. The three
   research agents run concurrently under ParallelAgent, so summing their I/O
   time double-counts overlapping work and can exceed 100% of wall clock -- which
   would light the ">10% overhead" warning permanently and meaninglessly.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# Overhead above this fraction of wall clock is worth a diagnostic note.
# Demoted from a headline badge: at realistic LLM latencies this is always low,
# and under concurrency the naive version was mathematically broken.
OVERHEAD_DIAGNOSTIC_THRESHOLD_PCT = 10.0


@dataclass(slots=True)
class StateOp:
    """One read or write against the state layer."""

    op_type: str          # session_create | session_get | session_append | scratch_write | ...
    agent_role: str       # supervisor | scout | transit | stay | budget | itinerary | runner
    backend: str          # valkey | postgres | postgres_cached | ...
    duration_ms: float
    key: str
    payload_bytes: int
    started_at: float     # perf_counter origin; required for the interval union

    variant_id: str = ""        # which arm / sweep cell produced this op
    iteration: int = -1         # repeat index; -1 marks a discarded warm-up
    error: str | None = None    # errored/timed-out ops are recorded, never dropped
    round_trips: int = 1        # guards against silent chattiness regressions
    cache_hit: bool | None = None  # cached arm only

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile.

    Degrades sensibly on tiny samples (never NaN) -- in a live demo an honest
    number from n=3 beats an empty chart.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(pct / 100.0 * len(ordered) + 0.5))))
    return ordered[rank - 1]


def interval_union_ms(ops: Iterable[StateOp]) -> float:
    """Wall-clock time during which at least one state operation was in flight.

    Sorts by start and merges overlaps, so concurrent agents contribute the time
    actually spent waiting rather than the sum of their individual waits.
    """
    spans = sorted(
        (op.started_at, op.started_at + op.duration_ms / 1000.0) for op in ops
    )
    if not spans:
        return 0.0

    total = 0.0
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start > cur_end:          # disjoint -> bank the previous span
            total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:                         # overlapping -> extend
            cur_end = max(cur_end, end)
    total += cur_end - cur_start
    return total * 1000.0


class RunTelemetry:
    """Collects StateOps for a single swarm invocation."""

    def __init__(self, run_id: str, backend: str, variant_id: str = "") -> None:
        self.run_id = run_id
        self.backend = backend
        self.variant_id = variant_id
        self.ops: list[StateOp] = []
        self.wall_clock_ms: float = 0.0
        self.agent_steps: list[dict[str, Any]] = []
        self.evicted_keys: int = 0

    def record(self, op: StateOp) -> None:
        self.ops.append(op)

    @property
    def successful_ops(self) -> list[StateOp]:
        return [op for op in self.ops if op.error is None]

    def summary(self) -> dict[str, Any]:
        ok = self.successful_ops
        errored = [op for op in self.ops if op.error is not None]

        # Errored ops are excluded from latency percentiles (a timeout's duration
        # is an artifact of the timeout setting, not of the backend's speed) but
        # counted in error_rate -- dropping them entirely would flatter whichever
        # backend fails more often.
        durations = [op.duration_ms for op in ok]

        by_type: dict[str, list[float]] = {}
        by_agent: dict[str, list[float]] = {}
        for op in ok:
            by_type.setdefault(op.op_type, []).append(op.duration_ms)
            by_agent.setdefault(op.agent_role, []).append(op.duration_ms)

        busy_ms = interval_union_ms(ok)
        overhead_pct = (
            (busy_ms / self.wall_clock_ms * 100.0) if self.wall_clock_ms > 0 else 0.0
        )

        cache_ops = [op for op in ok if op.cache_hit is not None]
        cache_hits = [op for op in cache_ops if op.cache_hit]

        return {
            "run_id": self.run_id,
            "backend": self.backend,
            "variant_id": self.variant_id,
            # ---- HEADLINE ----
            "e2e_latency_ms": round(self.wall_clock_ms, 3),
            # ---- diagnostics ----
            "ops_total": len(self.ops),
            "errors": len(errored),
            "error_rate_pct": round(len(errored) / len(self.ops) * 100.0, 2)
            if self.ops
            else 0.0,
            "round_trips_total": sum(op.round_trips for op in self.ops),
            "io_busy_ms": round(busy_ms, 3),
            "io_overhead_pct": round(overhead_pct, 2),
            "io_overhead_is_high": overhead_pct > OVERHEAD_DIAGNOSTIC_THRESHOLD_PCT,
            "op_latency": {
                "n": len(durations),
                "p50_ms": round(_percentile(durations, 50), 3),
                "p95_ms": round(_percentile(durations, 95), 3),
                "p99_ms": round(_percentile(durations, 99), 3),
                "mean_ms": round(statistics.fmean(durations), 3) if durations else 0.0,
            },
            "ops_by_type": {
                k: {"n": len(v), "p50_ms": round(_percentile(v, 50), 3),
                    "p99_ms": round(_percentile(v, 99), 3)}
                for k, v in sorted(by_type.items())
            },
            "ops_by_agent": {
                k: {"n": len(v), "p50_ms": round(_percentile(v, 50), 3)}
                for k, v in sorted(by_agent.items())
            },
            "cache": {
                "n": len(cache_ops),
                "hit_rate_pct": round(len(cache_hits) / len(cache_ops) * 100.0, 2)
                if cache_ops
                else None,
                "hit_p50_ms": round(
                    _percentile([o.duration_ms for o in cache_hits], 50), 3
                )
                if cache_hits
                else None,
                "miss_p50_ms": round(
                    _percentile(
                        [o.duration_ms for o in cache_ops if not o.cache_hit], 50
                    ),
                    3,
                )
                if len(cache_ops) > len(cache_hits)
                else None,
            }
            if cache_ops
            else None,
            # Non-zero eviction means results are not comparable AND is a real
            # operational hazard of the cache approach. Surfaced, not hidden.
            "evicted_keys": self.evicted_keys,
            "agent_steps": self.agent_steps,
        }
