"""ADK BaseSessionService backed directly by Valkey.

Key layout:
  sess:{app}:{user}:{sid}   HASH  -> state JSON + last_update_time
  evt:{app}:{user}:{sid}    ZSET  -> score = event.timestamp, member = event JSON
  sessidx:{app}:{user}      SET   -> session ids, backs list_sessions

The ZSET is what lets GetSessionConfig filtering happen datastore-side
(ZRANGEBYSCORE for after_timestamp, ZREVRANGE+LIMIT for num_recent_events)
rather than fetching everything and filtering in Python.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import orjson
import redis.asyncio as redis
from google.adk.events.event import Event
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import GetSessionConfig, ListSessionsResponse

from app.telemetry.instrument import timed


class ValkeySessionService(BaseSessionService):
    backend = "valkey"

    def __init__(
        self,
        client: redis.Redis,
        semaphore,
        backend_label: str = "valkey",
    ) -> None:
        self._client = client
        self._sem = semaphore
        self.backend = backend_label

    # ---- key helpers -------------------------------------------------
    @staticmethod
    def _sess_key(app: str, user: str, sid: str) -> str:
        return f"sess:{app}:{user}:{sid}"

    @staticmethod
    def _evt_key(app: str, user: str, sid: str) -> str:
        return f"evt:{app}:{user}:{sid}"

    @staticmethod
    def _idx_key(app: str, user: str) -> str:
        return f"sessidx:{app}:{user}"

    # ---- BaseSessionService -----------------------------------------
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
        blob = orjson.dumps(state)
        key = self._sess_key(app_name, user_id, sid)

        async with timed("session_create", self.backend, key) as box:
            box.payload_bytes = len(blob)
            async with self._sem:
                pipe = self._client.pipeline(transaction=False)
                pipe.hset(key, mapping={"state": blob, "last_update_time": str(now)})
                pipe.sadd(self._idx_key(app_name, user_id), sid)
                await pipe.execute()

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
        skey = self._sess_key(app_name, user_id, session_id)
        ekey = self._evt_key(app_name, user_id, session_id)

        async with timed("session_get", self.backend, skey) as box:
            async with self._sem:
                pipe = self._client.pipeline(transaction=False)
                pipe.hgetall(skey)

                # Push the filtering into Valkey rather than into Python.
                if config and config.after_timestamp is not None:
                    pipe.zrangebyscore(ekey, config.after_timestamp, "+inf")
                elif config and config.num_recent_events is not None:
                    if config.num_recent_events == 0:
                        pipe.zrange(ekey, 0, -1)  # placeholder, discarded below
                    else:
                        pipe.zrange(ekey, -config.num_recent_events, -1)
                else:
                    pipe.zrange(ekey, 0, -1)

                meta, raw_events = await pipe.execute()

            if not meta:
                return None
            box.payload_bytes = sum(len(e) for e in raw_events)

        if config and config.num_recent_events == 0:
            raw_events = []
        elif config and config.after_timestamp is not None and config.num_recent_events:
            raw_events = raw_events[-config.num_recent_events :]

        state_raw = meta.get(b"state") or meta.get("state") or b"{}"
        lut_raw = meta.get(b"last_update_time") or meta.get("last_update_time") or b"0"

        return Session(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            state=orjson.loads(state_raw),
            events=[Event.model_validate(orjson.loads(e)) for e in raw_events],
            last_update_time=float(lut_raw),
        )

    async def list_sessions(
        self, *, app_name: str, user_id: Optional[str] = None
    ) -> ListSessionsResponse:
        key = self._idx_key(app_name, user_id or "")
        async with timed("session_list", self.backend, key):
            async with self._sem:
                sids = await self._client.smembers(key)

        return ListSessionsResponse(
            sessions=[
                Session(
                    id=(s.decode() if isinstance(s, bytes) else s),
                    app_name=app_name,
                    user_id=user_id or "",
                    state={},
                    events=[],
                    last_update_time=0.0,
                )
                for s in sorted(sids)
            ]
        )

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        skey = self._sess_key(app_name, user_id, session_id)
        async with timed("session_delete", self.backend, skey):
            async with self._sem:
                pipe = self._client.pipeline(transaction=False)
                pipe.delete(skey)
                pipe.delete(self._evt_key(app_name, user_id, session_id))
                pipe.srem(self._idx_key(app_name, user_id), session_id)
                await pipe.execute()

    async def append_event(self, session: Session, event: Event) -> Event:
        # Delegate to ADK for canonical state_delta merge semantics, then persist
        # the merged result. Reimplementing the merge would risk drifting from
        # ADK's behaviour and silently breaking the parity test against
        # InMemorySessionService.
        await super().append_event(session=session, event=event)

        skey = self._sess_key(session.app_name, session.user_id, session.id)
        ekey = self._evt_key(session.app_name, session.user_id, session.id)
        payload = orjson.dumps(event.model_dump(mode="json", exclude_none=True))
        state_blob = orjson.dumps(session.state)

        async with timed("session_append", self.backend, skey) as box:
            box.payload_bytes = len(payload) + len(state_blob)
            async with self._sem:
                pipe = self._client.pipeline(transaction=False)
                pipe.zadd(ekey, {payload: event.timestamp})
                pipe.hset(
                    skey,
                    mapping={
                        "state": state_blob,
                        "last_update_time": str(session.last_update_time),
                    },
                )
                await pipe.execute()

        return event

    async def close(self) -> None:
        await self._client.aclose()
