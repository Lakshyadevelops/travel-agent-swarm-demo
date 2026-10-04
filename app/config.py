"""Environment-driven settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# Per-run telemetry (ops.jsonl) and the live model-latency trace land here.
# Overridable because the container image's code tree is read-only; Cloud Run
# sets RUNS_DIR=/tmp/runs.
RUNS_DIR = Path(os.getenv("RUNS_DIR") or REPO_ROOT / "runs")

# Retry policy for "busy" Gemini answers (429 and 5xx), shared by the agents
# (swarm._model_for) and grounded research (research._generate). Under high
# demand Gemini answers 503 UNAVAILABLE ("preempted by higher priority") in
# spells that can outlast 15 s, so a call rides out about 70 s of them: pauses
# of roughly 2, 4, 8, 16, 20 and 20 s between 7 attempts. A busy answer comes
# back in about a second, so the pauses are nearly all of the extra wait.
MODEL_RETRY_ATTEMPTS = 7
MODEL_RETRY_FIRST_S = 2.0
MODEL_RETRY_MAX_S = 20.0


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


# Values of VALKEY_CACHE_URL that mean "no cache tier". The hosted deployment
# has no second Valkey (the UI never offers the postgres_cached arm), and an
# explicit "off" beats a URL that silently fails every health check.
CACHE_OFF_VALUES = frozenset({"", "off", "none", "disabled"})


@dataclass(frozen=True)
class Settings:
    google_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))
    # Vertex AI through Application Default Credentials: the hosted deployment
    # runs as a Cloud Run service account and has no API key at all. The same
    # three variables drive google-genai directly, so ADK's Gemini model (which
    # builds a Client with no explicit credentials) follows them without help.
    use_vertex: bool = field(default_factory=lambda: _flag("GOOGLE_GENAI_USE_VERTEXAI"))
    gcp_project: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    # "global" is where the Gemini 3.x publisher models are served; a regional
    # location returns 404 for them. Independent of where the stores live.
    vertex_location: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    )
    gemini_model: str = field(
        # 3.8-flash then 3.5-flash returned frequent 503 UNAVAILABLE under
        # demand; 3.6-flash was 4/4 in a probe at similar latency. Agents also
        # retry 429/5xx with backoff (see swarm._model_for).
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    )
    llm_mode: str = field(default_factory=lambda: os.getenv("LLM_MODE", "fake"))

    # The compose file publishes every store on 127.0.0.1 only.
    valkey_url: str = field(
        default_factory=lambda: os.getenv("VALKEY_URL", "redis://127.0.0.1:6379")
    )
    # Empty string once normalised means "no cache tier" (see CACHE_OFF_VALUES).
    valkey_cache_url: str = field(
        default_factory=lambda: _cache_url(os.getenv("VALKEY_CACHE_URL", "redis://127.0.0.1:6380"))
    )
    postgres_dsn: str = field(
        default_factory=lambda: os.getenv(
            "POSTGRES_DSN", "postgresql://swarm:swarm@127.0.0.1:5432/swarm"
        )
    )

    # Cloud Run tells the container which port to serve on via PORT; it wins
    # over the repo's APP_PORT so one image serves both environments.
    app_port: int = field(default_factory=lambda: _int("PORT", _int("APP_PORT", 8080)))

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

    @property
    def model_ready(self) -> bool:
        """Can live planning reach a model at all? Never reveals which way."""
        return bool(self.google_api_key) or self.use_vertex

    @property
    def cache_enabled(self) -> bool:
        return bool(self.valkey_cache_url)


def _cache_url(raw: str) -> str:
    return "" if raw.strip().lower() in CACHE_OFF_VALUES else raw.strip()


settings = Settings()
