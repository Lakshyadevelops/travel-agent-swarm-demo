"""Hosting-related configuration: Vertex route, schema bootstrap, optional cache.

The Cloud Run deployment differs from a laptop in three ways -- no API key
(Vertex AI through the service account), no init hook on Cloud SQL, and no
second Valkey for the cache tier. Each difference has a test here so a laptop
cannot silently diverge from what the hosted demo relies on.
"""

from __future__ import annotations

import dataclasses
import sys
import types
import uuid

import httpx
import pytest

import app.main as main
from app.config import REPO_ROOT, Settings, settings
from app.llm import client as llm_client
from app.state import registry
from app.state.registry import BackendManager, backends


# ------------------------------------------------------------- Settings
def _settings(monkeypatch, **env: str) -> Settings:
    for key in ("GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT",
                "GOOGLE_CLOUD_LOCATION", "VALKEY_CACHE_URL", "PORT", "APP_PORT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_model_ready_with_key_or_vertex(monkeypatch):
    assert not _settings(monkeypatch).model_ready
    assert _settings(monkeypatch, GOOGLE_API_KEY="k").model_ready
    assert _settings(monkeypatch, GOOGLE_GENAI_USE_VERTEXAI="true").model_ready
    assert _settings(monkeypatch, GOOGLE_GENAI_USE_VERTEXAI="1").model_ready
    assert not _settings(monkeypatch, GOOGLE_GENAI_USE_VERTEXAI="false").model_ready


def test_vertex_defaults_to_global_location(monkeypatch):
    s = _settings(monkeypatch, GOOGLE_GENAI_USE_VERTEXAI="true", GOOGLE_CLOUD_PROJECT="p")
    assert (s.use_vertex, s.gcp_project, s.vertex_location) == (True, "p", "global")


@pytest.mark.parametrize("raw", ["", "off", "OFF", "none", "disabled", "  off "])
def test_cache_tier_can_be_switched_off(monkeypatch, raw):
    s = _settings(monkeypatch, VALKEY_CACHE_URL=raw)
    assert s.valkey_cache_url == ""
    assert not s.cache_enabled


def test_cache_tier_default_and_explicit_url(monkeypatch):
    assert _settings(monkeypatch).cache_enabled
    s = _settings(monkeypatch, VALKEY_CACHE_URL="redis://cache:6379")
    assert (s.valkey_cache_url, s.cache_enabled) == ("redis://cache:6379", True)


def test_cloud_run_port_beats_app_port(monkeypatch):
    assert _settings(monkeypatch).app_port == 8080
    assert _settings(monkeypatch, APP_PORT="9000").app_port == 9000
    assert _settings(monkeypatch, APP_PORT="9000", PORT="8081").app_port == 8081


# ------------------------------------------------------- client factory
class _FakeGenai(types.ModuleType):
    """Stands in for google.genai so the test sees the constructor arguments."""

    def __init__(self) -> None:
        super().__init__("google.genai")
        self.calls: list[dict] = []
        test = self

        class Client:
            def __init__(self, **kwargs):
                test.calls.append(kwargs)

        self.Client = Client


@pytest.fixture
def fake_genai(monkeypatch):
    fake = _FakeGenai()
    import google

    monkeypatch.setitem(sys.modules, "google.genai", fake)
    monkeypatch.setattr(google, "genai", fake, raising=False)
    return fake


def test_client_factory_uses_vertex_without_a_key(monkeypatch, fake_genai):
    monkeypatch.setattr(llm_client, "settings", dataclasses.replace(
        settings, google_api_key="", use_vertex=True, gcp_project="ks-opm-project",
        vertex_location="global"))
    llm_client.make_client()
    assert fake_genai.calls == [
        {"vertexai": True, "project": "ks-opm-project", "location": "global"}
    ]
    assert llm_client.route_label() == "vertex:global"


def test_client_factory_prefers_vertex_over_a_stray_key(monkeypatch, fake_genai):
    """A developer's .env key must not flip a Vertex deployment onto the key."""
    monkeypatch.setattr(llm_client, "settings", dataclasses.replace(
        settings, google_api_key="stray", use_vertex=True, gcp_project="p"))
    llm_client.make_client()
    assert fake_genai.calls[0]["vertexai"] is True
    assert "api_key" not in fake_genai.calls[0]


def test_client_factory_uses_the_key_locally(monkeypatch, fake_genai):
    monkeypatch.setattr(llm_client, "settings", dataclasses.replace(
        settings, google_api_key="local-key", use_vertex=False))
    llm_client.make_client()
    assert fake_genai.calls == [{"api_key": "local-key"}]
    assert llm_client.route_label() == "api-key"


# ------------------------------------------------------------ schema
async def test_schema_bootstrap_is_idempotent():
    """Every Cloud Run instance replays schema.sql at startup; it must be a no-op
    on an initialised database and leave the tables usable."""
    before = await backends.raw_pg_pool("postgres").fetchval(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';"
    )
    await backends.ensure_schema()
    await backends.ensure_schema()
    after = await backends.raw_pg_pool("postgres").fetchval(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';"
    )
    assert after == before
    run = f"r-{uuid.uuid4().hex[:8]}"
    pad = backends.scratchpad("postgres")
    await pad.write(run, "k", {"v": 1})
    assert await pad.read(run, "k") == {"v": 1}
    await pad.delete(run)


def test_schema_file_is_fully_idempotent():
    sql = registry.SCHEMA_PATH.read_text()
    creates = [line for line in sql.splitlines() if line.startswith("CREATE ")]
    assert creates, "schema.sql has no CREATE statements?"
    assert all("IF NOT EXISTS" in line for line in creates), creates


# ---------------------------------------------------- optional cache tier
async def test_manager_without_cache_tier(monkeypatch):
    monkeypatch.setattr(registry, "settings", dataclasses.replace(settings, valkey_cache_url=""))
    manager = BackendManager()
    await manager.startup()
    try:
        health = await manager.health()
        assert health == {"valkey": "ok", "postgres": "ok"}  # no phantom "down"
        assert manager.scratchpad("postgres") is not None
        with pytest.raises(ValueError, match="VALKEY_CACHE_URL"):
            manager.scratchpad("postgres_cached")
        assert await manager.evicted_keys("postgres_cached") == 0
    finally:
        await manager.shutdown()


# ------------------------------------------------------------ the API
async def test_config_reports_readiness_not_the_route(monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(
        settings, google_api_key="", use_vertex=True))
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        body = (await client.get("/api/config")).json()
    assert body["model_ready"] is True
    assert "has_api_key" not in body
    assert not {k for k in body if "vertex" in k.lower() or "key" in k.lower()}


def test_ui_uses_the_readiness_flag():
    js = (REPO_ROOT / "app" / "static" / "app.js").read_text()
    assert "model_ready" in js
    assert "has_api_key" not in js
