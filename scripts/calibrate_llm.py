"""Record real Gemini latency per agent invocation for replay in load tests.

Usage:  .venv/bin/python scripts/calibrate_llm.py --runs 3

Each run is one full planning session (~15-20 model calls). Rows are appended
to runs/llm_trace.jsonl; re-running adds samples rather than replacing them.
This is the ONLY step in the load-testing workflow that touches the API.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.orchestrator import run_swarm  # noqa: E402
from app.llm.latency import TRACE_PATH, LatencyModel  # noqa: E402
from app.state.registry import backends  # noqa: E402

CITIES = ["Lisbon", "Tokyo", "Paris", "Mexico City", "Reykjavik", "New York"]


async def main(runs: int) -> None:
    await backends.startup()
    try:
        for i in range(runs):
            brief = {
                "destination": CITIES[i % len(CITIES)], "origin": "SFO",
                "start_date": "2026-10-10", "end_date": "2026-10-14",
                "travelers": 2, "budget_total": 4000,
                "nuance": "love food markets and sunset viewpoints",
            }
            t0 = time.perf_counter()
            try:
                # Benchmark tooling: plan from the curated catalog, as the
                # load tests that replay these latencies do.
                s = await run_swarm(brief, "valkey", llm_mode="gemini", live_research=False)
                print(f"run {i + 1}/{runs} {brief['destination']}: "
                      f"{s['e2e_latency_ms'] / 1000:.1f}s, errors={s['errors']}")
            except Exception as exc:  # noqa: BLE001 - keep calibrating on failures
                print(f"run {i + 1}/{runs} failed after "
                      f"{time.perf_counter() - t0:.1f}s: {exc!r}")
    finally:
        await backends.shutdown()

    model = LatencyModel.from_trace(TRACE_PATH)
    print(f"\ntrace: {TRACE_PATH}")
    for role, d in model.describe()["per_role"].items():
        print(f"  {role:20s} n={d['n']:3d}  p50={d['p50_ms'] / 1000:5.1f}s  "
              f"max={d['max_ms'] / 1000:5.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    asyncio.run(main(ap.parse_args().runs))
