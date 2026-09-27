"""Where a run's data lives, and a raw look at it.

Both functions serve the Under the Hood tab:

* `describe_layout()` is sent with the run's `started` event. It names the exact
  keys or tables holding the run and the physical commands behind each logical
  operation, taken from the stores' own key helpers and PRIMITIVES.
* `snapshot()` is a raw, UNINSTRUMENTED read of everything the run left behind.
  It bypasses `timed()`, so inspecting a run never adds operations to any run's
  telemetry. Every SQL statement is parameterized.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import orjson

from app.config import settings
from app.state.cached_scratchpad import CachedScratchpad
from app.state.postgres_scratchpad import PostgresScratchpad
from app.state.postgres_session import PostgresSessionService
from app.state.registry import BACKEND_SPECS, backends
from app.state.valkey_scratchpad import ValkeyScratchpad
from app.state.valkey_session import ValkeySessionService

EVENT_ROW_LIMIT = 500

_PG_SESSION_SQL = (
    "SELECT session_id, state, last_update_time FROM adk_sessions "
    "WHERE app_name = $1 AND user_id = $2 AND session_id = $3"
)
_PG_EVENTS_SQL = (
    "SELECT ts, author, event_id, payload FROM adk_events "
    "WHERE app_name = $1 AND user_id = $2 AND session_id = $3 "
    "ORDER BY ts LIMIT $4"
)
_PG_SCRATCH_SQL = (
    "SELECT field, value, updated_at, expires_at, expires_at <= now() AS expired "
    "FROM scratchpad WHERE run_id = $1 ORDER BY updated_at, field"
)


def describe_layout(backend_id: str, run_id: str) -> dict[str, Any]:
    """Where this run's session and scratchpad live, and what each op does."""
    app, user = settings.app_name, settings.demo_user_id
    ttl = settings.scratchpad_ttl_seconds
    label = BACKEND_SPECS[backend_id].label

    if backend_id == "valkey":
        svc = ValkeySessionService
        return {
            "backend": backend_id,
            "label": label,
            "engine": "valkey",
            "session": {
                "summary": "HASH + ZSET",
                "structures": [
                    {"type": "HASH", "name": svc._sess_key(app, user, run_id),
                     "holds": "state (JSON) and last_update_time"},
                    {"type": "ZSET", "name": svc._evt_key(app, user, run_id),
                     "holds": "one member per event (JSON), score = event timestamp"},
                    {"type": "SET", "name": svc._idx_key(app, user),
                     "holds": "session ids for this user"},
                ],
            },
            "scratchpad": {
                "summary": f"HASH · TTL {ttl} s",
                "ttl_s": ttl,
                "structures": [
                    {"type": "HASH", "name": ValkeyScratchpad._key(run_id),
                     "holds": f"one field per finding (JSON); EXPIRE {ttl} s on every write"},
                ],
            },
            "primitives": {**svc.PRIMITIVES, **ValkeyScratchpad.PRIMITIVES},
        }

    # Postgres family. For postgres_cached the tables are the system of record;
    # the cache tier holds a derived copy and is not part of the UI.
    pad = CachedScratchpad if backend_id == "postgres_cached" else PostgresScratchpad
    return {
        "backend": backend_id,
        "label": label,
        "engine": "postgres",
        "session": {
            "summary": "adk_sessions + adk_events rows",
            "structures": [
                {"type": "TABLE", "name": "adk_sessions", "rows": f"session_id = {run_id}",
                 "holds": "one row per session: state JSONB, last_update_time"},
                {"type": "TABLE", "name": "adk_events", "rows": f"session_id = {run_id}",
                 "holds": "one row per event: payload JSONB, ts, author"},
            ],
        },
        "scratchpad": {
            "summary": f"scratchpad rows · expires_at = now() + {ttl} s",
            "ttl_s": ttl,
            "structures": [
                {"type": "TABLE", "name": "scratchpad", "rows": f"run_id = {run_id}",
                 "holds": "one row per finding: value JSONB, updated_at, expires_at"},
            ],
        },
        "primitives": {**PostgresSessionService.PRIMITIVES, **pad.PRIMITIVES},
    }


def _text(raw: Any) -> str:
    return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)


def _json(raw: Any) -> Any:
    if raw is None:
        return None
    try:
        return orjson.loads(raw)
    except orjson.JSONDecodeError:
        return _text(raw)


def _plain(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


async def snapshot(backend_id: str, run_id: str) -> dict[str, Any] | None:
    """Everything the run left in its store, read raw. None if nothing is there."""
    if backend_id == "valkey":
        return await _valkey_snapshot(run_id)
    return await _postgres_snapshot(backend_id, run_id)


async def _valkey_snapshot(run_id: str) -> dict[str, Any] | None:
    app, user = settings.app_name, settings.demo_user_id
    skey = ValkeySessionService._sess_key(app, user, run_id)
    ekey = ValkeySessionService._evt_key(app, user, run_id)
    xkey = ValkeyScratchpad._key(run_id)

    pipe = backends.raw_valkey().pipeline(transaction=False)
    pipe.hgetall(skey)
    pipe.zrange(ekey, 0, -1, withscores=True)
    pipe.hgetall(xkey)
    pipe.ttl(xkey)
    meta, members, scratch, ttl = await pipe.execute()
    if not meta and not members and not scratch:
        return None

    if ttl == -2:
        ttl_note = "key expired or deleted (TTL elapsed)"
    elif ttl == -1:
        ttl_note = "no TTL set"
    else:
        ttl_note = f"TTL {ttl} s remaining"

    return {
        "engine": "valkey",
        "run_id": run_id,
        "blocks": [
            {
                "title": "Session",
                "structure": f"HASH {skey}",
                "command": f"HGETALL {skey}",
                "note": f"{len(meta)} fields",
                "columns": ["field", "value"],
                "rows": [{"field": _text(k), "value": _json(v)} for k, v in meta.items()],
            },
            {
                "title": "Session events",
                "structure": f"ZSET {ekey}",
                "command": f"ZRANGE {ekey} 0 -1 WITHSCORES",
                "note": f"{len(members)} members · score = event timestamp",
                "columns": ["score", "member"],
                "rows": [{"score": score, "member": _json(m)} for m, score in members],
            },
            {
                "title": "Shared scratchpad",
                "structure": f"HASH {xkey}",
                "command": f"HGETALL {xkey} · TTL {xkey}",
                "note": f"{len(scratch)} fields · {ttl_note}",
                "ttl_s": ttl,
                "columns": ["field", "value"],
                "rows": [{"field": _text(k), "value": _json(v)} for k, v in scratch.items()],
            },
        ],
    }


async def _postgres_snapshot(backend_id: str, run_id: str) -> dict[str, Any] | None:
    app, user = settings.app_name, settings.demo_user_id
    async with backends.raw_pg_pool(backend_id).acquire() as conn:
        sessions = await conn.fetch(_PG_SESSION_SQL, app, user, run_id)
        events = await conn.fetch(_PG_EVENTS_SQL, app, user, run_id, EVENT_ROW_LIMIT)
        scratch = await conn.fetch(_PG_SCRATCH_SQL, run_id)
    if not sessions and not events and not scratch:
        return None

    expired = sum(1 for r in scratch if r["expired"])
    note = f"{len(scratch)} rows"
    if expired:
        note += f" · {expired} past expires_at (invisible to reads; removed by the sweeper)"

    return {
        "engine": "postgres",
        "run_id": run_id,
        "blocks": [
            {
                "title": "Session",
                "structure": "TABLE adk_sessions",
                "command": _PG_SESSION_SQL,
                "note": f"{len(sessions)} row",
                "columns": ["session_id", "state", "last_update_time"],
                "rows": [{"session_id": r["session_id"], "state": _json(r["state"]),
                          "last_update_time": r["last_update_time"]} for r in sessions],
            },
            {
                "title": "Session events",
                "structure": "TABLE adk_events",
                "command": _PG_EVENTS_SQL,
                "note": f"{len(events)} rows",
                "columns": ["ts", "author", "event_id", "payload"],
                "rows": [{"ts": r["ts"], "author": r["author"], "event_id": r["event_id"],
                          "payload": _json(r["payload"])} for r in events],
            },
            {
                "title": "Shared scratchpad",
                "structure": "TABLE scratchpad",
                "command": _PG_SCRATCH_SQL,
                "note": note,
                "columns": ["field", "value", "updated_at", "expires_at", "expired"],
                "rows": [{"field": r["field"], "value": _json(r["value"]),
                          "updated_at": _plain(r["updated_at"]),
                          "expires_at": _plain(r["expires_at"]),
                          "expired": r["expired"]} for r in scratch],
            },
        ],
    }
