"""Shared fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.state.registry import backends

ARMS = ["valkey", "postgres", "postgres_cached"]


@pytest_asyncio.fixture(scope="session", autouse=True)
async def live_backends():
    """Bring up shared clients; skip the suite if the containers aren't running."""
    await backends.startup()
    health = await backends.health()
    bad = {k: v for k, v in health.items() if v != "ok"}
    if bad:
        await backends.shutdown()
        pytest.skip(f"backends unavailable: {bad} (run `docker compose up -d`)")
    yield
    await backends.shutdown()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test reaches Gemini, even with an API key in the environment.

    Live research refuses to call the model (tests patch research._generate
    with canned answers instead), and runs started from the page use the
    scripted model unless a test opts into "gemini".
    """
    from app import main
    from app.providers import research

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to call Gemini for live research")

    monkeypatch.setattr(research, "_call", refuse)
    monkeypatch.setattr(main, "UI_LLM_MODE", "fake")


BRIEF = {
    "destination": "Lisbon",
    "origin": "SFO",
    "start_date": "2026-10-10",
    "end_date": "2026-10-14",
    "travelers": 2,
    "budget_total": 4000,
    "nuance": "food markets",
}
