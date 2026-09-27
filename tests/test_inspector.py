"""Under the Hood plumbing: the live state feed, the raw inspector and the API."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import uuid

import httpx
import pytest

import app.main as main
from app.agents.orchestrator import run_swarm
from app.config import settings
from app.state import inspector
from app.telemetry.feed import RunStream
from app.telemetry.instrument import CURRENT_RUN
from app.telemetry.metrics import RunTelemetry
from tests.conftest import BRIEF

UI_ARMS = ["valkey", "postgres"]


def _run_id(arm: str) -> str:
    return f"{arm}-{uuid.uuid4().hex[:8]}"


def _drain(queue: RunStream) -> list[dict]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def _counts(result: dict) -> dict[str, int]:
    return {op: v["n"] for op, v in result["ops_by_type"].items()}


def _blocks(snap: dict) -> dict[str, dict]:
    return {b["title"]: b for b in snap["blocks"]}


# ------------------------------------------------------------------ feed
@pytest.mark.parametrize("arm", UI_ARMS)
async def test_feed_streams_every_op(arm):
    queue = RunStream()
    r = await run_swarm(BRIEF, arm, llm_mode="fake", provider_latency_ms=0,
                        step_queue=queue)
    events = _drain(queue)
    ops = [e for e in events if e["type"] == "state_op"]

    # One event per recorded op, on one strictly ordered timeline.
    assert len(ops) == r["ops_total"]
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(events) + 1))
    assert all(e["t_ms"] >= 0 for e in events)

    scratch = [e for e in ops if e["store"] == "scratchpad"]
    assert all(e["field"] for e in scratch if e["op"] in ("write", "read"))

    # Replaying the writes rebuilds exactly the final board.
    last_write = {e["field"]: e["value"] for e in scratch if e["op"] == "write"}
    assert last_write == r["scratchpad"]

    # Session ops carry what was stored: the initial state and each ADK event.
    create = next(e for e in ops if e["op_type"] == "session_create")
    assert create["value"]["brief"]["destination"] == "Lisbon"
    appends = [e for e in ops if e["op_type"] == "session_append"]
    assert appends[0]["value"]["author"] == "user"
    assert all("author" in e["value"] and "content" in e["value"] for e in appends)
    get = next(e for e in ops if e["op_type"] == "session_get")
    assert "events_loaded" in get["value"]
    assert not any(e["error"] for e in ops)


async def test_feed_does_not_change_op_counts():
    plain = await run_swarm(BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0)
    fed = await run_swarm(BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0,
                          step_queue=RunStream())
    assert _counts(plain) == _counts(fed)


async def test_waited_signal_replaces_per_read_events():
    queue = RunStream()
    await run_swarm(BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0,
                    step_queue=queue)
    signals = [e for e in _drain(queue) if e["type"] == "blackboard"]
    actions = {e["action"] for e in signals}
    assert "read" not in actions  # store reads are state_op events now
    assert actions <= {"wrote", "waited", "timed_out"}
    assert all(e["waited_ms"] >= 1 for e in signals if e["action"] == "waited")
    verdict = next(e for e in signals if e["field"] == "budget_verdict")
    assert verdict["within_budget"] is True and "within your" in verdict["detail"]


# ------------------------------------------------------------------ inspector
async def test_snapshot_valkey_layout():
    run_id = _run_id("valkey")
    r = await run_swarm(BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0,
                        run_id=run_id)
    blocks = _blocks(await inspector.snapshot("valkey", run_id))

    session = blocks["Session"]
    assert session["structure"] == f"HASH sess:{settings.app_name}:{settings.demo_user_id}:{run_id}"
    fields = {row["field"]: row["value"] for row in session["rows"]}
    assert fields["state"]["brief"]["destination"] == "Lisbon"

    events = blocks["Session events"]
    assert events["structure"].startswith("ZSET evt:")
    assert len(events["rows"]) == _counts(r)["session_append"]

    pad = blocks["Shared scratchpad"]
    assert pad["structure"] == f"HASH scratch:{run_id}"
    assert {row["field"] for row in pad["rows"]} == set(r["scratchpad"])
    assert 0 < pad["ttl_s"] <= settings.scratchpad_ttl_seconds


async def test_snapshot_postgres_layout():
    run_id = _run_id("postgres")
    r = await run_swarm(BRIEF, "postgres", llm_mode="fake", provider_latency_ms=0,
                        run_id=run_id)
    blocks = _blocks(await inspector.snapshot("postgres", run_id))

    session = blocks["Session"]
    assert session["structure"] == "TABLE adk_sessions"
    assert [row["session_id"] for row in session["rows"]] == [run_id]
    assert session["rows"][0]["state"]["brief"]["destination"] == "Lisbon"

    events = blocks["Session events"]
    assert len(events["rows"]) == _counts(r)["session_append"]
    ts = [row["ts"] for row in events["rows"]]
    assert ts == sorted(ts)

    pad = blocks["Shared scratchpad"]
    assert {row["field"] for row in pad["rows"]} == set(r["scratchpad"])
    assert not any(row["expired"] for row in pad["rows"])


async def test_snapshot_is_uninstrumented():
    run_id = _run_id("valkey")
    await run_swarm(BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0,
                    run_id=run_id)
    probe = RunTelemetry("probe", "valkey")
    token = CURRENT_RUN.set(probe)
    try:
        assert await inspector.snapshot("valkey", run_id) is not None
        await inspector.snapshot("postgres", run_id)
    finally:
        CURRENT_RUN.reset(token)
    assert probe.ops == []


async def test_snapshot_of_unknown_run_is_none():
    assert await inspector.snapshot("valkey", "valkey-00000000") is None
    assert await inspector.snapshot("postgres", "postgres-00000000") is None


# ------------------------------------------------------------------ API
def _client() -> httpx.AsyncClient:
    # ASGITransport skips the app lifespan; the suite's live_backends fixture
    # has already started the shared clients.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                             base_url="http://testserver")


def _sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def scripted_ui(monkeypatch):
    """The UI always uses live Gemini; tests swap the module constant, not a request field."""
    monkeypatch.setattr(main, "UI_LLM_MODE", "fake")


@pytest.mark.parametrize("arm", UI_ARMS)
async def test_run_stream_contract(scripted_ui, arm):
    async with _client() as client:
        resp = await client.post("/api/run", json=dict(BRIEF, backend=arm))
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _sse(resp.text)

        started = events[0]
        assert started["type"] == "started" and started["backend"] == arm
        assert started["layout"]["engine"] == ("valkey" if arm == "valkey" else "postgres")
        assert "session_append" in started["layout"]["primitives"]
        assert {"agent_step", "blackboard", "state_op"} <= {e["type"] for e in events}
        assert events[-1]["type"] == "complete"
        assert events[-1]["result"]["itinerary"]["days"]

        inspect = await client.get(f"/api/inspect/{started['run_id']}")
        assert inspect.status_code == 200
        assert inspect.json()["run_id"] == started["run_id"]


async def test_run_without_api_key_is_a_friendly_error(monkeypatch):
    monkeypatch.setattr(main, "UI_LLM_MODE", "gemini")
    monkeypatch.setattr(main, "settings", dataclasses.replace(settings, google_api_key=""))
    async with _client() as client:
        events = _sse((await client.post("/api/run", json=BRIEF)).text)
    assert [e["type"] for e in events] == ["started", "error"]
    assert events[1]["category"] == "config"
    assert "Traceback" not in events[1]["message"]


@pytest.mark.parametrize("patch", [
    {"travelers": 0},
    {"travelers": 13},
    {"budget_total": -1},
    {"start_date": "2026-10-14", "end_date": "2026-10-10"},
    {"start_date": "2026-10-01", "end_date": "2026-12-01"},
    {"start_date": "10/10/2026"},
    {"destination": "Atlantis"},
    {"destination": ""},
    {"nuance": "x" * 1001},
    {"backend": "postgres_cached"},
    {"llm_mode": "fake"},  # no request-level model switch
])
async def test_run_rejects_invalid_briefs(patch):
    async with _client() as client:
        resp = await client.post("/api/run", json=dict(BRIEF, **patch))
    assert resp.status_code == 422, patch


async def test_run_rejects_cross_site_requests():
    async with _client() as client:
        text = await client.post("/api/run", content=json.dumps(BRIEF),
                                 headers={"content-type": "text/plain"})
        foreign = await client.post("/api/run", json=BRIEF,
                                    headers={"origin": "http://evil.example"})
    assert text.status_code == 415
    assert foreign.status_code == 403


async def test_removed_and_invalid_endpoints():
    async with _client() as client:
        for path in ("/api/benchmark", "/api/sweep/write-frequency", "/api/sweep/concurrency"):
            assert (await client.post(path, json={})).status_code == 404, path
        for path in ("/api/gemini-latency", "/api/backends", "/api/metrics/valkey-12345678",
                     "/api/scratchpad/valkey-12345678", "/docs", "/openapi.json"):
            assert (await client.get(path)).status_code == 404, path
        for run_id in ("valkey-zzzzzzzz", "postgres_cached-12345678", "valkey-1234",
                       "valkey-00000000"):
            assert (await client.get(f"/api/inspect/{run_id}")).status_code == 404, run_id


async def test_config_and_security_headers():
    async with _client() as client:
        page = await client.get("/")
        cfg = await client.get("/api/config")
        health = await client.get("/healthz")

    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"
    assert cfg.headers["cache-control"] == "no-store"

    body = cfg.json()
    assert [s["id"] for s in body["stores"]] == ["valkey", "postgres"]
    assert "Lisbon" in body["destinations"]
    assert "llm_mode" not in body
    if settings.google_api_key:
        assert settings.google_api_key not in cfg.text
    assert set(health.json()["stores"]) == {"valkey", "postgres"}


async def test_page_pins_asset_versions_and_is_revalidated():
    """Regression: a browser ran the new page with an old cached app.js, whose
    Plan handler threw after showing "Planning…" and never sent the request."""
    version = main.asset_version()
    async with _client() as client:
        page = await client.get("/")
        assets = [await client.get(f"/static/{name}?v={version}") for name in main.UI_ASSETS]

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-cache"
    assert "__ASSET_VERSION__" not in page.text
    assert f'<script src="/static/app.js?v={version}" defer></script>' in page.text
    assert f'<link rel="stylesheet" href="/static/styles.css?v={version}">' in page.text
    for asset in assets:
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-cache"
        assert asset.headers["etag"]  # so revalidation is a cheap 304
        assert asset.headers["x-content-type-options"] == "nosniff"


def test_asset_version_follows_content(tmp_path):
    for name in main.UI_ASSETS:
        (tmp_path / name).write_text("v1")
    before = main.asset_version(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{12}", before)
    assert main.asset_version(tmp_path) == before
    (tmp_path / "styles.css").write_text("v2")
    assert main.asset_version(tmp_path) != before


async def test_quiet_run_stream_sends_keep_alives(monkeypatch):
    """Long model calls leave the queue empty; comments keep the stream visibly alive."""
    async def slow_swarm(*args, **kwargs):
        await asyncio.sleep(0.3)
        return {"itinerary": {"days": []}}

    monkeypatch.setattr(main, "UI_LLM_MODE", "fake")
    monkeypatch.setattr(main, "run_swarm", slow_swarm)
    monkeypatch.setattr(main, "HEARTBEAT_S", 0.05)
    async with _client() as client:
        text = (await client.post("/api/run", json=BRIEF)).text

    assert text.count(": keep-alive\n\n") >= 2
    assert [e["type"] for e in _sse(text)] == ["started", "complete"]
    assert [e["seq"] for e in _sse(text)] == [1, 2]  # comments carry no event
