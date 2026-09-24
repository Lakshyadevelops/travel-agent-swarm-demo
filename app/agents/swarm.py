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
from google.adk.tools import FunctionTool

from app.agents import prompts
from app.agents.callbacks import make_after_callback, make_before_callback
from app.agents.scratchpad_tools import (
    budget_tool,
    flights_tool,
    itinerary_tool,
    scout_tool,
    stays_tool,
)
from app.config import settings
from app.llm.fake_llm import FakeLlm

# name -> (label, instruction, tool, closing line used by FakeLlm)
AGENT_SPECS = {
    "supervisor_intake": (
        "Supervisor · intake", prompts.SUPERVISOR_INTAKE, None,
        "Brief understood. Dispatching the specialists.",
    ),
    "destination_scout": (
        "Destination & Vibe Scout", prompts.SCOUT, scout_tool,
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
        "Your trip is planned and within the numbers you gave me.",
    ),
}


def _model_for(agent_name: str, llm_mode: str):
    """Real Gemini, or the deterministic stub, behind an identical graph."""
    label, instruction, tool, closing = AGENT_SPECS[agent_name]
    if llm_mode == "gemini":
        return settings.gemini_model
    return FakeLlm(
        role=agent_name,
        tool_name=tool.__name__ if tool else None,
        closing=closing,
    )


def _build_agent(agent_name: str, llm_mode: str) -> LlmAgent:
    label, instruction, tool, _closing = AGENT_SPECS[agent_name]
    return LlmAgent(
        name=agent_name,
        description=label,
        model=_model_for(agent_name, llm_mode),
        instruction=instruction,
        tools=[FunctionTool(tool)] if tool else [],
        before_agent_callback=make_before_callback(agent_name, label),
        after_agent_callback=make_after_callback(agent_name, label),
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
        max_iterations=3,
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
