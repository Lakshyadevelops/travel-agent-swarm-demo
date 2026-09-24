"""Benchmark runner: interleaved, warm-up-discarding, CI-reporting.

Two methodology choices that matter more than anything else here:

1. INTERLEAVING. Arms run round-robin (A,B,C,A,B,C,...), not all-A-then-all-B.
   Sequential blocks absorb machine drift -- thermal state, page cache warmth,
   whatever else is running on the box -- into whichever arm happened to run
   second. Interleaving spreads that noise evenly and enables paired statistics.

2. WARM-UP DISCARDS. The first iterations pay for JIT, connection pool fill and
   cold caches. Our own smoke test showed the first arm reporting 1765ms against
   a steady-state ~150ms. Discarding them is not massaging the data; including
   them would measure Python startup, not storage.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Iterable

from app.agents.orchestrator import run_swarm
from app.bench.stats import compare, describe
from app.config import settings
from app.state.registry import BACKEND_SPECS
from app.telemetry.oplog import OpLog

DEFAULT_REPEATS = 30
DEFAULT_WARMUPS = 3


async def run_benchmark(
    brief: dict[str, Any],
    arms: Iterable[str],
    *,
    repeats: int = DEFAULT_REPEATS,
    warmups: int = DEFAULT_WARMUPS,
    writes_per_step: int = 1,
    llm_mode: str = "fake",
    provider_latency_ms: int = 0,
    baseline: str = "postgres",
    progress=None,
) -> dict[str, Any]:
    """Run every arm `repeats` times, interleaved, and compare against baseline."""
    arms = [a for a in arms if a in BACKEND_SPECS]
    if not arms:
        raise ValueError("no valid arms requested")

    bench_id = f"bench-{uuid.uuid4().hex[:8]}"
    oplog = OpLog(bench_id)
    oplog.start()

    e2e: dict[str, list[float]] = {a: [] for a in arms}
    io_busy: dict[str, list[float]] = {a: [] for a in arms}
    op_counts: dict[str, list[int]] = {a: [] for a in arms}
    errors: dict[str, int] = {a: 0 for a in arms}
    round_trips: dict[str, list[int]] = {a: [] for a in arms}
    cache_hit_rates: list[float] = []
    evicted: dict[str, int] = {a: 0 for a in arms}

    total = (warmups + repeats) * len(arms)
    done = 0
    started_wall = time.time()

    # Round-robin over arms so drift hits every arm equally.
    for iteration in range(-warmups, repeats):
        for arm in arms:
            summary = await run_swarm(
                brief,
                arm,
                llm_mode=llm_mode,
                writes_per_step=writes_per_step,
                provider_latency_ms=provider_latency_ms,
                variant_id=f"{arm}|w{writes_per_step}",
                iteration=iteration,
                oplog=oplog,
            )
            done += 1
            if progress is not None:
                progress(done, total, arm, iteration)

            if iteration < 0:
                continue  # warm-up: recorded in the op log, excluded from stats

            e2e[arm].append(summary["e2e_latency_ms"])
            io_busy[arm].append(summary["io_busy_ms"])
            op_counts[arm].append(summary["ops_total"])
            round_trips[arm].append(summary["round_trips_total"])
            errors[arm] += summary["errors"]
            evicted[arm] = max(evicted[arm], summary.get("evicted_keys", 0))
            if summary.get("cache") and summary["cache"].get("hit_rate_pct") is not None:
                cache_hit_rates.append(summary["cache"]["hit_rate_pct"])

    await oplog.close()

    baseline_arm = baseline if baseline in arms else arms[0]
    comparisons = [
        compare(baseline_arm, e2e[baseline_arm], arm, e2e[arm]).__dict__
        for arm in arms
        if arm != baseline_arm
    ]

    manifest = {
        "bench_id": bench_id,
        "arms": arms,
        "repeats": repeats,
        "warmups_discarded": warmups,
        "writes_per_step": writes_per_step,
        "llm_mode": llm_mode,
        "provider_latency_ms": provider_latency_ms,
        "baseline": baseline_arm,
        "brief": brief,
        "gemini_model": settings.gemini_model,
        "ordering": "interleaved round-robin",
        "ci_method": "paired percentile bootstrap, 10000 resamples",
        "container_caps": "cpus=1.0, memory=256M per store",
        "started_at": started_wall,
        "duration_s": round(time.time() - started_wall, 2),
    }
    oplog.write_manifest(manifest)

    return {
        "bench_id": bench_id,
        "manifest": manifest,
        "arms": {
            arm: {
                "label": BACKEND_SPECS[arm].label,
                "notes": BACKEND_SPECS[arm].notes,
                "e2e": describe(e2e[arm]),
                "io_busy": describe(io_busy[arm]),
                "ops_per_run": round(
                    sum(op_counts[arm]) / len(op_counts[arm]), 1
                )
                if op_counts[arm]
                else 0,
                "round_trips_per_run": round(
                    sum(round_trips[arm]) / len(round_trips[arm]), 1
                )
                if round_trips[arm]
                else 0,
                "errors": errors[arm],
                "evicted_keys": evicted[arm],
            }
            for arm in arms
        },
        "comparisons": comparisons,
        "cache_hit_rate_pct": round(
            sum(cache_hit_rates) / len(cache_hit_rates), 2
        )
        if cache_hit_rates
        else None,
        "oplog_path": str(oplog.path),
    }
