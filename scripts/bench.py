"""Single-session benchmarks from the shell. The UI no longer runs benchmarks.

A thin wrapper over app.bench (unchanged):
  bench   interleaved A/B/A/B runs, warm-ups discarded, bootstrap 95% CI on the
          median end-to-end delta against a baseline arm
  sweep   the same, at several scratchpad writes per agent step (1/10/100)
  probe   live Gemini latency, to put storage deltas in context (API calls)

bench and sweep use the scripted model: no API calls, deterministic op counts.

Examples
  .venv/bin/python scripts/bench.py bench --arms valkey,postgres --repeats 30 --warmups 3 --writes 10
  .venv/bin/python scripts/bench.py sweep --frequencies 1,10,100
  .venv/bin/python scripts/bench.py probe --samples 30

Output: tables on stdout, and results.json next to the raw op log (ops.jsonl)
in runs/<bench_id>/. A sweep also writes runs/sweep-<id>.json.
For concurrent-user load tests see scripts/load_campaign.sh.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.bench.runner import DEFAULT_REPEATS, DEFAULT_WARMUPS, run_benchmark  # noqa: E402
from app.bench.sweeps import WRITE_FREQUENCIES, write_frequency_sweep  # noqa: E402
from app.llm.gemini_probe import probe_gemini_latency  # noqa: E402
from app.state.registry import BACKEND_SPECS, backends  # noqa: E402

# Same trip as the test suite; within budget, so one budget round per run.
BRIEF = {
    "destination": "Lisbon",
    "origin": "SFO",
    "start_date": "2026-10-10",
    "end_date": "2026-10-14",
    "travelers": 2,
    "budget_total": 4000,
    "nuance": "food markets",
}


def _arms(text: str) -> list[str]:
    arms = [a.strip() for a in text.split(",") if a.strip()]
    unknown = [a for a in arms if a not in BACKEND_SPECS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown arm(s) {unknown}; choose from {sorted(BACKEND_SPECS)}"
        )
    return arms


def _ints(text: str) -> tuple[int, ...]:
    try:
        values = tuple(int(v) for v in text.split(",") if v.strip())
    except ValueError:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from None
    if not values or min(values) < 1:
        raise argparse.ArgumentTypeError("values must be positive integers")
    return values


def _progress(done: int, total: int, arm: str, iteration: int) -> None:
    phase = "warm-up" if iteration < 0 else f"iter {iteration + 1}"
    print(f"\r  {done}/{total}  {arm:<18} {phase:<10}", end="", file=sys.stderr, flush=True)
    if done == total:
        print(file=sys.stderr)


def _print_bench(result: dict[str, Any]) -> None:
    m = result["manifest"]
    print(f"\n{result['bench_id']}: {m['writes_per_step']} write(s)/step, "
          f"{m['repeats']} repeats + {m['warmups_discarded']} warm-ups discarded, "
          f"{m['ordering']}, model={m['llm_mode']}")
    header = (f"{'arm':<18} {'n':>4} {'e2e p50':>9} {'p95':>9} {'p99':>9} "
              f"{'ops/run':>8} {'round trips':>12} {'errors':>7} {'evicted':>8}")
    print(header)
    print("-" * len(header))
    for arm, a in result["arms"].items():
        e = a["e2e"]
        print(f"{arm:<18} {e['n']:>4} {e['p50_ms']:>7.1f}ms {e['p95_ms']:>7.1f}ms "
              f"{e['p99_ms']:>7.1f}ms {a['ops_per_run']:>8} {a['round_trips_per_run']:>12} "
              f"{a['errors']:>7} {a['evicted_keys']:>8}")
    for c in result["comparisons"]:
        print("  " + c["verdict"])
    if result.get("cache_hit_rate_pct") is not None:
        print(f"  cache hit rate: {result['cache_hit_rate_pct']}%")


def _save(result: dict[str, Any]) -> Path:
    path = Path(result["oplog_path"]).parent / "results.json"
    path.write_text(json.dumps(result, indent=2, default=str))
    return path


async def _with_backends(coro):
    await backends.startup()
    try:
        health = await backends.health()
        down = {k: v for k, v in health.items() if v != "ok"}
        if down:
            raise SystemExit(f"stores unavailable: {down} (run `docker compose up -d`)")
        return await coro
    finally:
        await backends.shutdown()


async def cmd_bench(args: argparse.Namespace) -> None:
    result = await _with_backends(run_benchmark(
        BRIEF, args.arms, repeats=args.repeats, warmups=args.warmups,
        writes_per_step=args.writes, baseline=args.baseline, progress=_progress,
    ))
    _print_bench(result)
    print(f"\nresults: {_save(result)}\nop log:  {result['oplog_path']}")


async def cmd_sweep(args: argparse.Namespace) -> None:
    sweep = await _with_backends(write_frequency_sweep(
        BRIEF, args.arms, repeats=args.repeats, warmups=args.warmups,
        frequencies=args.frequencies, baseline=args.baseline, progress=_progress,
    ))
    runs_dir = REPO / "runs"
    for freq in sweep["frequencies"]:
        cell = sweep["cells"][str(freq)]
        _print_bench(cell)
        _save(cell)
        runs_dir = Path(cell["oplog_path"]).parent.parent

    print("\nMedian end-to-end (ms) by writes per agent step")
    arms = list(sweep["cells"][str(sweep["frequencies"][0])]["arms"])
    print(f"{'writes/step':>12} " + " ".join(f"{a:>18}" for a in arms))
    for freq in sweep["frequencies"]:
        cell = sweep["cells"][str(freq)]["arms"]
        print(f"{freq:>12} " + " ".join(
            f"{cell[a]['e2e']['p50_ms']:>11.1f} (n={cell[a]['e2e']['n']})" for a in arms))

    path = runs_dir / f"sweep-{uuid.uuid4().hex[:8]}.json"
    path.write_text(json.dumps(sweep, indent=2, default=str))
    print(f"\nsweep results: {path}")


async def cmd_probe(args: argparse.Namespace) -> None:
    r = await probe_gemini_latency(samples=args.samples, concurrency=args.concurrency)
    if not r.get("available"):
        raise SystemExit(f"probe unavailable: {r.get('reason')}")
    print(f"Gemini {r['model']}: p50 {r['p50_ms']} ms, p95 {r['p95_ms']} ms, "
          f"p99 {r['p99_ms']} ms (n={r['n']})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--arms", type=_arms, default=_arms("valkey,postgres"),
                       help="comma-separated arms (default: valkey,postgres)")
        p.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
        p.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
        p.add_argument("--baseline", default="postgres")

    b = sub.add_parser("bench", help="interleaved A/B benchmark")
    common(b)
    b.add_argument("--writes", type=int, default=1, help="scratchpad writes per agent step")
    b.set_defaults(func=cmd_bench)

    s = sub.add_parser("sweep", help="write-frequency sweep")
    common(s)
    s.add_argument("--frequencies", type=_ints, default=WRITE_FREQUENCIES,
                   help="comma-separated writes per step (default: 1,10,100)")
    s.set_defaults(func=cmd_sweep)

    p = sub.add_parser("probe", help="live Gemini latency (makes API calls)")
    p.add_argument("--samples", type=int, default=30)
    p.add_argument("--concurrency", type=int, default=4)
    p.set_defaults(func=cmd_probe)

    args = parser.parse_args()
    for name in ("repeats", "samples", "concurrency"):
        if getattr(args, name, 1) < 1:
            parser.error(f"--{name} must be at least 1")
    if getattr(args, "warmups", 0) < 0:
        parser.error("--warmups must be 0 or more")
    if args.cmd == "bench" and args.writes < 1:
        parser.error("--writes must be at least 1")
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
