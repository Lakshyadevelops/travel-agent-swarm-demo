"""Blackboard coordination between concurrently running agents.

The scratchpad store holds the data; this module holds the *signals*. When the
scout publishes its shortlist, the stay agent -- running at the same moment in
the ParallelAgent fan-out -- needs to know it is there without hammering the
store in a poll loop.

Why not poll the store? Polling makes the number of reads depend on timing: a
slower store would issue more reads, which would then make it look slower still.
That breaks the benchmark's core parity guarantee that every arm issues an
identical operation sequence per run. So:

* `publish(field)` sets an in-process asyncio.Event after the store write lands;
* `await_field(field)` waits on the event, then issues **exactly one** store
  read. Same op count on every arm, every run, regardless of timing.

Signals are scoped to a *round* of the budget loop. The guardrail bumps the round
before asking for a retry, so round-2 waiters cannot be satisfied by a stale
round-1 publication.

One Coordinator is created per run and shared via a contextvar. ParallelAgent
children run in copied contexts, but a copied contextvar still points to the
same object, so every child sees the same events.
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Any


class Coordinator:
    def __init__(self, wait_timeout_s: float = 30.0, max_rounds: int = 3) -> None:
        self.round = 0
        self.max_rounds = max_rounds
        self.wait_timeout_s = wait_timeout_s
        self._events: dict[str, asyncio.Event] = {}
        # Human-readable collaboration trace for the UI. In-memory only: it is
        # not state-layer traffic and must not add store ops.
        self.trace: list[dict[str, Any]] = []
        self._t0 = time.perf_counter()

    def _event(self, field: str, round_: int | None = None) -> asyncio.Event:
        key = f"{field}@{self.round if round_ is None else round_}"
        ev = self._events.get(key)
        if ev is None:
            ev = self._events[key] = asyncio.Event()
        return ev

    def announce(self, field: str, agent: str) -> None:
        self._event(field).set()
        self.note(agent, "published", field)

    def next_round(self) -> int:
        self.round += 1
        return self.round

    async def wait_for(self, field: str, agent: str) -> tuple[bool, float]:
        """Block until `field` is published this round. Returns (ok, waited_ms)."""
        ev = self._event(field)
        started = time.perf_counter()
        ok = True
        if not ev.is_set():
            try:
                await asyncio.wait_for(ev.wait(), timeout=self.wait_timeout_s)
            except asyncio.TimeoutError:
                ok = False
        waited = (time.perf_counter() - started) * 1000.0
        self.note(agent, "waited" if ok else "timed_out", field, waited_ms=round(waited, 1))
        return ok, waited

    def note(self, agent: str, action: str, field: str, **extra: Any) -> None:
        self.trace.append({
            "t_ms": round((time.perf_counter() - self._t0) * 1000.0, 1),
            "round": self.round,
            "agent": agent,
            "action": action,
            "field": field,
            **extra,
        })


CURRENT_COORD: ContextVar[Coordinator | None] = ContextVar("current_coord", default=None)
