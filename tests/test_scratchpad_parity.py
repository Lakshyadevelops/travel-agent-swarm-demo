"""Scratchpad parity, TTL semantics, and concurrency safety."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.state.postgres_scratchpad import PostgresScratchpad
from app.state.registry import backends
from tests.conftest import ARMS


@pytest.mark.parametrize("arm", ARMS)
async def test_write_read_roundtrip(arm):
    pad = backends.scratchpad(arm)
    run = f"r-{uuid.uuid4().hex[:8]}"

    await pad.write(run, "brief", {"dest": "Lisbon", "n": 2})
    assert await pad.read(run, "brief") == {"dest": "Lisbon", "n": 2}

    await pad.write_many(run, {"a": [1, 2, 3], "b": {"nested": True}})
    allv = await pad.read_all(run)
    assert allv["a"] == [1, 2, 3]
    assert allv["b"] == {"nested": True}
    assert set(allv) == {"brief", "a", "b"}

    await pad.delete(run)
    assert await pad.read_all(run) == {}


@pytest.mark.parametrize("arm", ARMS)
async def test_missing_key_returns_none(arm):
    pad = backends.scratchpad(arm)
    assert await pad.read(f"nope-{uuid.uuid4().hex}", "absent") is None


@pytest.mark.parametrize("arm", ARMS)
async def test_write_many_empty_is_noop(arm):
    pad = backends.scratchpad(arm)
    run = f"r-{uuid.uuid4().hex[:8]}"
    await pad.write_many(run, {})
    assert await pad.read_all(run) == {}


async def test_arms_produce_identical_state():
    """Same writes must yield identical blackboards on every arm."""
    run = f"r-{uuid.uuid4().hex[:8]}"
    payload = {"x": [1, 2], "y": {"deep": {"er": "value"}}, "z": None}

    snapshots = {}
    for arm in ARMS:
        pad = backends.scratchpad(arm)
        key = f"{run}-{arm}"
        await pad.write_many(key, payload)
        snapshots[arm] = await pad.read_all(key)
        await pad.delete(key)

    first = snapshots[ARMS[0]]
    for arm, snap in snapshots.items():
        assert snap == first, f"{arm} diverged: {snap} != {first}"
    assert first == payload


async def test_postgres_ttl_expiry():
    """Postgres must honour TTL the way Valkey's EXPIRE does."""
    pool = backends._pg_pool  # noqa: SLF001 - deliberate: TTL needs a short override
    pad = PostgresScratchpad(pool, ttl_seconds=1, semaphore=backends._sem_pg)  # noqa: SLF001
    run = f"r-{uuid.uuid4().hex[:8]}"

    await pad.write(run, "ephemeral", {"v": 1})
    assert await pad.read(run, "ephemeral") == {"v": 1}

    await asyncio.sleep(1.4)
    assert await pad.read(run, "ephemeral") is None, "expired row still readable"
    assert await pad.read_all(run) == {}

    await pad.sweep_expired()


@pytest.mark.parametrize("arm", ARMS)
async def test_concurrent_write_storm(arm):
    """50 concurrent writes must all land without exhausting the pool."""
    pad = backends.scratchpad(arm)
    run = f"r-{uuid.uuid4().hex[:8]}"

    await asyncio.gather(*(pad.write(run, f"f{i}", {"i": i}) for i in range(50)))

    allv = await pad.read_all(run)
    assert len(allv) == 50, f"{arm} lost writes: {len(allv)}/50"
    assert all(allv[f"f{i}"] == {"i": i} for i in range(50))

    await pad.delete(run)
