"""ADK BaseSessionService backed directly by PostgreSQL via asyncpg.

ROUND-TRIP DISCIPLINE: `append_event` is a single data-modifying CTE and
`get_session` is a single CTE returning session row + filtered events together.
The earlier BEGIN / SET LOCAL / INSERT / UPDATE / COMMIT shape was five round
trips against Valkey's one, which would have dominated the comparison with our
own inefficiency rather than any property of Postgres.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import asyncpg
import orjson
from google.adk.events.event import Event
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import GetSessionConfig, ListSessionsResponse

from app.telemetry.instrument import timed

_CREATE_SQL = """
INSERT INTO adk_sessions (app_name, user_id, session_id, state, last_update_time)
VALUES ($1, $2, $3, $4::jsonb, $5)
ON CONFLICT (app_name, user_id, session_id)
DO UPDATE SET state = EXCLUDED.state, last_update_time = EXCLUDED.last_update_time;
"""

# One round trip: session row and its (already filtered, already ordered) events.
_GET_SQL = """
WITH s AS (
  SELECT state, last_update_time
    FROM adk_sessions
   WHERE app_name = $1 AND user_id = $2 AND session_id = $3
), e AS (
  SELECT coalesce(jsonb_agg(payload ORDER BY ts), '[]'::jsonb) AS events
    FROM (
      SELECT payload, ts
        FROM adk_events
       WHERE app_name = $1 AND user_id = $2 AND session_id = $3
         AND ($5::double precision IS NULL OR ts >= $5)
       ORDER BY ts DESC
       LIMIT $4
    ) sub
)
SELECT s.state, s.last_update_time, e.events FROM s, e;
"""

# One round trip: insert the event and merge the new state in a single statement.
# A single statement is already atomic, so BEGIN/COMMIT would add nothing but latency.
_APPEND_SQL = """
WITH ev AS (
  INSERT INTO adk_events (app_name, user_id, session_id, event_id, ts, author, payload)
  VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
  ON CONFLICT DO NOTHING
)
UPDATE adk_sessions
   SET state = $8::jsonb,
       last_update_time = $5
 WHERE app_name = $1 AND user_id = $2 AND session_id = $3;
"""

_LIST_SQL = """
SELECT session_id, last_update_time FROM adk_sessions
 WHERE app_name = $1 AND ($2::text IS NULL OR user_id = $2)
 ORDER BY session_id;
"""

_DELETE_SQL = """
WITH d AS (
  DELETE FROM adk_events
   WHERE app_name = $1 AND user_id = $2 AND session_id = $3
)
DELETE FROM adk_sessions
 WHERE app_name = $1 AND user_id = $2 AND session_id = $3;
"""


class PostgresSessionService(BaseSessionService):
    backend = "postgres"

    # What each logical op physically does. Shown next to each step in the
    # Under the Hood tab.
    PRIMITIVES = {
        "session_create": "INSERT INTO adk_sessions (app_name, user_id, session_id, state, "
                          "last_update_time) VALUES (...) ON CONFLICT DO UPDATE (1 statement)",
        "session_get": "WITH s AS (SELECT state FROM adk_sessions ...), e AS (SELECT "
                       "jsonb_agg(payload ORDER BY ts) FROM adk_events ...) SELECT ... "
                       "(1 statement)",
        "session_append": "WITH ev AS (INSERT INTO adk_events ... payload::jsonb) "
                          "UPDATE adk_sessions SET state = $8::jsonb (1 statement)",
        "session_list": "SELECT session_id FROM adk_sessions WHERE app_name = $1",
        "session_delete": "WITH d AS (DELETE FROM adk_events ...) DELETE FROM adk_sessions ...",
    }

    def __init__(
        self,
        pool: asyncpg.Pool,
        semaphore,
        backend_label: str = "postgres",
    ) -> None:
        self._pool = pool
        self._sem = semaphore
        self.backend = backend_label

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        sid = session_id or str(uuid.uuid4())
        now = time.time()
        state = state or {}
        blob = orjson.dumps(state).decode()

        async with timed(
            "session_create", self.backend, f"sess:{app_name}:{user_id}:{sid}"
        ) as box:
            box.payload_bytes = len(blob)
            box.value = state
            async with self._sem:
                await self._pool.execute(_CREATE_SQL, app_name, user_id, sid, blob, now)

        return Session(
            id=sid,
            app_name=app_name,
            user_id=user_id,
            state=state,
            events=[],
            last_update_time=now,
        )

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        limit = (
            config.num_recent_events
            if config and config.num_recent_events is not None
            else None
        )
        after = config.after_timestamp if config else None
        # asyncpg needs a concrete LIMIT; ALL is expressed as a large sentinel.
        sql_limit = limit if limit is not None else 2**31 - 1

        async with timed(
            "session_get", self.backend, f"sess:{app_name}:{user_id}:{session_id}"
        ) as box:
            async with self._sem:
                row = await self._pool.fetchrow(
                    _GET_SQL, app_name, user_id, session_id, sql_limit, after
                )
            if row is None:
                return None
            box.payload_bytes = len(row["events"])
            # Lazy: evaluated only by the UI listener, after timing has stopped,
            # so the measured region stays exactly as before.
            box.value = lambda: {"events_loaded": len(orjson.loads(row["events"]))}

        events_json = orjson.loads(row["events"])
        return Session(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            state=orjson.loads(row["state"]),
            events=[Event.model_validate(e) for e in events_json],
            last_update_time=row["last_update_time"],
        )

    async def list_sessions(
        self, *, app_name: str, user_id: Optional[str] = None
    ) -> ListSessionsResponse:
        async with timed("session_list", self.backend, f"sessidx:{app_name}"):
            async with self._sem:
                rows = await self._pool.fetch(_LIST_SQL, app_name, user_id)

        return ListSessionsResponse(
            sessions=[
                Session(
                    id=r["session_id"],
                    app_name=app_name,
                    user_id=user_id or "",
                    state={},
                    events=[],
                    last_update_time=0.0,
                )
                for r in rows
            ]
        )

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        async with timed(
            "session_delete", self.backend, f"sess:{app_name}:{user_id}:{session_id}"
        ):
            async with self._sem:
                await self._pool.execute(_DELETE_SQL, app_name, user_id, session_id)

    async def append_event(self, session: Session, event: Event) -> Event:
        # ADK owns the state_delta merge; we persist the result.
        await super().append_event(session=session, event=event)

        payload = orjson.dumps(event.model_dump(mode="json", exclude_none=True)).decode()
        state_blob = orjson.dumps(session.state).decode()

        async with timed(
            "session_append",
            self.backend,
            f"sess:{session.app_name}:{session.user_id}:{session.id}",
        ) as box:
            box.payload_bytes = len(payload) + len(state_blob)
            box.round_trips = 1  # single CTE, not BEGIN/INSERT/UPDATE/COMMIT
            box.value = event
            async with self._sem:
                await self._pool.execute(
                    _APPEND_SQL,
                    session.app_name,
                    session.user_id,
                    session.id,
                    event.id,
                    event.timestamp,
                    event.author,
                    payload,
                    state_blob,
                )

        return event

    async def close(self) -> None:
        await self._pool.close()
