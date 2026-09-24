"""Postgres scratchpad: JSONB with TTL emulated via expires_at.

ROUND-TRIP DISCIPLINE (the thing that distorted the first draft of this project):
every logical operation is exactly ONE round trip, matching Valkey's pipelined
equivalents. `write_many` in particular uses a single `unnest` multi-row upsert
rather than `executemany`, which would have been N round trips and would have
attributed our own app-code chattiness to Postgres.
"""

from __future__ import annotations

from typing import Any, Mapping

import asyncpg
import orjson

from app.telemetry.instrument import timed

# Single statement -> implicitly atomic, so no BEGIN/COMMIT round trips.
# No `SET LOCAL synchronous_commit` either: durability is what we are pricing,
# so the server's stock setting stands.
_WRITE_SQL = """
INSERT INTO scratchpad (run_id, field, value, expires_at)
VALUES ($1, $2, $3::jsonb, now() + ($4 || ' seconds')::interval)
ON CONFLICT (run_id, field)
DO UPDATE SET value = EXCLUDED.value,
              updated_at = now(),
              expires_at = EXCLUDED.expires_at;
"""

_WRITE_MANY_SQL = """
INSERT INTO scratchpad (run_id, field, value, expires_at)
SELECT $1, f, v::jsonb, now() + ($4 || ' seconds')::interval
  FROM unnest($2::text[], $3::text[]) AS t(f, v)
ON CONFLICT (run_id, field)
DO UPDATE SET value = EXCLUDED.value,
              updated_at = now(),
              expires_at = EXCLUDED.expires_at;
"""

_READ_SQL = """
SELECT value FROM scratchpad
 WHERE run_id = $1 AND field = $2
   AND (expires_at IS NULL OR expires_at > now());
"""

_READ_ALL_SQL = """
SELECT field, value FROM scratchpad
 WHERE run_id = $1
   AND (expires_at IS NULL OR expires_at > now());
"""

_SWEEP_SQL = "DELETE FROM scratchpad WHERE expires_at IS NOT NULL AND expires_at <= now();"


class PostgresScratchpad:
    backend = "postgres"

    def __init__(
        self,
        pool: asyncpg.Pool,
        ttl_seconds: int,
        semaphore,
        backend_label: str = "postgres",
    ) -> None:
        self._pool = pool
        self._ttl = str(ttl_seconds)
        self._sem = semaphore
        self.backend = backend_label

    async def write(self, run_id: str, field: str, value: Any) -> None:
        blob = orjson.dumps(value).decode()
        async with timed("scratch_write", self.backend, f"scratch:{run_id}") as box:
            box.payload_bytes = len(blob)
            box.round_trips = 1
            async with self._sem:
                await self._pool.execute(_WRITE_SQL, run_id, field, blob, self._ttl)

    async def write_many(self, run_id: str, values: Mapping[str, Any]) -> None:
        if not values:
            return
        fields = list(values.keys())
        blobs = [orjson.dumps(v).decode() for v in values.values()]
        async with timed("scratch_write_many", self.backend, f"scratch:{run_id}") as box:
            box.payload_bytes = sum(len(b) for b in blobs)
            box.round_trips = 1  # one unnest upsert, NOT executemany
            async with self._sem:
                await self._pool.execute(_WRITE_MANY_SQL, run_id, fields, blobs, self._ttl)

    async def read(self, run_id: str, field: str) -> Any | None:
        async with timed("scratch_read", self.backend, f"scratch:{run_id}") as box:
            async with self._sem:
                row = await self._pool.fetchval(_READ_SQL, run_id, field)
            box.payload_bytes = len(row) if row else 0
        return orjson.loads(row) if row else None

    async def read_all(self, run_id: str) -> dict[str, Any]:
        async with timed("scratch_read_all", self.backend, f"scratch:{run_id}") as box:
            async with self._sem:
                rows = await self._pool.fetch(_READ_ALL_SQL, run_id)
            box.payload_bytes = sum(len(r["value"]) for r in rows)
        return {r["field"]: orjson.loads(r["value"]) for r in rows}

    async def delete(self, run_id: str) -> None:
        async with timed("scratch_delete", self.backend, f"scratch:{run_id}"):
            async with self._sem:
                await self._pool.execute("DELETE FROM scratchpad WHERE run_id = $1;", run_id)

    async def sweep_expired(self) -> None:
        """Reclaim expired rows.

        Valkey reclaims automatically on EXPIRE; without this Postgres would
        accumulate dead tuples across a 30-iteration benchmark and be unfairly
        penalised by bloat that has nothing to do with its steady-state latency.
        Runs outside the measured path.
        """
        async with self._sem:
            await self._pool.execute(_SWEEP_SQL)

    # ---- raw, UNINSTRUMENTED variants -------------------------------
    # Used by CachedScratchpad, which wraps the whole cache+origin sequence in a
    # single `timed()` block. Calling the instrumented methods here would record
    # two StateOps for one logical operation and corrupt the op counts.

    async def write_raw(self, run_id: str, field: str, blob: str) -> None:
        async with self._sem:
            await self._pool.execute(_WRITE_SQL, run_id, field, blob, self._ttl)

    async def write_many_raw(
        self, run_id: str, fields: list[str], blobs: list[str]
    ) -> None:
        async with self._sem:
            await self._pool.execute(_WRITE_MANY_SQL, run_id, fields, blobs, self._ttl)

    async def read_raw(self, run_id: str, field: str) -> str | None:
        async with self._sem:
            return await self._pool.fetchval(_READ_SQL, run_id, field)

    async def read_all_raw(self, run_id: str) -> dict[str, str]:
        async with self._sem:
            rows = await self._pool.fetch(_READ_ALL_SQL, run_id)
        return {r["field"]: r["value"] for r in rows}

    async def delete_raw(self, run_id: str) -> None:
        async with self._sem:
            await self._pool.execute("DELETE FROM scratchpad WHERE run_id = $1;", run_id)

    async def close(self) -> None:
        await self._pool.close()

