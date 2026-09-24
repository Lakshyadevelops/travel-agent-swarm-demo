"""Statistics for defending a result -- including a null result.

The likely honest finding here is "no user-visible difference at low
concurrency". A null is harder to support than a win: you cannot just report two
medians and shrug. So every comparison carries a confidence interval, every
percentile carries its n, and the verdict text is generated from the numbers
rather than written by hand.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; stable on small samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(pct / 100.0 * len(ordered) + 0.5))))
    return ordered[rank - 1]


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


@dataclass
class DeltaResult:
    baseline: str
    variant: str
    baseline_median: float
    variant_median: float
    delta: float          # variant - baseline; negative means the variant is faster
    ci_low: float
    ci_high: float
    n: int
    significant: bool     # CI excludes zero
    verdict: str


def bootstrap_delta_ci(
    baseline: list[float],
    variant: list[float],
    *,
    resamples: int = 10_000,
    paired: bool = True,
    seed: int = 0,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Percentile bootstrap CI on median(variant) - median(baseline).

    Paired resampling is the default because the benchmark interleaves arms:
    iteration i of every arm runs under the same machine conditions, so pairing
    cancels common-mode drift (thermal, page cache, noisy neighbours) and gives a
    much tighter interval for the same sample size.
    """
    if not baseline or not variant:
        return 0.0, 0.0, 0.0

    rng = random.Random(seed)
    observed = median(variant) - median(baseline)

    deltas: list[float] = []
    if paired and len(baseline) == len(variant):
        n = len(baseline)
        idx = range(n)
        for _ in range(resamples):
            pick = [rng.choice(idx) for _ in range(n)]
            deltas.append(
                median([variant[i] for i in pick]) - median([baseline[i] for i in pick])
            )
    else:
        for _ in range(resamples):
            b = [rng.choice(baseline) for _ in baseline]
            v = [rng.choice(variant) for _ in variant]
            deltas.append(median(v) - median(b))

    alpha = (1.0 - confidence) / 2.0
    deltas.sort()
    lo = deltas[int(alpha * len(deltas))]
    hi = deltas[min(len(deltas) - 1, int((1 - alpha) * len(deltas)))]
    return observed, lo, hi


def compare(
    baseline_label: str,
    baseline: list[float],
    variant_label: str,
    variant: list[float],
    *,
    resamples: int = 10_000,
    seed: int = 0,
) -> DeltaResult:
    delta, lo, hi = bootstrap_delta_ci(
        baseline, variant, resamples=resamples, seed=seed
    )
    significant = not (lo <= 0.0 <= hi)
    n = min(len(baseline), len(variant))

    if not significant:
        verdict = (
            f"{baseline_label} -> {variant_label}: median delta = {delta:+.1f}ms "
            f"(95% CI [{lo:+.1f}, {hi:+.1f}], n={n}). "
            "CI spans zero, so no detectable user-facing difference at this workload."
        )
    else:
        direction = "faster" if delta < 0 else "slower"
        verdict = (
            f"{baseline_label} -> {variant_label}: median delta = {delta:+.1f}ms "
            f"(95% CI [{lo:+.1f}, {hi:+.1f}], n={n}). "
            f"{variant_label} is reliably {direction}."
        )

    return DeltaResult(
        baseline=baseline_label,
        variant=variant_label,
        baseline_median=round(median(baseline), 3),
        variant_median=round(median(variant), 3),
        delta=round(delta, 3),
        ci_low=round(lo, 3),
        ci_high=round(hi, 3),
        n=n,
        significant=significant,
        verdict=verdict,
    )


def describe(values: list[float]) -> dict:
    """Summary with n attached, so no percentile is ever quoted context-free."""
    return {
        "n": len(values),
        "mean_ms": round(statistics.fmean(values), 3) if values else 0.0,
        "p50_ms": round(percentile(values, 50), 3),
        "p95_ms": round(percentile(values, 95), 3),
        "p99_ms": round(percentile(values, 99), 3),
        "min_ms": round(min(values), 3) if values else 0.0,
        "max_ms": round(max(values), 3) if values else 0.0,
    }
