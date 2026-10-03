"""Closed-loop concurrent-user load test with replayed LLM latency.

WHAT IS MEASURED: end-to-end latency per planning session -- the time from a
user pressing "Plan" to the final itinerary -- while N virtual users hammer the
same store. That is the only number a user experiences.

HOW IT STAYS REALISTIC WITHOUT THE API
  * Model time: replayed from a recorded trace of real Gemini calls, per agent
    role (see app/llm/latency.py). Zero API calls during load.
  * Tool/provider time: the same simulated 40ms flight/hotel API latency the
    demo uses.
  * Storage: the real Valkey / Postgres containers, real ADK session service,
    real scratchpad traffic, real connection pools.

HOW IT STAYS FAIR
  * Closed loop: each virtual user runs sessions back-to-back, like a user who
    plans a trip, reads it, and plans another. Users start staggered over a ramp
    so they don't arrive as one synchronized herd.
  * Paired workloads: virtual user u's i-th session uses the same brief AND the
    same sampled model latencies on every arm (RNG seeded by (u, i), never by
    arm). Arm-vs-arm deltas are computed on matched (u, i) pairs.
  * Multi-process: ADK is CPU-heavy Python. One process tops out long before a
    1-core Valkey does, and then you are benchmarking the load generator. So
    users are split across worker processes, and the client's own CPU usage is
    recorded next to the store's so a saturated client can't masquerade as a
    slow database.
  * Each worker also measures its own event-loop lag during the measured
    window: a late loop delays every await of every session on any store, so a
    high lag marks the level as limited by the load generator.
  * Workers start together: each signals ready after importing ADK and opening
    its pools, and the parent releases them with one shared start time.
  * Sessions are deleted after they complete (outside the timed window), as a
    real app would expire them, so neither store is measured with an
    ever-growing dataset or pushed into eviction.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import resource
import sys
import time
from pathlib import Path
from typing import Any

CITIES = ["Lisbon", "Paris", "London", "New York", "Tokyo", "Kyoto",
          "Mexico City", "Reykjavik"]


def brief_for(uid: int, i: int) -> dict[str, Any]:
    """Deterministic, varied workload: same (uid, i) -> same trip on every arm."""
    rng = random.Random(f"brief:{uid}:{i}")
    return {
        "destination": CITIES[(uid + i) % len(CITIES)],
        "origin": rng.choice(["SFO", "JFK", "ORD", "LHR"]),
        "start_date": "2026-10-10",
        "end_date": rng.choice(["2026-10-13", "2026-10-14", "2026-10-15"]),
        "travelers": rng.choice([1, 2, 2, 3, 4]),
        "budget_total": rng.choice([2500, 4000, 6000]),
        "nuance": rng.choice([
            "love food markets and sunset viewpoints",
            "museums and history, relaxed pace",
            "nightlife and live music",
            "nature walks, no early mornings",
        ]),
    }


# --------------------------------------------------------------------------
# Worker process
# --------------------------------------------------------------------------
async def _await_start(args: argparse.Namespace) -> float:
    """Signal ready, then wait for the parent's start time (t0).

    The parent writes t0 only once every worker has imported ADK and opened its
    pools, so no worker's users start late when there are many workers.
    """
    if not args.start_file:
        return args.t0
    Path(args.out + ".ready").write_text("ready")
    start = Path(args.start_file)
    deadline = time.time() + args.ready_timeout
    while not start.exists():
        if time.time() > deadline:
            raise RuntimeError("no start signal from the parent")
        await asyncio.sleep(0.1)
    return float(json.loads(start.read_text())["t0"])


async def _lag_monitor(measure_from: float, stop_at: float, lags: list[float]) -> None:
    """How late this event loop wakes a 100 ms timer: the client's own queueing.

    Every await in every session waits behind the same backlog, so a late loop
    inflates end-to-end latency on any store. Sampled in the measured window.
    """
    tick = 0.1
    await asyncio.sleep(max(0.0, measure_from - time.time()))
    while time.time() < stop_at:
        before = time.perf_counter()
        await asyncio.sleep(tick)
        lags.append(round((time.perf_counter() - before - tick) * 1000.0, 2))


async def _worker(args: argparse.Namespace) -> dict[str, Any]:
    from app.agents.orchestrator import run_swarm
    from app.config import settings
    from app.llm.latency import CURRENT_LLM_LATENCY, LatencyModel
    from app.state.registry import backends

    await backends.startup()
    model = LatencyModel.default(scale=args.llm_scale)

    t0 = await _await_start(args)
    measure_from = t0 + args.warmup
    stop_at = measure_from + args.measure
    sessions: list[dict[str, Any]] = []
    lags: list[float] = []

    async def user(uid: int) -> None:
        rng = random.Random(f"ramp:{uid}")
        await asyncio.sleep(max(0.0, t0 - time.time()) + rng.uniform(0, args.ramp))
        i = 0
        while time.time() < stop_at:
            run_id = f"lt-{args.arm}-{uid}-{i}-{os.getpid()}"
            CURRENT_LLM_LATENCY.set((model, f"llm:{uid}:{i}"))
            started = time.time()
            rec: dict[str, Any] = {"uid": uid, "i": i, "start": started - t0,
                                   "measured": started >= measure_from}
            try:
                s = await asyncio.wait_for(
                    run_swarm(brief_for(uid, i), args.arm, llm_mode="fake",
                              writes_per_step=args.writes,
                              provider_latency_ms=args.provider_ms,
                              run_id=run_id),
                    timeout=args.timeout,
                )
                rec.update(e2e_ms=s["e2e_latency_ms"], ok=s["errors"] == 0,
                           state_errors=s["errors"], io_busy_ms=s.get("io_busy_ms"),
                           round_trips=s.get("round_trips_total"))
            except asyncio.TimeoutError:
                rec.update(e2e_ms=None, ok=False, error="timeout")
            except Exception as exc:  # noqa: BLE001 - failures are data
                rec.update(e2e_ms=(time.time() - started) * 1000.0, ok=False,
                           error=type(exc).__name__ + ": " + str(exc)[:200])
            sessions.append(rec)

            # Session over -> expire it, outside the timed window.
            try:
                await backends.scratchpad(args.arm).delete(run_id)
                await backends.session_service(args.arm).delete_session(
                    app_name=settings.app_name, user_id="demo-user", session_id=run_id)
            except Exception:  # noqa: BLE001
                pass
            i += 1

    ru0 = resource.getrusage(resource.RUSAGE_SELF)
    wall0 = time.time()
    monitor = asyncio.create_task(_lag_monitor(measure_from, stop_at, lags))
    await asyncio.gather(*(user(u) for u in range(args.uid_start, args.uid_end)))
    await monitor
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    wall = time.time() - wall0
    evicted = await backends.evicted_keys(args.arm)
    await backends.shutdown()

    cpu_s = (ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)
    return {"sessions": sessions, "cpu_s": cpu_s, "wall_s": wall,
            "evicted_keys": evicted, "latency_model": model.describe(),
            "loop_lag_ms": lags}


def worker_main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--uid-start", type=int, required=True)
    ap.add_argument("--uid-end", type=int, required=True)
    ap.add_argument("--t0", type=float, default=0.0,
                    help="fixed start time; ignored when --start-file is given")
    ap.add_argument("--start-file", default="",
                    help="barrier: write <out>.ready, then wait for this file's t0")
    ap.add_argument("--ready-timeout", type=float, default=300.0)
    ap.add_argument("--warmup", type=float, required=True)
    ap.add_argument("--measure", type=float, required=True)
    ap.add_argument("--ramp", type=float, required=True)
    ap.add_argument("--writes", type=int, default=1)
    ap.add_argument("--provider-ms", type=int, default=40)
    ap.add_argument("--llm-scale", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    import logging
    import warnings
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)

    result = asyncio.run(_worker(args))
    Path(args.out).write_text(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker_main(sys.argv[2:])
    else:
        print("use scripts/loadtest.py to drive a load test", file=sys.stderr)
        sys.exit(2)
