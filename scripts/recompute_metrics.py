#!/usr/bin/env python3
"""Rebuild every published metric from a raw ops.jsonl.

If someone disputes a number, recompute it instead of re-running the benchmark.
The manifest alongside the op log pins exactly what produced it.

Usage:
    python scripts/recompute_metrics.py runs/<run_id>/ops.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.telemetry.metrics import StateOp, interval_union_ms  # noqa: E402


def load(path: Path) -> list[StateOp]:
    ops: list[StateOp] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                ops.append(StateOp(**json.loads(line)))
    return ops


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"no such op log: {path}")
        return 1

    ops = load(path)
    # Warm-ups are tagged iteration < 0 and excluded here exactly as they are live.
    measured = [o for o in ops if o.iteration >= 0]

    by_variant: dict[str, list[StateOp]] = defaultdict(list)
    for op in measured:
        by_variant[op.variant_id or op.backend].append(op)

    print(f"op log: {path}")
    print(f"total ops: {len(ops)}  measured: {len(measured)}  "
          f"warm-ups discarded: {len(ops) - len(measured)}\n")

    for variant, group in sorted(by_variant.items()):
        ok = [o for o in group if o.error is None]
        errs = [o for o in group if o.error is not None]
        durations = sorted(o.duration_ms for o in ok)

        def pct(p: float) -> float:
            if not durations:
                return 0.0
            rank = max(1, min(len(durations), int(round(p / 100 * len(durations) + 0.5))))
            return durations[rank - 1]

        print(f"  {variant}")
        print(f"    ops={len(group)} errors={len(errs)} "
              f"round_trips={sum(o.round_trips for o in group)}")
        print(f"    op p50={pct(50):.3f}ms p95={pct(95):.3f}ms p99={pct(99):.3f}ms "
              f"(n={len(durations)})")
        print(f"    io_busy(union)={interval_union_ms(ok):.1f}ms")

        cache = [o for o in ok if o.cache_hit is not None]
        if cache:
            hits = sum(1 for o in cache if o.cache_hit)
            print(f"    cache hit rate={hits / len(cache) * 100:.1f}% (n={len(cache)})")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
