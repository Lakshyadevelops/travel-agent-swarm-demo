"""Valkey scratchpad: native hashes with automatic expiry.

One pipelined round trip per logical operation, which is the bar the Postgres
implementation has to meet (see postgres_scratchpad.py).
"""

from __future__ import annotations

from typing import Any, Mapping

import orjson
import redis.asyncio as redis

from app.telemetry.instrument import timed


class ValkeyScratchpad:
    backend = "valkey"

    def __init__(
        self,
        client: redis.Redis,
        ttl_seconds: int,
        semaphore,
        backend_label: str = "valkey",
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._sem = semaphore
        self.backend = backend_label

    @staticmethod
    def _key(run_id: str) -> str:
        return f"scratch:{run_id}"

    async def write(self, run_id: str, field: str, value: Any) -> None:
        key = self._key(run_id)
        blob = orjson.dumps(value)
        async with timed("scratch_write", self.backend, key) as box:
            box.payload_bytes = len(blob)
            async with self._sem:
                pipe = self._client.pipeline(transaction=False)
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
            async with self._sem:
                pipe = self._client.pipeline(transaction=False)
                pipe.hset(key, mapping=mapping)
                pipe.expire(key, self._ttl)
                await pipe.execute()

    async def read(self, run_id: str, field: str) -> Any | None:
        key = self._key(run_id)
        async with timed("scratch_read", self.backend, key) as box:
            async with self._sem:
                raw = await self._client.hget(key, field)
            box.payload_bytes = len(raw) if raw else 0
        return orjson.loads(raw) if raw else None

    async def read_all(self, run_id: str) -> dict[str, Any]:
        key = self._key(run_id)
        async with timed("scratch_read_all", self.backend, key) as box:
            async with self._sem:
                raw = await self._client.hgetall(key)
            box.payload_bytes = sum(len(v) for v in raw.values())
        return {
            (k.decode() if isinstance(k, bytes) else k): orjson.loads(v)
            for k, v in raw.items()
        }

    async def delete(self, run_id: str) -> None:
        key = self._key(run_id)
        async with timed("scratch_delete", self.backend, key):
            async with self._sem:
                await self._client.delete(key)

    async def close(self) -> None:
        await self._client.aclose()
