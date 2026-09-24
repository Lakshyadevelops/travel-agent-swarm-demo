"""Swarm orchestration: run one planning session against one backend arm."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from google.adk.runners import Runner
from google.genai import types

from app.agents.blackboard import CURRENT_COORD, Coordinator
from app.agents.runtime import (
    CURRENT_BRIEF,
    CURRENT_RUN_ID,
    CURRENT_SCRATCHPAD,
    CURRENT_SEED,
    STEP_SINK,
    WRITES_PER_STEP,
)
from app.agents.swarm import PLAN_LOOP_MAX_ROUNDS, build_swarm
from app.config import settings
from app.providers.tools import PROVIDER_LATENCY_MS
from app.state.registry import backends
from app.telemetry.instrument import (
    CURRENT_ITERATION,
    CURRENT_OPLOG,
    CURRENT_RUN,
    CURRENT_VARIANT,
)
from app.telemetry.metrics import RunTelemetry
from app.telemetry.oplog import OpLog


def _nights(brief: dict[str, Any]) -> int:
    """Infer trip length from the date range, defaulting to 3."""
    start, end = brief.get("start_date"), brief.get("end_date")
    if start and end:
        try:
            from datetime import date

            d0 = date.fromisoformat(str(start))
            d1 = date.fromisoformat(str(end))
            return max(1, (d1 - d0).days)
        except ValueError:
            pass
    return 3


def brief_to_prompt(brief: dict[str, Any]) -> str:
    parts = [
        f"Destination: {brief.get('destination', 'anywhere')}",
        f"Origin: {brief.get('origin', 'SFO')}",
        f"Dates: {brief.get('start_date', 'TBD')} to {brief.get('end_date', 'TBD')}",
        f"Travelers: {brief.get('travelers', 1)}",
        f"Budget ceiling: ${brief.get('budget_total', 0):,.0f}",
    ]
    if brief.get("nuance"):
        parts.append(f"Preferences: {brief['nuance']}")
    return "\n".join(parts)


async def run_swarm(
    brief: dict[str, Any],
    backend_id: str,
    *,
    llm_mode: str | None = None,
    writes_per_step: int | None = None,
    provider_latency_ms: int | None = None,
    variant_id: str = "",
    iteration: int = -1,
    step_queue: asyncio.Queue | None = None,
    oplog: OpLog | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute one full planning session and return the telemetry summary."""
    run_id = run_id or f"{backend_id}-{uuid.uuid4().hex[:8]}"
    mode = llm_mode or settings.llm_mode

    brief = dict(brief)
    brief.setdefault("nights", _nights(brief))

    telemetry = RunTelemetry(run_id, backend_id, variant_id)

    # Bind the invocation context. All of these are contextvars so concurrent
    # runs (the concurrency sweep) stay isolated from one another.
    CURRENT_RUN.set(telemetry)
    CURRENT_VARIANT.set(variant_id)
    CURRENT_ITERATION.set(iteration)
    CURRENT_OPLOG.set(oplog)
    CURRENT_RUN_ID.set(run_id)
    CURRENT_BRIEF.set(brief)
    # Seed the mock providers from the BRIEF, never the run id: run ids embed the
    # backend name, which would give each arm different inventory and payload
    # sizes and silently invalidate the comparison.
    CURRENT_SEED.set(
        "|".join(
            str(brief.get(k, ""))
            for k in ("destination", "origin", "start_date", "end_date", "travelers")
        )
    )
    WRITES_PER_STEP.set(
        writes_per_step
        if writes_per_step is not None
        else settings.scratchpad_writes_per_step
    )
    PROVIDER_LATENCY_MS.set(
        provider_latency_ms
        if provider_latency_ms is not None
        else settings.provider_latency_ms
    )
    STEP_SINK.set(step_queue)
    # Per-run signalling for blackboard waits. Gemini agents can take tens of
    # seconds to reach their tool call, so the wait timeout is generous there.
    coord = Coordinator(wait_timeout_s=60.0 if mode == "gemini" else 10.0,
                        max_rounds=PLAN_LOOP_MAX_ROUNDS)
    CURRENT_COORD.set(coord)

    scratchpad = backends.scratchpad(backend_id)
    session_service = backends.session_service(backend_id)
    CURRENT_SCRATCHPAD.set(scratchpad)

    agent = build_swarm(mode)
    runner = Runner(
        app_name=settings.app_name,
        agent=agent,
        session_service=session_service,
    )

    await session_service.create_session(
        app_name=settings.app_name,
        user_id="demo-user",
        session_id=run_id,
        state={"brief": brief},
    )

    message = types.Content(
        role="user", parts=[types.Part(text=brief_to_prompt(brief))]
    )

    # Wall clock brackets the entire swarm, which is what the user actually waits for.
    started = time.perf_counter()
    final_text = ""
    try:
        async for event in runner.run_async(
            user_id="demo-user", session_id=run_id, new_message=message
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        final_text = part.text
    finally:
        telemetry.wall_clock_ms = (time.perf_counter() - started) * 1000.0

    board = await scratchpad.read_all(run_id)
    telemetry.evicted_keys = await backends.evicted_keys(backend_id)

    summary = telemetry.summary()
    summary["itinerary"] = board.get("itinerary")
    summary["final_text"] = final_text
    summary["scratchpad"] = board
    summary["llm_mode"] = mode
    summary["writes_per_step"] = WRITES_PER_STEP.get()
    summary["collaboration"] = coord.trace
    summary["budget_rounds"] = coord.round + 1
    return summary
