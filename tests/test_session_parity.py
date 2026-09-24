"""Session-service parity across arms, with InMemorySessionService as the oracle.

Every arm must behave identically to ADK's own reference implementation. If a
backend diverges on state merge or event filtering, the benchmark is comparing
two different things and the numbers are meaningless.
"""

from __future__ import annotations

import uuid

import pytest
from google.adk.events.event import Event, EventActions
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types

from app.state.registry import backends
from tests.conftest import ARMS

APP = "parity_test"


def make_event(i: int, state_delta: dict | None = None) -> Event:
    return Event(
        author="tester",
        invocation_id=f"inv-{i}",
        content=types.Content(role="model", parts=[types.Part(text=f"message {i}")]),
        actions=EventActions(state_delta=state_delta or {}),
    )


def services():
    yield "oracle", InMemorySessionService()
    for arm in ARMS:
        yield arm, backends.session_service(arm)


@pytest.mark.parametrize("arm", ["oracle", *ARMS])
async def test_create_get_delete(arm):
    svc = InMemorySessionService() if arm == "oracle" else backends.session_service(arm)
    sid = f"s-{uuid.uuid4().hex[:8]}"

    created = await svc.create_session(
        app_name=APP, user_id="u", state={"budget": 100}, session_id=sid
    )
    assert created.id == sid
    assert created.state == {"budget": 100}

    got = await svc.get_session(app_name=APP, user_id="u", session_id=sid)
    assert got is not None
    assert got.state == {"budget": 100}
    assert got.events == []

    await svc.delete_session(app_name=APP, user_id="u", session_id=sid)
    assert await svc.get_session(app_name=APP, user_id="u", session_id=sid) is None


@pytest.mark.parametrize("arm", ["oracle", *ARMS])
async def test_state_delta_merge(arm):
    """State merge must match ADK's semantics exactly on every backend."""
    svc = InMemorySessionService() if arm == "oracle" else backends.session_service(arm)
    sid = f"s-{uuid.uuid4().hex[:8]}"
    s = await svc.create_session(app_name=APP, user_id="u", state={"a": 1}, session_id=sid)

    await svc.append_event(session=s, event=make_event(0, {"b": 2}))
    await svc.append_event(session=s, event=make_event(1, {"a": 99}))

    got = await svc.get_session(app_name=APP, user_id="u", session_id=sid)
    assert got.state == {"a": 99, "b": 2}, f"{arm} diverged: {got.state}"

    await svc.delete_session(app_name=APP, user_id="u", session_id=sid)


@pytest.mark.parametrize("arm", ["oracle", *ARMS])
async def test_event_ordering_and_filters(arm):
    svc = InMemorySessionService() if arm == "oracle" else backends.session_service(arm)
    sid = f"s-{uuid.uuid4().hex[:8]}"
    s = await svc.create_session(app_name=APP, user_id="u", state={}, session_id=sid)

    events = [make_event(i) for i in range(5)]
    for i, ev in enumerate(events):
        ev.timestamp = 1000.0 + i
        await svc.append_event(session=s, event=ev)

    full = await svc.get_session(app_name=APP, user_id="u", session_id=sid)
    assert len(full.events) == 5
    texts = [e.content.parts[0].text for e in full.events]
    assert texts == [f"message {i}" for i in range(5)], f"{arm} ordering: {texts}"

    recent = await svc.get_session(
        app_name=APP, user_id="u", session_id=sid,
        config=GetSessionConfig(num_recent_events=2),
    )
    assert len(recent.events) == 2
    assert [e.content.parts[0].text for e in recent.events] == ["message 3", "message 4"]

    after = await svc.get_session(
        app_name=APP, user_id="u", session_id=sid,
        config=GetSessionConfig(after_timestamp=1003.0),
    )
    assert len(after.events) == 2, f"{arm} after_timestamp: {len(after.events)}"

    none = await svc.get_session(
        app_name=APP, user_id="u", session_id=sid,
        config=GetSessionConfig(num_recent_events=0),
    )
    assert none.events == []

    await svc.delete_session(app_name=APP, user_id="u", session_id=sid)


@pytest.mark.parametrize("arm", ARMS)
async def test_list_sessions(arm):
    svc = backends.session_service(arm)
    user = f"u-{uuid.uuid4().hex[:6]}"
    ids = {f"s-{uuid.uuid4().hex[:8]}" for _ in range(3)}
    for sid in ids:
        await svc.create_session(app_name=APP, user_id=user, state={}, session_id=sid)

    listed = await svc.list_sessions(app_name=APP, user_id=user)
    assert {s.id for s in listed.sessions} == ids

    for sid in ids:
        await svc.delete_session(app_name=APP, user_id=user, session_id=sid)


async def test_arms_agree_with_oracle():
    """Same operations on every arm must yield the same Session state."""
    sid_base = uuid.uuid4().hex[:8]
    results = {}

    for name, svc in services():
        sid = f"s-{sid_base}-{name}"
        s = await svc.create_session(
            app_name=APP, user_id="u", state={"start": True}, session_id=sid
        )
        for i in range(3):
            ev = make_event(i, {f"k{i}": i})
            ev.timestamp = 2000.0 + i
            await svc.append_event(session=s, event=ev)

        got = await svc.get_session(app_name=APP, user_id="u", session_id=sid)
        results[name] = {
            "state": got.state,
            "n_events": len(got.events),
            "texts": [e.content.parts[0].text for e in got.events],
        }
        await svc.delete_session(app_name=APP, user_id="u", session_id=sid)

    oracle = results.pop("oracle")
    for arm, got in results.items():
        assert got == oracle, f"{arm} diverged from InMemorySessionService: {got} != {oracle}"
