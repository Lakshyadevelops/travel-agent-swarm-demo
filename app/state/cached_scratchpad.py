"""Arm 3: Postgres as system of record, Valkey as a cache-aside tier.

This is the arm that answers the question a viewer actually has. Nobody runs an
agent app on Valkey as the system of record; the real decision is "I already have
Postgres -- should I put a cache in front of it?"

Semantics:
  read       -> cache GET; on miss fall through to Postgres and populate.
  write      -> write-through: Postgres first (durable), then refresh the cache.
  read_all   -> cache HGETALL; on miss, one Postgres read then repopulate + EXPIRE.

Every read records `cache_hit`, so the UI can report hit rate and split latency by
hit vs miss. A 95% hit rate saving 2ms is a very different recommendation from a
60% hit rate saving 40ms, and the aggregate alone cannot distinguish them.
"""

from __future__ import annotations

from typing import Any, Mapping

import orjson
import redis.asyncio as redis

from app.state.postgres_scratchpad import PostgresScratchpad
from app.telemetry.instrument import timed


class CachedScratchpad:
    backend = "postgres_cached"

    PRIMITIVES = {
        "scratch_write": "Postgres upsert (durable) then HSET cache:scratch:<run> + EXPIRE "
                         "(2 round trips)",
        "scratch_write_many": "Postgres unnest upsert then HSET cache:scratch:<run> + EXPIRE "
                              "(2 round trips)",
        "scratch_read": "HGET cache:scratch:<run> <field>; on miss SELECT from Postgres "
                        "and repopulate",
        "scratch_read_all": "HGETALL cache:scratch:<run>; on miss SELECT from Postgres "
                            "and repopulate",
        "scratch_delete": "DELETE FROM scratchpad + DEL cache:scratch:<run>",
    }

    def __init__(
        self,
        origin: PostgresScratchpad,
        cache: redis.Redis,
        ttl_seconds: int,
        semaphore,
    ) -> None:
        self._origin = origin
        self._cache = cache
        self._ttl = ttl_seconds
        self._sem = semaphore

    @staticmethod
    def _key(run_id: str) -> str:
        return f"cache:scratch:{run_id}"

    async def write(self, run_id: str, field: str, value: Any) -> None:
        key = self._key(run_id)
        blob = orjson.dumps(value)
        async with timed("scratch_write", self.backend, key) as box:
            box.payload_bytes = len(blob)
            box.round_trips = 2  # durable origin write + cache refresh
            box.field, box.value = field, value
            # Origin first: a crash between the two must never leave the cache
            # advertising data that was never durably committed.
            await self._origin.write_raw(run_id, field, blob.decode())
            async with self._sem:
                pipe = self._cache.pipeline(transaction=False)
                pipe.hset(key, field, blob)
                pipe.expire(key, self._ttl)
                await pipe.execute()

    async def write_many(self, run_id: str, values: Mapping[str, Any]) -> None:
        if not values:
            return
        key = self._key(run_id)
        mapping = {f: orjson.dumps(v) for f, v in values.items()}
        async with timed("scratch_write_many", self.backend, key) as box:
            box.payload_bytes = sum(len(v) for v in mapping.values())
            box.round_trips = 2
            box.value = values
            await self._origin.write_many_raw(
                run_id, list(mapping.keys()), [v.decode() for v in mapping.values()]
            )
            async with self._sem:
                pipe = self._cache.pipeline(transaction=False)
                pipe.hset(key, mapping=mapping)
                pipe.expire(key, self._ttl)
                await pipe.execute()

    async def read(self, run_id: str, field: str) -> Any | None:
        key = self._key(run_id)
        async with timed("scratch_read", self.backend, key) as box:
            box.field = field
            async with self._sem:
                raw = await self._cache.hget(key, field)
            if raw is not None:
                box.cache_hit = True
                box.payload_bytes = len(raw)
                return orjson.loads(raw)

            box.cache_hit = False
            box.round_trips = 2
            value = await self._origin.read_raw(run_id, field)
            if value is not None:
                async with self._sem:
                    pipe = self._cache.pipeline(transaction=False)
                    pipe.hset(key, field, value)
                    pipe.expire(key, self._ttl)
                    await pipe.execute()
                box.payload_bytes = len(value)
                return orjson.loads(value)
            return None

    async def read_all(self, run_id: str) -> dict[str, Any]:
        key = self._key(run_id)
        async with timed("scratch_read_all", self.backend, key) as box:
            async with self._sem:
                raw = await self._cache.hgetall(key)
            if raw:
                box.cache_hit = True
                box.payload_bytes = sum(len(v) for v in raw.values())
                box.value = raw  # the inspector lists the field names
                return {
                    (k.decode() if isinstance(k, bytes) else k): orjson.loads(v)
                    for k, v in raw.items()
                }

            box.cache_hit = False
            box.round_trips = 2
            rows = await self._origin.read_all_raw(run_id)
            if rows:
                async with self._sem:
                    pipe = self._cache.pipeline(transaction=False)
                    pipe.hset(key, mapping=rows)
                    pipe.expire(key, self._ttl)
                    await pipe.execute()
                box.payload_bytes = sum(len(v) for v in rows.values())
            box.value = rows
            return {k: orjson.loads(v) for k, v in rows.items()}

    async def delete(self, run_id: str) -> None:
        async with timed("scratch_delete", self.backend, self._key(run_id)) as box:
            box.round_trips = 2
            await self._origin.delete_raw(run_id)
            async with self._sem:
                await self._cache.delete(self._key(run_id))

    async def close(self) -> None:
        await self._cache.aclose()
        await self._origin.close()
