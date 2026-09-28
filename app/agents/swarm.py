"""Six-agent swarm composition.

    SequentialAgent
      1. supervisor_intake
      2. LoopAgent (max 3)
           ParallelAgent [scout | transit | stay]   <- genuinely concurrent
           budget_guardrail                          <- escalates to exit
      3. itinerary_assembly
      4. supervisor_final

The ParallelAgent fan-out is what makes this a meaningful state-layer workload:
three agents writing to the blackboard simultaneously is the concurrency the
storage comparison is actually about.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.models.google_llm import Gemini
from google.adk.tools import FunctionTool
from google.genai import types

from app.agents import prompts
from app.agents.callbacks import (
    make_after_callback,
    make_before_callback,
    make_model_callbacks,
)
from app.agents.scratchpad_tools import (
    budget_tool,
    flights_tool,
    intake_tool,
    itinerary_tool,
    scout_tool,
    stays_tool,
)
from app.config import settings
from app.llm.fake_llm import FakeLlm

PLAN_LOOP_MAX_ROUNDS = 3

# name -> (label, instruction, tool, closing line used by FakeLlm). An
# instruction is text, or an ADK InstructionProvider built per run.
AGENT_SPECS = {
    "supervisor_intake": (
        "Supervisor · intake", prompts.SUPERVISOR_INTAKE, intake_tool,
        "Brief understood. Dispatching the specialists.",
    ),
    "destination_scout": (
        "Destination & Vibe Scout", prompts.scout_instruction, scout_tool,
        "Shortlisted the neighborhoods worth basing in this season.",
    ),
    "transit_agent": (
        "Flight & Transit", prompts.TRANSIT, flights_tool,
        "Routing options retrieved and ranked by price.",
    ),
    "stay_agent": (
        "Accommodation & Stay", prompts.STAY, stays_tool,
        "Lodging options sourced for the stated dates.",
    ),
    "budget_guardrail": (
        "Budget Guardrail", prompts.BUDGET, budget_tool,
        "Costs cross-referenced against the ceiling.",
    ),
    "itinerary_assembly": (
        "Itinerary Assembly", prompts.ITINERARY, itinerary_tool,
        "Day-by-day timeline assembled.",
    ),
    "supervisor_final": (
        "Supervisor · finalize", prompts.SUPERVISOR_FINAL, None,
        "Your trip plan is ready.",
    ),
}


def _model_for(agent_name: str, llm_mode: str):
    """Real Gemini, or the deterministic stub, behind an identical graph."""
    label, instruction, tool, closing = AGENT_SPECS[agent_name]
    if llm_mode == "gemini":
        # Retry transient overload errors (503 UNAVAILABLE, 429, 5xx) with
        # exponential backoff instead of failing the agent mid-plan.
        return Gemini(
            model=settings.gemini_model,
            retry_options=types.HttpRetryOptions(
                attempts=5, initial_delay=1.0, max_delay=16.0, exp_base=2.0,
                jitter=0.5, http_status_codes=[429, 500, 502, 503, 504],
            ),
        )
    return FakeLlm(
        role=agent_name,
        tool_name=tool.__name__ if tool else None,
        closing=closing,
    )


def _build_agent(agent_name: str, llm_mode: str) -> LlmAgent:
    label, instruction, tool, _closing = AGENT_SPECS[agent_name]
    extra = {}
    if llm_mode == "gemini":
        # Record real model latency so load tests can replay it without the API.
        before_model, after_model = make_model_callbacks(agent_name, settings.gemini_model)
        extra = {"before_model_callback": before_model, "after_model_callback": after_model}
    return LlmAgent(
        name=agent_name,
        description=label,
        model=_model_for(agent_name, llm_mode),
        instruction=instruction,
        tools=[FunctionTool(tool)] if tool else [],
        before_agent_callback=make_before_callback(agent_name, label),
        after_agent_callback=make_after_callback(agent_name, label),
        **extra,
    )


def build_swarm(llm_mode: str | None = None) -> SequentialAgent:
    mode = llm_mode or settings.llm_mode

    research_fanout = ParallelAgent(
        name="research_fanout",
        description="Concurrent destination, transit and lodging research",
        sub_agents=[
            _build_agent("destination_scout", mode),
            _build_agent("transit_agent", mode),
            _build_agent("stay_agent", mode),
        ],
    )

    plan_loop = LoopAgent(
        name="plan_loop",
        description="Research and budget-check until the plan fits the ceiling",
        max_iterations=PLAN_LOOP_MAX_ROUNDS,
        sub_agents=[research_fanout, _build_agent("budget_guardrail", mode)],
    )

    return SequentialAgent(
        name="travel_swarm",
        description="Six-agent travel concierge swarm",
        sub_agents=[
            _build_agent("supervisor_intake", mode),
            plan_loop,
            _build_agent("itinerary_assembly", mode),
            _build_agent("supervisor_final", mode),
        ],
    )
