"""Blackboard-backed agent tools.

Each tool does real provider work, publishes its findings to the shared
scratchpad, and (where relevant) reads what its peers published. That traffic is
the workload being measured.

`WRITES_PER_STEP` amplifies the number of scratchpad writes per agent step. This
is the primary sweep axis: the honest argument for an in-memory tier is not that
it wins at one write per step, but that it enables a chattier agent design that a
durable store cannot sustain.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from app.agents.runtime import (
    CURRENT_BRIEF,
    CURRENT_RUN_ID,
    CURRENT_SCRATCHPAD,
    CURRENT_SEED,
    WRITES_PER_STEP,
)
from app.providers.tools import scout_destination, search_flights, search_stays


async def _publish(field: str, value: Any) -> None:
    """Write a finding to the blackboard, amplified by the sweep factor."""
    pad = CURRENT_SCRATCHPAD.get()
    if pad is None:
        return
    run_id = CURRENT_RUN_ID.get()
    n = max(1, WRITES_PER_STEP.get())

    # The canonical write, then n-1 revision writes. Models an agent that
    # checkpoints intermediate reasoning rather than only its final answer.
    await pad.write(run_id, field, value)
    for i in range(1, n):
        await pad.write(run_id, f"{field}__rev{i}", value)


async def _consume(field: str) -> Any | None:
    pad = CURRENT_SCRATCHPAD.get()
    if pad is None:
        return None
    return await pad.read(CURRENT_RUN_ID.get(), field)


async def scout_tool() -> dict:
    """Shortlist neighborhoods and seasonal highlights for the destination."""
    brief = CURRENT_BRIEF.get()
    result = await scout_destination(brief.get("destination", ""), CURRENT_SEED.get())
    payload = result.model_dump()
    await _publish("destination_shortlist", payload)
    return payload


async def flights_tool() -> dict:
    """Find flight and transit options into the destination."""
    brief = CURRENT_BRIEF.get()
    options = await search_flights(
        origin=brief.get("origin", "SFO"),
        destination=brief.get("destination", ""),
        start_date=brief.get("start_date", ""),
        travelers=int(brief.get("travelers", 1)),
        run_id=CURRENT_SEED.get(),
    )
    payload = {
        "options": [o.model_dump() for o in options],
        "selected_index": 0,
        "local_transit_notes": "Airport rail link runs every 20 minutes into the centre.",
    }
    await _publish("transit_plan", payload)
    return payload


async def stays_tool() -> dict:
    """Find accommodation matching the brief."""
    brief = CURRENT_BRIEF.get()
    nights = max(1, int(brief.get("nights", 3)))
    options = await search_stays(
        destination=brief.get("destination", ""),
        nights=nights,
        travelers=int(brief.get("travelers", 1)),
        run_id=CURRENT_SEED.get(),
    )
    payload = {
        "options": [o.model_dump() for o in options],
        "selected_index": 0,
        "nights": nights,
    }
    await _publish("stay_plan", payload)
    return payload


async def budget_tool(tool_context: ToolContext) -> dict:
    """Cross-reference committed costs against the spending ceiling.

    Reads peers' findings off the blackboard rather than taking them as
    arguments -- that read traffic is part of the workload being measured.
    """
    brief = CURRENT_BRIEF.get()
    ceiling = float(brief.get("budget_total", 0) or 0)

    transit = await _consume("transit_plan") or {}
    stay = await _consume("stay_plan") or {}

    flight_opts = transit.get("options", [])
    stay_opts = stay.get("options", [])
    f_idx = min(transit.get("selected_index", 0), max(0, len(flight_opts) - 1))
    s_idx = min(stay.get("selected_index", 0), max(0, len(stay_opts) - 1))

    flight_cost = flight_opts[f_idx]["price_usd"] if flight_opts else 0.0
    nights = max(1, int(stay.get("nights", 1)))
    stay_cost = stay_opts[s_idx]["nightly_usd"] * nights if stay_opts else 0.0
    activities = 60.0 * nights * max(1, int(brief.get("travelers", 1)))

    total = round(flight_cost + stay_cost + activities, 2)
    within = ceiling <= 0 or total <= ceiling
    overage = 0.0 if within else round(total - ceiling, 2)

    # Downshift to cheaper options on the retry rather than just reporting failure.
    if not within:
        if len(stay_opts) > s_idx + 1 or len(flight_opts) > f_idx + 1:
            stay["selected_index"] = min(s_idx + 1, max(0, len(stay_opts) - 1))
            transit["selected_index"] = min(f_idx + 1, max(0, len(flight_opts) - 1))
            await _publish("stay_plan", stay)
            await _publish("transit_plan", transit)
            guidance = (
                f"Over ceiling by ${overage:.0f}. Downshifted to cheaper flight and "
                "stay options; re-evaluate."
            )
        else:
            guidance = (
                f"Over ceiling by ${overage:.0f} with no cheaper inventory left. "
                "Recommend shortening the trip or raising the budget."
            )
    else:
        guidance = "Within budget."

    verdict = {
        "within_budget": within,
        "total_estimate_usd": total,
        "ceiling_usd": ceiling,
        "overage_usd": overage,
        "guidance": guidance,
    }
    await _publish("budget_verdict", verdict)

    # Breaking the LoopAgent is the guardrail's decision, not the model's.
    # Escalate when the plan fits, or when retrying cannot help because there is
    # no cheaper inventory left -- otherwise we'd burn iterations re-deriving the
    # same over-budget answer.
    exhausted = not within and "no cheaper inventory" in guidance
    if within or exhausted:
        tool_context.actions.escalate = True

    return verdict


async def itinerary_tool() -> dict:
    """Weave approved transit, stay and activities into a day-by-day timeline."""
    brief = CURRENT_BRIEF.get()
    pad = CURRENT_SCRATCHPAD.get()
    board: dict[str, Any] = {}
    if pad is not None:
        board = await pad.read_all(CURRENT_RUN_ID.get())

    shortlist = board.get("destination_shortlist", {}) or {}
    transit = board.get("transit_plan", {}) or {}
    stay = board.get("stay_plan", {}) or {}
    verdict = board.get("budget_verdict", {}) or {}

    flight_opts = transit.get("options", [])
    stay_opts = stay.get("options", [])
    f_idx = min(transit.get("selected_index", 0), max(0, len(flight_opts) - 1))
    s_idx = min(stay.get("selected_index", 0), max(0, len(stay_opts) - 1))
    flight = flight_opts[f_idx] if flight_opts else None
    chosen_stay = stay_opts[s_idx] if stay_opts else None

    activities = list(shortlist.get("signature_activities", []))
    neighborhoods = shortlist.get("neighborhoods", [])
    nights = max(1, int(stay.get("nights", 3)))

    days = []
    for d in range(nights):
        hood = neighborhoods[d % len(neighborhoods)]["name"] if neighborhoods else "the centre"
        days.append(
            {
                "day": d + 1,
                "date": "",
                "morning": activities[(d * 2) % len(activities)] if activities else f"Explore {hood}",
                "afternoon": f"Wander {hood}",
                "evening": activities[(d * 2 + 1) % len(activities)] if activities else "Dinner locally",
                "est_cost_usd": round(60.0 * max(1, int(brief.get("travelers", 1))), 2),
            }
        )

    itinerary = {
        "destination": shortlist.get("destination", brief.get("destination", "")),
        "summary": shortlist.get("season_summary", ""),
        "days": days,
        "flight": flight,
        "stay": chosen_stay,
        "total_estimate_usd": verdict.get("total_estimate_usd", 0.0),
        "within_budget": verdict.get("within_budget", True),
    }
    await _publish("itinerary", itinerary)
    return itinerary


async def read_scratchpad(field: str) -> dict:
    """Read another agent's finding from the shared blackboard."""
    value = await _consume(field)
    return {"field": field, "value": value}
