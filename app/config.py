"""Environment-driven settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

RUNS_DIR = REPO_ROOT / "runs"


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    google_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))
    gemini_model: str = field(
        # 3.8-flash then 3.5-flash returned frequent 503 UNAVAILABLE under
        # demand; 3.6-flash was 4/4 in a probe at similar latency. Agents also
        # retry 429/5xx with backoff (see swarm._model_for).
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    )
    llm_mode: str = field(default_factory=lambda: os.getenv("LLM_MODE", "fake"))

    valkey_url: str = field(
        default_factory=lambda: os.getenv("VALKEY_URL", "redis://localhost:6379")
    )
    valkey_cache_url: str = field(
        default_factory=lambda: os.getenv("VALKEY_CACHE_URL", "redis://localhost:6380")
    )
    postgres_dsn: str = field(
        default_factory=lambda: os.getenv(
            "POSTGRES_DSN", "postgresql://swarm:swarm@localhost:5432/swarm"
        )
    )

    app_port: int = field(default_factory=lambda: _int("APP_PORT", 8080))

    provider_latency_ms: int = field(
        default_factory=lambda: _int("PROVIDER_LATENCY_MS", 40)
    )
    provider_latency_ms_bench: int = field(
        default_factory=lambda: _int("PROVIDER_LATENCY_MS_BENCH", 0)
    )

    scratchpad_writes_per_step: int = field(
        default_factory=lambda: _int("SCRATCHPAD_WRITES_PER_STEP", 1)
    )
    scratchpad_ttl_seconds: int = field(
        default_factory=lambda: _int("SCRATCHPAD_TTL_SECONDS", 900)
    )

    # Concurrency gates are PER BACKEND, never shared: a shared semaphore would
    # couple the arms and serialise the cached arm against itself. Env-driven so
    # the multi-process load generator can split Postgres's max_connections=50
    # budget across worker processes.
    valkey_max_concurrency: int = field(
        default_factory=lambda: _int("VALKEY_MAX_CONCURRENCY", 32))
    postgres_max_concurrency: int = field(
        default_factory=lambda: _int("POSTGRES_MAX_CONCURRENCY", 16))
    postgres_pool_min: int = field(default_factory=lambda: _int("POSTGRES_POOL_MIN", 2))
    postgres_pool_max: int = field(default_factory=lambda: _int("POSTGRES_POOL_MAX", 16))

    app_name: str = "travel_swarm"
    # Every run belongs to this user: the demo has no accounts.
    # TODO(security): real per-user ids once authentication exists.
    demo_user_id: str = "demo-user"


settings = Settings()
