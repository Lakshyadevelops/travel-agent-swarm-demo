"""Guards the overhead metric against the double-counting bug.

Summing op durations across concurrently-running agents can exceed wall clock,
which made the old ">10% overhead" badge fire permanently and meaninglessly.
Overhead is now a union of busy intervals and must be bounded by 100%.
"""

from __future__ import annotations

from app.telemetry.metrics import RunTelemetry, StateOp, interval_union_ms


def op(start: float, dur_ms: float, agent: str = "a") -> StateOp:
    return StateOp(
        op_type="scratch_write", agent_role=agent, backend="test",
        duration_ms=dur_ms, key="k", payload_bytes=0, started_at=start,
    )


def test_disjoint_intervals_sum():
    ops = [op(0.0, 10.0), op(1.0, 10.0)]  # 0-10ms and 1000-1010ms
    assert abs(interval_union_ms(ops) - 20.0) < 1e-6


def test_fully_overlapping_intervals_count_once():
    """Three agents waiting on the same 10ms is 10ms of waiting, not 30ms."""
    ops = [op(0.0, 10.0), op(0.0, 10.0), op(0.0, 10.0)]
    assert interval_union_ms(ops) == 10.0


def test_partially_overlapping_intervals_merge():
    ops = [op(0.0, 10.0), op(0.005, 10.0)]  # 0-10ms and 5-15ms -> 0-15ms
    assert abs(interval_union_ms(ops) - 15.0) < 1e-6


def test_nested_interval_absorbed():
    ops = [op(0.0, 100.0), op(0.010, 10.0)]
    assert abs(interval_union_ms(ops) - 100.0) < 1e-6


def test_empty():
    assert interval_union_ms([]) == 0.0


def test_overhead_cannot_exceed_100_percent():
    """The regression this metric exists to prevent."""
    tel = RunTelemetry("r", "test")
    # Three concurrent agents, each blocked on the same 50ms window.
    for agent in ("scout", "transit", "stay"):
        tel.record(op(0.0, 50.0, agent))
    tel.wall_clock_ms = 100.0

    summary = tel.summary()
    # Naive summing would report 150ms of I/O inside a 100ms run -> 150%.
    assert summary["io_busy_ms"] == 50.0
    assert summary["io_overhead_pct"] == 50.0
    assert summary["io_overhead_pct"] <= 100.0
    assert summary["io_overhead_is_high"] is True


def test_errored_ops_excluded_from_percentiles_but_counted():
    tel = RunTelemetry("r", "test")
    good = op(0.0, 5.0)
    bad = op(0.1, 9999.0)
    bad.error = "TimeoutError: boom"
    tel.record(good)
    tel.record(bad)
    tel.wall_clock_ms = 100.0

    s = tel.summary()
    assert s["ops_total"] == 2
    assert s["errors"] == 1
    assert s["error_rate_pct"] == 50.0
    # The timeout's duration reflects the timeout setting, not backend speed.
    assert s["op_latency"]["n"] == 1
    assert s["op_latency"]["p50_ms"] == 5.0
