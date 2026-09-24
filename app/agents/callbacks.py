"""Agent callbacks: role attribution and live step logging."""

from __future__ import annotations

import time
from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from app.agents.runtime import STEP_SINK
from app.telemetry.instrument import CURRENT_AGENT, CURRENT_RUN

# Maps ADK agent names to the short role recorded on every StateOp.
AGENT_ROLES: dict[str, str] = {
    "supervisor_intake": "supervisor",
    "destination_scout": "scout",
    "transit_agent": "transit",
    "stay_agent": "stay",
    "budget_guardrail": "budget",
    "itinerary_assembly": "itinerary",
    "supervisor_final": "supervisor",
}

_STARTS: dict[str, float] = {}


def _emit(event: dict[str, Any]) -> None:
    sink = STEP_SINK.get()
    if sink is not None:
        try:
            sink.put_nowait(event)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - UI streaming must never break a run
            pass


def make_before_callback(agent_name: str, label: str):
    def before_agent(callback_context: CallbackContext) -> Optional[types.Content]:
        role = AGENT_ROLES.get(agent_name, agent_name)
        CURRENT_AGENT.set(role)
        _STARTS[agent_name] = time.perf_counter()
        _emit({"type": "agent_step", "phase": "start", "agent": agent_name,
               "label": label, "role": role})
        return None

    return before_agent


def make_after_callback(agent_name: str, label: str):
    def after_agent(callback_context: CallbackContext) -> Optional[types.Content]:
        role = AGENT_ROLES.get(agent_name, agent_name)
        started = _STARTS.pop(agent_name, None)
        elapsed_ms = (time.perf_counter() - started) * 1000.0 if started else 0.0

        run = CURRENT_RUN.get()
        if run is not None:
            run.agent_steps.append(
                {"agent": agent_name, "label": label, "role": role,
                 "duration_ms": round(elapsed_ms, 2)}
            )

        _emit({"type": "agent_step", "phase": "end", "agent": agent_name,
               "label": label, "role": role, "duration_ms": round(elapsed_ms, 2)})
        CURRENT_AGENT.set("runner")
        return None

    return after_agent
