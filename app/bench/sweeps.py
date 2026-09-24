"""Sweeps: the axes where an in-memory tier can actually earn its place.

WRITE-FREQUENCY (primary). At one scratchpad write per agent step the answer is
trivially "no difference" -- the storage cost is lost in the noise of everything
else. The real argument for an in-memory tier is that it ENABLES A CHATTIER
AGENT DESIGN: checkpointing intermediate reasoning, fine-grained sub-goals,
speculative branches. Sweeping 1 -> 10 -> 100 writes per step asks whether the
curves diverge, which is the question worth answering.

CONCURRENCY (secondary). Multiple simultaneous planning sessions contending for
the same 1.0 CPU / 256MB envelope.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agents.orchestrator import run_swarm
from app.bench.runner import run_benchmark
from app.bench.stats import compare, describe

WRITE_FREQUENCIES = (1, 10, 100)
CONCURRENCY_LEVELS = (1, 4, 16, 64)


async def write_frequency_sweep(
    brief: dict[str, Any],
    arms: list[str],
    *,
    repeats: int = 30,
    warmups: int = 3,
    frequencies: tuple[int, ...] = WRITE_FREQUENCIES,
    baseline: str = "postgres",
    progress=None,
) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for freq in frequencies:
        cells[str(freq)] = await run_benchmark(
            brief,
            arms,
            repeats=repeats,
            warmups=warmups,
            writes_per_step=freq,
            baseline=baseline,
            progress=progress,
        )
    return {"axis": "writes_per_step", "frequencies": list(frequencies), "cells": cells}


async def concurrency_sweep(
    brief: dict[str, Any],
    arms: list[str],
    *,
    levels: tuple[int, ...] = CONCURRENCY_LEVELS,
    writes_per_step: int = 10,
    repeats: int = 5,
) -> dict[str, Any]:
    """Run `level` planning sessions simultaneously and measure completion time."""
    results: dict[str, Any] = {}

    for level in levels:
        per_arm: dict[str, list[float]] = {a: [] for a in arms}
        for _ in range(repeats):
            for arm in arms:
                tasks = [
                    run_swarm(
                        brief,
                        arm,
                        llm_mode="fake",
                        writes_per_step=writes_per_step,
                        provider_latency_ms=0,
                        variant_id=f"{arm}|c{level}",
                    )
                    for _ in range(level)
                ]
                summaries = await asyncio.gather(*tasks, return_exceptions=True)
                for s in summaries:
                    # A backend that collapses under concurrency must show up as a
                    # failure, not as a silently missing data point.
                    if isinstance(s, Exception):
                        per_arm[arm].append(float("nan"))
                    else:
                        per_arm[arm].append(s["e2e_latency_ms"])

        baseline_arm = "postgres" if "postgres" in arms else arms[0]
        clean = {
            a: [v for v in vals if v == v]  # drop NaNs from failed runs
            for a, vals in per_arm.items()
        }
        results[str(level)] = {
            "arms": {a: describe(v) for a, v in clean.items()},
            "failures": {a: sum(1 for v in per_arm[a] if v != v) for a in arms},
            "comparisons": [
                compare(baseline_arm, clean[baseline_arm], a, clean[a]).__dict__
                for a in arms
                if a != baseline_arm
            ],
        }

    return {"axis": "concurrent_sessions", "levels": list(levels), "cells": results}
