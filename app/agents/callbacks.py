"""Agent callbacks: role attribution, live step logging, LLM latency tracing."""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.agents.runtime import CURRENT_RUN_ID, STEP_SINK
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

# Keyed by (run_id, agent) -- a bare agent-name key would let concurrent
# sessions overwrite each other's start times under load.
_STARTS: dict[tuple[str, str], float] = {}
# Per-invocation LLM timing accumulator for the latency trace (gemini mode).
_LLM: dict[tuple[str, str], dict[str, Any]] = {}


def _key(agent_name: str) -> tuple[str, str]:
    return (CURRENT_RUN_ID.get() or "", agent_name)


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
        _STARTS[_key(agent_name)] = time.perf_counter()
        _emit({"type": "agent_step", "phase": "start", "agent": agent_name,
               "label": label, "role": role})
        return None

    return before_agent


def make_after_callback(agent_name: str, label: str):
    def after_agent(callback_context: CallbackContext) -> Optional[types.Content]:
        role = AGENT_ROLES.get(agent_name, agent_name)
        started = _STARTS.pop(_key(agent_name), None)
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
        _flush_llm_trace(agent_name)
        return None

    return after_agent


# ---- LLM latency trace (real-model runs only) ----------------------------
# Only calibration (scripts/calibrate_llm.py) records. Demo runs research with
# Google Search, retry busy answers and plan any destination, so their model
# times say nothing about the catalog workload the load tests replay; letting
# them append would make benchmark results drift with demo use.
RECORD_LLM_TRACE: ContextVar[bool] = ContextVar("record_llm_trace", default=False)


def make_model_callbacks(agent_name: str, model_name: str):
    """Time every real model call, split by whether it produced a tool call.

    When RECORD_LLM_TRACE is on, one row per agent invocation lands in
    runs/llm_trace.jsonl; the load generator replays these instead of calling
    the API (see llm/latency.py).
    """

    def before_model(callback_context: CallbackContext, llm_request: LlmRequest):
        acc = _LLM.setdefault(_key(agent_name), {"tool_ms": 0.0, "final_ms": 0.0,
                                                  "calls": 0, "error": None})
        acc["_t0"] = time.perf_counter()
        return None

    def after_model(callback_context: CallbackContext, llm_response: LlmResponse):
        acc = _LLM.get(_key(agent_name))
        if acc is None or "_t0" not in acc:
            return None
        ms = (time.perf_counter() - acc.pop("_t0")) * 1000.0
        parts = (llm_response.content.parts if llm_response.content else None) or []
        is_tool = any(getattr(p, "function_call", None) for p in parts)
        acc["tool_ms" if is_tool else "final_ms"] += ms
        acc["calls"] += 1
        acc["model"] = model_name
        if llm_response.error_code:
            acc["error"] = str(llm_response.error_code)
        return None

    return before_model, after_model


def _flush_llm_trace(agent_name: str) -> None:
    acc = _LLM.pop(_key(agent_name), None)
    if not acc or not acc.get("calls") or not RECORD_LLM_TRACE.get():
        return
    from app.llm.latency import TRACE_PATH  # local: avoid import cycle at startup

    row = {
        "ts": time.time(),
        "run_id": CURRENT_RUN_ID.get(),
        "role": agent_name,
        "model": acc.get("model"),
        "calls": acc["calls"],
        "tool_ms": round(acc["tool_ms"], 1),
        "final_ms": round(acc["final_ms"], 1),
        "error": acc.get("error"),
    }
    try:
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRACE_PATH.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
