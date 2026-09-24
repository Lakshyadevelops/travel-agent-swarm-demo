"""Cache-aside correctness for the postgres_cached arm.

A cache that serves stale or missing data would make the arm look fast while
being wrong, which is the most dangerous possible benchmark outcome.
"""

from __future__ import annotations

import uuid

from app.state.registry import backends
from app.telemetry.instrument import CURRENT_RUN
from app.telemetry.metrics import RunTelemetry


async def test_write_is_durable_in_origin_not_just_cache():
    """The cache must never be the only copy of a committed write."""
    run = f"r-{uuid.uuid4().hex[:8]}"
    cached = backends.scratchpad("postgres_cached")
    await cached.write(run, "durable", {"v": 42})

    # Read through the plain Postgres arm: bypasses the cache entirely.
    plain = backends.scratchpad("postgres")
    assert await plain.read(run, "durable") == {"v": 42}

    await cached.delete(run)


async def test_miss_then_hit_accounting():
    run = f"r-{uuid.uuid4().hex[:8]}"

    # Seed the origin directly so the cache is guaranteed cold.
    plain = backends.scratchpad("postgres")
    await plain.write(run, "seeded", {"from": "origin"})

    tel = RunTelemetry(run, "postgres_cached")
    CURRENT_RUN.set(tel)
    cached = backends.scratchpad("postgres_cached")

    first = await cached.read(run, "seeded")   # miss -> populates
    second = await cached.read(run, "seeded")  # hit

    CURRENT_RUN.set(None)

    assert first == {"from": "origin"}
    assert second == {"from": "origin"}

    reads = [o for o in tel.ops if o.op_type == "scratch_read"]
    assert len(reads) == 2
    assert reads[0].cache_hit is False, "first read should miss"
    assert reads[1].cache_hit is True, "second read should hit"

    summary = tel.summary()
    assert summary["cache"]["hit_rate_pct"] == 50.0
    assert summary["cache"]["n"] == 2

    await cached.delete(run)


async def test_write_refreshes_cache_no_stale_read():
    run = f"r-{uuid.uuid4().hex[:8]}"
    cached = backends.scratchpad("postgres_cached")

    await cached.write(run, "k", {"version": 1})
    assert await cached.read(run, "k") == {"version": 1}

    await cached.write(run, "k", {"version": 2})
    assert await cached.read(run, "k") == {"version": 2}, "served a stale cached value"

    await cached.delete(run)


async def test_read_all_populates_from_origin():
    run = f"r-{uuid.uuid4().hex[:8]}"
    plain = backends.scratchpad("postgres")
    await plain.write_many(run, {"a": 1, "b": 2, "c": 3})

    cached = backends.scratchpad("postgres_cached")
    first = await cached.read_all(run)   # cold
    second = await cached.read_all(run)  # warm

    assert first == {"a": 1, "b": 2, "c": 3}
    assert second == first

    await cached.delete(run)


async def test_delete_clears_both_tiers():
    run = f"r-{uuid.uuid4().hex[:8]}"
    cached = backends.scratchpad("postgres_cached")
    plain = backends.scratchpad("postgres")

    await cached.write(run, "k", {"v": 1})
    await cached.delete(run)

    assert await cached.read(run, "k") is None
    assert await plain.read(run, "k") is None, "origin row survived delete"
