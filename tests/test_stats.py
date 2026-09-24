"""Validates the bootstrap CI against known distributions.

A confidence interval that doesn't actually cover the true value at the stated
rate is worse than no interval at all -- it launders noise as rigour. This checks
empirical coverage rather than trusting the implementation by inspection.
"""

from __future__ import annotations

import random

from app.bench.stats import bootstrap_delta_ci, compare, describe, percentile


def test_percentile_small_samples():
    assert percentile([], 50) == 0.0
    assert percentile([5.0], 99) == 5.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0


def test_describe_attaches_n():
    d = describe([1.0, 2.0, 3.0])
    assert d["n"] == 3
    assert d["p50_ms"] == 2.0


def test_zero_delta_ci_spans_zero():
    """Identical distributions must produce a null verdict, not a fake winner."""
    rng = random.Random(7)
    a = [rng.gauss(100, 10) for _ in range(30)]
    b = [rng.gauss(100, 10) for _ in range(30)]

    result = compare("A", a, "B", b)
    assert result.ci_low <= 0 <= result.ci_high
    assert not result.significant
    assert "no detectable" in result.verdict


def test_large_delta_is_detected():
    rng = random.Random(7)
    a = [rng.gauss(100, 5) for _ in range(30)]
    b = [rng.gauss(50, 5) for _ in range(30)]

    result = compare("slow", a, "fast", b)
    assert result.significant
    assert result.delta < 0
    assert "faster" in result.verdict


def test_ci_covers_true_delta_at_stated_rate():
    """Empirical coverage of the 95% interval should be near 95%."""
    true_delta = -20.0
    covered = 0
    trials = 200

    for trial in range(trials):
        rng = random.Random(trial)
        a = [rng.gauss(100, 10) for _ in range(30)]
        b = [x + true_delta for x in (rng.gauss(100, 10) for _ in range(30))]
        _, lo, hi = bootstrap_delta_ci(a, b, resamples=500, seed=trial, paired=False)
        if lo <= true_delta <= hi:
            covered += 1

    coverage = covered / trials
    assert 0.85 <= coverage <= 1.0, f"CI coverage {coverage:.2%} is not near 95%"


def test_empty_inputs_do_not_crash():
    assert bootstrap_delta_ci([], []) == (0.0, 0.0, 0.0)
    r = compare("A", [], "B", [])
    assert r.n == 0
