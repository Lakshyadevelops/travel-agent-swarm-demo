"""Guards the round-trip discipline.

The original design issued BEGIN / SET LOCAL / INSERT / UPDATE / COMMIT for a
single append -- five round trips against Valkey's one. That ratio, not any
property of Postgres, would have dominated the benchmark. These tests fail the
build if that chattiness ever returns.
"""

from __future__ import annotations

import uuid

import pytest
from google.adk.events.event import Event
from google.genai import types

from app.state.registry import backends
from app.telemetry.instrument import CURRENT_RUN
from app.telemetry.metrics import RunTelemetry


class QueryCounter:
    """Counts statements actually sent to Postgres.

    asyncpg's query logger invokes the callback with a single LoggedQuery record.
    """

    def __init__(self) -> None:
        self.count = 0
        self.queries: list[str] = []

    def __call__(self, record) -> None:
        self.count += 1
        self.queries.append(getattr(record, "query", ""))


async def count_pg_queries(coro_factory) -> int:
    pool = backends._pg_pool  # noqa: SLF001
    counter = QueryCounter()
    async with pool.acquire() as conn:
        conn.add_query_logger(counter)
        try:
            await coro_factory(conn)
        finally:
            conn.remove_query_logger(counter)
    return counter.count


async def test_append_event_is_one_round_trip():
    from app.state.postgres_scratchpad import PostgresScratchpad  # noqa: F401
    from app.state.postgres_session import _APPEND_SQL

    sid = f"s-{uuid.uuid4().hex[:8]}"
    svc = backends.session_service("postgres")
    await svc.create_session(app_name="rt", user_id="u", state={}, session_id=sid)

    event = Event(
        author="tester",
        invocation_id="inv",
        content=types.Content(role="model", parts=[types.Part(text="hi")]),
    )

    async def do(conn):
        await conn.execute(
            _APPEND_SQL, "rt", "u", sid, event.id, event.timestamp,
            event.author, "{}", "{}",
        )

    n = await count_pg_queries(do)
    assert n == 1, f"append_event should be exactly 1 round trip, issued {n}"

    await svc.delete_session(app_name="rt", user_id="u", session_id=sid)


async def test_write_many_is_one_round_trip():
    """The unnest upsert must not degrade back into executemany."""
    from app.state.postgres_scratchpad import _WRITE_MANY_SQL

    run = f"r-{uuid.uuid4().hex[:8]}"
    fields = [f"f{i}" for i in range(25)]
    blobs = ['{"i":%d}' % i for i in range(25)]

    async def do(conn):
        await conn.execute(_WRITE_MANY_SQL, run, fields, blobs, "900")

    n = await count_pg_queries(do)
    assert n == 1, f"write_many of 25 fields should be 1 round trip, issued {n}"


@pytest.mark.parametrize("arm", ["valkey", "postgres"])
async def test_reported_round_trips_match_across_arms(arm):
    """Both arms must self-report the same round trips for the same work."""
    tel = RunTelemetry("rt", arm)
    CURRENT_RUN.set(tel)

    pad = backends.scratchpad(arm)
    run = f"r-{uuid.uuid4().hex[:8]}"
    await pad.write(run, "a", {"v": 1})
    await pad.write_many(run, {"b": 2, "c": 3})
    await pad.read(run, "a")
    await pad.read_all(run)
    await pad.delete(run)

    CURRENT_RUN.set(None)
    per_op = {op.op_type: op.round_trips for op in tel.ops}
    assert per_op["scratch_write"] == 1
    assert per_op["scratch_write_many"] == 1, "write_many regressed to multiple round trips"
    assert per_op["scratch_read"] == 1
    assert per_op["scratch_read_all"] == 1
