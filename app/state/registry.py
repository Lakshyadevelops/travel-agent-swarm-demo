"""Backend registry.

Arms are registry entries, not a boolean. Adding `postgres_sync_off` or a future
arm is a dict entry -- the UI selector, benchmark runner and sweeps all enumerate
this registry, so nothing downstream hardcodes "valkey or postgres".

Clients are built once at startup and shared for process lifetime so the UI's
live toggle never pays connection-setup cost mid-run and pools never leak.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import asyncpg
import redis.asyncio as redis
from google.adk.sessions import BaseSessionService

from app.config import settings
from app.state.base import ScratchpadStore
from app.state.cached_scratchpad import CachedScratchpad
from app.state.postgres_scratchpad import PostgresScratchpad
from app.state.postgres_session import PostgresSessionService
from app.state.valkey_scratchpad import ValkeyScratchpad
from app.state.valkey_session import ValkeySessionService


@dataclass(frozen=True)
class BackendSpec:
    id: str
    label: str
    notes: str          # rendered verbatim in the UI methodology panel
    is_durable: bool


BACKEND_SPECS: dict[str, BackendSpec] = {
    "valkey": BackendSpec(
        id="valkey",
        label="Valkey (in-memory)",
        notes=(
            "Valkey 8 alpine, stock config except maxmemory=200mb/allkeys-lru. "
            "Native HASH scratchpad with EXPIRE; sessions as HASH + ZSET. "
            "No fsync-per-commit durability: this is the property being priced."
        ),
        is_durable=False,
    ),
    "postgres": BackendSpec(
        id="postgres",
        label="PostgreSQL (durable)",
        notes=(
            "Postgres 17 alpine, STOCK config (synchronous_commit=on), "
            "max_connections=50 to fit the 256MB cap. JSONB state, single "
            "round trip per logical operation."
        ),
        is_durable=True,
    ),
    "postgres_cached": BackendSpec(
        id="postgres_cached",
        label="PostgreSQL + Valkey cache",
        notes=(
            "Postgres 17 as durable system of record with a separate Valkey 8 "
            "cache-aside tier. Write-through; reads served from cache on hit. "
            "This is the realistic 'should I add a cache?' configuration."
        ),
        is_durable=True,
    ),
    "postgres_sync_off": BackendSpec(
        id="postgres_sync_off",
        label="PostgreSQL (synchronous_commit=off)",
        notes=(
            "Sensitivity arm. Identical to `postgres` but with fsync-per-commit "
            "disabled, isolating how much of the Postgres/Valkey gap is durability "
            "rather than engine overhead. Not a default; used in sweeps."
        ),
        is_durable=False,
    ),
}


class BackendManager:
    """Owns process-lifetime clients for every arm."""

    def __init__(self) -> None:
        self._valkey: redis.Redis | None = None
        self._valkey_cache: redis.Redis | None = None
        self._pg_pool: asyncpg.Pool | None = None
        self._pg_pool_sync_off: asyncpg.Pool | None = None
        # PER-BACKEND gates. A single shared semaphore would couple the arms and
        # serialise the cached arm against its own origin.
        self._sem_valkey = asyncio.Semaphore(settings.valkey_max_concurrency)
        self._sem_valkey_cache = asyncio.Semaphore(settings.valkey_max_concurrency)
        self._sem_pg = asyncio.Semaphore(settings.postgres_max_concurrency)
        self._sem_pg_sync_off = asyncio.Semaphore(settings.postgres_max_concurrency)

    async def startup(self) -> None:
        # BlockingConnectionPool: when every connection is busy, wait for one
        # (like asyncpg's pool) instead of raising MaxConnectionsError. The
        # default redis-py pool raised under the 1000-user load test.
        def _valkey_client(url: str) -> redis.Redis:
            pool = redis.BlockingConnectionPool.from_url(
                url, max_connections=settings.valkey_max_concurrency, timeout=30)
            return redis.Redis(connection_pool=pool, decode_responses=False)

        self._valkey = _valkey_client(settings.valkey_url)
        self._valkey_cache = _valkey_client(settings.valkey_cache_url)
        self._pg_pool = await asyncpg.create_pool(
            settings.postgres_dsn,
            min_size=settings.postgres_pool_min,
            max_size=settings.postgres_pool_max,
            command_timeout=10,
        )
        self._pg_pool_sync_off = await asyncpg.create_pool(
            settings.postgres_dsn,
            min_size=1,
            max_size=settings.postgres_pool_max,
            command_timeout=10,
            server_settings={"synchronous_commit": "off"},
        )

    async def shutdown(self) -> None:
        for client in (self._valkey, self._valkey_cache):
            if client is not None:
                await client.aclose()
        for pool in (self._pg_pool, self._pg_pool_sync_off):
            if pool is not None:
                await pool.close()

    # ---- builders ----------------------------------------------------
    def session_service(self, backend_id: str) -> BaseSessionService:
        if backend_id == "valkey":
            return ValkeySessionService(self._valkey, self._sem_valkey, "valkey")
        if backend_id == "postgres":
            return PostgresSessionService(self._pg_pool, self._sem_pg, "postgres")
        if backend_id == "postgres_cached":
            # Sessions stay on Postgres: the cache tier is for the hot scratchpad
            # path. Caching the append-only event log would be a write-through
            # cache nobody reads -- pure overhead.
            return PostgresSessionService(self._pg_pool, self._sem_pg, "postgres_cached")
        if backend_id == "postgres_sync_off":
            return PostgresSessionService(
                self._pg_pool_sync_off, self._sem_pg_sync_off, "postgres_sync_off"
            )
        raise ValueError(f"unknown backend: {backend_id}")

    def scratchpad(self, backend_id: str) -> ScratchpadStore:
        ttl = settings.scratchpad_ttl_seconds
        if backend_id == "valkey":
            return ValkeyScratchpad(self._valkey, ttl, self._sem_valkey, "valkey")
        if backend_id == "postgres":
            return PostgresScratchpad(self._pg_pool, ttl, self._sem_pg, "postgres")
        if backend_id == "postgres_cached":
            origin = PostgresScratchpad(
                self._pg_pool, ttl, self._sem_pg, "postgres_cached"
            )
            return CachedScratchpad(
                origin, self._valkey_cache, ttl, self._sem_valkey_cache
            )
        if backend_id == "postgres_sync_off":
            return PostgresScratchpad(
                self._pg_pool_sync_off, ttl, self._sem_pg_sync_off, "postgres_sync_off"
            )
        raise ValueError(f"unknown backend: {backend_id}")

    async def evicted_keys(self, backend_id: str) -> int:
        """Valkey eviction count.

        Non-zero means results are not comparable across arms AND flags a real
        operational hazard of the cache approach, so it is surfaced in the UI
        rather than quietly ignored.
        """
        client = self._valkey if backend_id == "valkey" else self._valkey_cache
        if client is None or backend_id in ("postgres", "postgres_sync_off"):
            return 0
        try:
            info = await client.info("stats")
            return int(info.get("evicted_keys", 0))
        except Exception:  # noqa: BLE001 - diagnostics must never break a run
            return 0

    async def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            await self._valkey.ping()
            out["valkey"] = "ok"
        except Exception as exc:  # noqa: BLE001
            out["valkey"] = f"error: {exc}"
        try:
            await self._valkey_cache.ping()
            out["valkey_cache"] = "ok"
        except Exception as exc:  # noqa: BLE001
            out["valkey_cache"] = f"error: {exc}"
        try:
            await self._pg_pool.fetchval("SELECT 1;")
            out["postgres"] = "ok"
        except Exception as exc:  # noqa: BLE001
            out["postgres"] = f"error: {exc}"
        return out


backends = BackendManager()
