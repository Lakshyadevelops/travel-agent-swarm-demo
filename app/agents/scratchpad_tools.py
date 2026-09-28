"""Blackboard-backed agent tools.

Each tool does real provider work, publishes its findings to the shared
scratchpad, and reads what its peers published. That traffic is the workload
being measured, and it is also genuine collaboration: every specialist's output
depends on what another specialist found.

    supervisor_intake --trip_constraints (incl. reading of the note)--> scout
    scout  --destination_shortlist (POIs + places added for wishes, base_area,
             the note's reading)--> stay, transit, budget, itinerary
    stay   --stay_plan (hotel chosen near scout's base)--> transit, budget
    transit--transit_plan (flight + airport->hotel transfer)--> budget
    budget --budget_directive (price caps)--> stay, transit   [next loop round]
    all    --> itinerary

Within the ParallelAgent fan-out, stay waits for the scout and transit waits for
stay. Waiting is signalled in-process (see blackboard.py) and followed by exactly
one store read, so the op count per run is identical on every backend.

`WRITES_PER_STEP` amplifies the number of scratchpad writes per agent step. This
is the primary sweep axis: the honest argument for an in-memory tier is not that
it wins at one write per step, but that it enables a chattier agent design that a
durable store cannot sustain.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from app.agents.blackboard import CURRENT_COORD
from app.agents.planner import plan_days
from app.agents.preferences import (
    Tailoring,
    clean_extra_places,
    describe,
    merge_reading,
    normalize_prefs,
    parse_preferences,
)
from app.agents.runtime import (
    CURRENT_BRIEF,
    CURRENT_RUN_ID,
    CURRENT_SCRATCHPAD,
    CURRENT_SEED,
    STEP_SINK,
    WRITES_PER_STEP,
)
# Category, Pace and WishPlace appear in tool signatures: ADK resolves those
# annotations to build the function declarations the model sees.
from app.agents.schemas import Category, Pace, WishPlace
from app.providers.geo import haversine_km, travel_leg
from app.providers.tools import scout_destination, search_flights, search_stays
from app.telemetry.instrument import CURRENT_AGENT

# Value-of-time used to trade flight price against duration: most travellers
# will pay something to avoid a 4-hour layover.
_USD_PER_HOUR_PER_TRAVELER = 25.0


def _emit(action: str, field: str, detail: str = "", **extra: Any) -> None:
    sink = STEP_SINK.get()
    if sink is None:
        return
    coord = CURRENT_COORD.get()
    try:
        sink.put_nowait({  # type: ignore[attr-defined]
            "type": "blackboard", "agent": CURRENT_AGENT.get(), "action": action,
            "field": field, "detail": detail,
            "round": coord.round if coord is not None else 0, **extra,
        })
    except Exception:  # noqa: BLE001 - UI streaming must never break a run
        pass


async def _publish(field: str, value: Any, detail: str = "", **extra: Any) -> None:
    """Write a finding to the blackboard, then signal any waiting peers."""
    pad = CURRENT_SCRATCHPAD.get()
    if pad is not None:
        run_id = CURRENT_RUN_ID.get()
        n = max(1, WRITES_PER_STEP.get())
        # The canonical write, then n-1 revision writes. Models an agent that
        # checkpoints intermediate reasoning rather than only its final answer.
        await pad.write(run_id, field, value)
        for i in range(1, n):
            await pad.write(run_id, f"{field}__rev{i}", value)

    coord = CURRENT_COORD.get()
    if coord is not None:
        coord.announce(field, CURRENT_AGENT.get())
    _emit("wrote", field, detail, **extra)


async def _consume(field: str) -> Any | None:
    pad = CURRENT_SCRATCHPAD.get()
    if pad is None:
        return None
    return await pad.read(CURRENT_RUN_ID.get(), field)


async def _await_field(field: str) -> Any | None:
    """Wait for a peer's publication this round, then read it exactly once."""
    coord = CURRENT_COORD.get()
    waited = 0.0
    if coord is not None:
        ok, waited = await coord.wait_for(field, CURRENT_AGENT.get())
        if not ok:
            _emit("timed_out", field, "peer did not publish in time; using latest value")
    # The read itself is streamed as a state_op; only a real wait is a signal
    # worth showing.
    if waited >= 1:
        _emit("waited", field, f"waited {waited:.0f} ms for a peer to publish",
              waited_ms=round(waited, 1))
    return await _consume(field)


def _brief() -> dict[str, Any]:
    return CURRENT_BRIEF.get()


def _selected(plan: dict | None) -> dict | None:
    if not plan:
        return None
    opts = plan.get("options", [])
    if not opts:
        return None
    return opts[min(int(plan.get("selected_index", 0)), len(opts) - 1)]


# --------------------------------------------------------------- supervisor
async def intake_tool(
    interests: list[Category] | None = None,
    avoid: list[Category] | None = None,
    pace: Pace | None = None,
    early_starts_ok: bool | None = None,
    wishes: list[str] | None = None,
) -> dict:
    """Post the brief, and your reading of the traveler's note, for the specialists.

    Args:
      interests: Categories the note asks for.
      avoid: Categories the note rules out.
      pace: How full the days should be, only if the note implies it.
      early_starts_ok: False if they don't want early mornings or sunrise starts.
      wishes: Up to 4 short phrases for specific things they want, beyond a
        category, e.g. "hill trek" or "beach day".
    """
    brief = _brief()
    # Model-supplied: validated field by field, keywords fill whatever is missing.
    prefs = merge_reading(
        brief.get("nuance", ""), interests=interests, avoid=avoid, pace=pace,
        early_starts_ok=early_starts_ok, wishes=wishes,
    )
    constraints = {
        "destination": brief.get("destination", ""),
        "origin": brief.get("origin", ""),
        "start_date": brief.get("start_date", ""),
        "nights": int(brief.get("nights", 3)),
        "travelers": int(brief.get("travelers", 1)),
        "budget_total": float(brief.get("budget_total", 0) or 0),
        **prefs,
    }
    await _publish("trip_constraints", constraints, describe(prefs))
    return constraints


# --------------------------------------------------------------- scout
async def scout_tool(extra_places: list[WishPlace] | None = None) -> dict:
    """Shortlist neighborhoods, must-see places and the best area to stay.

    Args:
      extra_places: Up to 3 real places in or near the destination for wishes or
        interests the catalog does not cover. Leave empty when it covers them.
    """
    brief = _brief()
    constraints = await _consume("trip_constraints") or {}
    prefs = normalize_prefs(constraints or parse_preferences(brief.get("nuance", "")))
    tailor = Tailoring(prefs)

    result = await scout_destination(brief.get("destination", ""), CURRENT_SEED.get())
    payload = result.model_dump()

    extras, rejected = clean_extra_places(
        extra_places, catalog=payload["pois"],
        centre=(payload["lat"], payload["lon"]), prefs=prefs,
    )
    # Wishes first, then interests; the planner fills days in this order.
    payload["pois"] = sorted(extras + payload["pois"], key=tailor.tier)
    payload["interest_matches"] = [p["name"] for p in payload["pois"] if tailor.tier(p) < 2]
    payload["added_for_you"] = [{"name": p["name"], "for": p["for_you"]} for p in extras]
    if rejected:
        payload["rejected_suggestions"] = rejected
    # The reading of the note rides along, so the budget guardrail and the
    # itinerary plan with it without another store read.
    payload["prefs"] = prefs
    payload["inputs_from"] = ["supervisor: trip_constraints"]

    base = payload["base_area"]["name"]
    detail = f"{len(payload['pois'])} places · recommends basing in {base}"
    if extras:
        detail += f" · added {len(extras)} for you"
    await _publish("destination_shortlist", payload, detail)
    return {
        "destination": payload["destination"],
        "recommended_base": base,
        "neighborhoods": [n["name"] for n in payload["neighborhoods"]],
        "places": [f"{p['name']} ({p['best_time']})" for p in payload["pois"]],
        "matched_interests": payload["interest_matches"],
        "added_for_traveler": [f"{p['name']} (for {p['for_you']})" for p in extras],
        "rejected_suggestions": [f"{r['name']}: {r['reason']}" for r in rejected],
    }


# --------------------------------------------------------------- stay
async def stays_tool() -> dict:
    """Pick lodging close to the scout's recommended base, within budget caps."""
    brief = _brief()
    nights = max(1, int(brief.get("nights", 3)))
    options = await search_stays(
        destination=brief.get("destination", ""),
        nights=nights,
        travelers=int(brief.get("travelers", 1)),
        run_id=CURRENT_SEED.get(),
    )
    # Always read the directive, even in round 0 when it is absent: a
    # conditional read would make op counts depend on the loop path.
    directive = await _consume("budget_directive") or {}
    shortlist = await _await_field("destination_shortlist") or {}

    base = shortlist.get("base_area") or {"name": "centre", "lat": 0.0, "lon": 0.0}
    cap = directive.get("max_nightly_usd")

    ranked = []
    for o in options:
        d = o.model_dump()
        d["km_to_base"] = round(haversine_km(base["lat"], base["lon"], d["lat"], d["lon"]), 2) \
            if base.get("lat") else 0.0
        # Utility: quality up, price and distance-from-the-action down.
        d["score"] = round(d["rating"] * 40 - d["nightly_usd"] * 0.25 - d["km_to_base"] * 30, 1)
        d["within_cap"] = cap is None or d["nightly_usd"] <= cap
        ranked.append(d)

    eligible = [i for i, d in enumerate(ranked) if d["within_cap"]]
    if eligible:
        idx = max(eligible, key=lambda i: ranked[i]["score"])
    else:
        idx = min(range(len(ranked)), key=lambda i: ranked[i]["nightly_usd"])
    chosen = ranked[idx]

    why = (f"Scout recommends basing in {base['name']}; {chosen['name']} is "
           f"{chosen['km_to_base']:.1f} km from the centre of the must-see places")
    if cap is not None:
        why += f", and fits the guardrail's ${cap:.0f}/night cap"

    payload = {
        "options": ranked,
        "selected_index": idx,
        "nights": nights,
        "rationale": why,
        "inputs_from": ["scout: destination_shortlist.base_area"]
                       + (["budget: budget_directive"] if directive else []),
    }
    await _publish("stay_plan", payload,
                   f"{chosen['name']} · ${chosen['nightly_usd']:.0f}/night")
    return {"selected": chosen["name"], "neighborhood": chosen["neighborhood"],
            "nightly_usd": chosen["nightly_usd"], "rationale": why}


# --------------------------------------------------------------- transit
async def flights_tool() -> dict:
    """Pick a flight and plan the airport-to-hotel transfer."""
    brief = _brief()
    travelers = max(1, int(brief.get("travelers", 1)))
    options = await search_flights(
        origin=brief.get("origin", "SFO"),
        destination=brief.get("destination", ""),
        start_date=brief.get("start_date", ""),
        travelers=travelers,
        run_id=CURRENT_SEED.get(),
    )
    directive = await _consume("budget_directive") or {}
    cap = directive.get("max_flight_usd")

    ranked = []
    for o in options:
        d = o.model_dump()
        d["value_score"] = round(
            d["price_usd"] + d["duration_hours"] * _USD_PER_HOUR_PER_TRAVELER * travelers, 2)
        d["within_cap"] = cap is None or d["price_usd"] <= cap
        ranked.append(d)
    eligible = [i for i, d in enumerate(ranked) if d["within_cap"]]
    idx = (min(eligible, key=lambda i: ranked[i]["value_score"]) if eligible
           else min(range(len(ranked)), key=lambda i: ranked[i]["price_usd"]))
    flight = ranked[idx]

    # The transfer depends on where stay chose to put the travelers.
    shortlist = await _await_field("destination_shortlist") or {}
    stay_plan = await _await_field("stay_plan") or {}
    hotel = _selected(stay_plan)

    transfer = None
    if hotel and shortlist.get("airport"):
        transfer = travel_leg(shortlist["airport"], hotel, travelers=travelers,
                              speed_factor=float(shortlist.get("speed_factor", 1.0)))

    why = (f"{flight['carrier']} balances price and {flight['duration_hours']:.1f} h "
           f"travel time ({flight['stops']} stop{'s' if flight['stops'] != 1 else ''})")
    if cap is not None:
        why += f", under the guardrail's ${cap:.0f} cap"
    if transfer:
        why += (f"; lands {flight['arrive'][-5:]}, then {transfer['distance_km']:.0f} km "
                f"by {transfer['mode']} (~{transfer['minutes']} min) to {hotel['name']}")

    payload = {
        "options": ranked,
        "selected_index": idx,
        "arrival_transfer": transfer,
        "local_transit_notes": why,
        "rationale": why,
        "inputs_from": ["stay: stay_plan (hotel location)", "scout: airport"]
                       + (["budget: budget_directive"] if directive else []),
    }
    await _publish("transit_plan", payload,
                   f"{flight['carrier']} ${flight['price_usd']:.0f}"
                   + (f" + {transfer['mode']} to hotel" if transfer else ""))
    return {"selected": flight["carrier"], "price_usd": flight["price_usd"],
            "arrival_transfer": transfer, "rationale": why}


# --------------------------------------------------------------- budget
def _next_below(values: list[float], current: float) -> float | None:
    lower = [v for v in values if v < current - 0.01]
    return max(lower) if lower else None


async def budget_tool(tool_context: ToolContext) -> dict:
    """Price the full plan (flight + stay + day-by-day ground costs) vs the ceiling.

    Does not edit peers' plans. When over budget it publishes a directive with
    price caps, and the specialists re-plan within those caps on the next round.
    """
    brief = _brief()
    ceiling = float(brief.get("budget_total", 0) or 0)
    coord = CURRENT_COORD.get()

    transit = await _consume("transit_plan") or {}
    stay = await _consume("stay_plan") or {}
    shortlist = await _consume("destination_shortlist") or {}
    # Earlier caps stay in force: a round-2 flight cut must not undo a round-1
    # hotel cap. Read unconditionally to keep op counts path-independent.
    prev_directive = await _consume("budget_directive") or {}

    flight = _selected(transit)
    hotel = _selected(stay)
    nights = max(1, int(stay.get("nights", brief.get("nights", 1))))

    flight_cost = float(flight["price_usd"]) if flight else 0.0
    stay_cost = float(hotel["nightly_usd"]) * nights if hotel else 0.0
    plan = plan_days(shortlist, hotel, flight, brief) if shortlist else {"ground_cost_usd": 0.0}
    ground = float(plan["ground_cost_usd"])

    total = round(flight_cost + stay_cost + ground, 2)
    within = ceiling <= 0 or total <= ceiling
    overage = 0.0 if within else round(total - ceiling, 2)

    breakdown = {"flight_usd": round(flight_cost, 2), "stay_usd": round(stay_cost, 2),
                 "ground_usd": round(ground, 2)}
    directive: dict[str, Any] | None = None
    exhausted = False

    if within:
        guidance = "Within budget."
    else:
        cur_night = float(hotel["nightly_usd"]) if hotel else 0.0
        cur_flight = flight_cost
        next_night = _next_below([o["nightly_usd"] for o in stay.get("options", [])], cur_night)
        next_flight = _next_below([o["price_usd"] for o in transit.get("options", [])], cur_flight)
        save_stay = (cur_night - next_night) * nights if next_night is not None else 0.0
        save_flight = cur_flight - next_flight if next_flight is not None else 0.0

        # Cut the smallest thing that closes the gap; cut both if neither alone does.
        cap_night = cap_flight = None
        if save_stay >= overage and (save_flight < overage or save_stay <= save_flight):
            cap_night = next_night
        elif save_flight >= overage:
            cap_flight = next_flight
        else:
            cap_night, cap_flight = next_night, next_flight

        last_round = coord is not None and coord.round >= coord.max_rounds - 1
        if cap_night is None and cap_flight is None:
            exhausted = True
            guidance = (f"Over ceiling by ${overage:.0f} with no cheaper inventory left. "
                        "Recommend shortening the trip or raising the budget.")
        elif last_round:
            exhausted = True
            guidance = (f"Still over ceiling by ${overage:.0f} after "
                        f"{coord.max_rounds - 1} re-plans. Recommend shortening the trip "
                        "or raising the budget.")
        else:
            parts = []
            if cap_night is not None:
                parts.append(f"stay ≤ ${cap_night:.0f}/night")
            if cap_flight is not None:
                parts.append(f"flight ≤ ${cap_flight:.0f}")
            guidance = f"Over ceiling by ${overage:.0f}. Asking specialists to re-plan: " \
                       + ", ".join(parts) + "."
            if cap_night is None:
                cap_night = prev_directive.get("max_nightly_usd")
            if cap_flight is None:
                cap_flight = prev_directive.get("max_flight_usd")
            directive = {
                "max_nightly_usd": cap_night, "max_flight_usd": cap_flight,
                "overage_usd": overage, "reason": guidance,
                "issued_round": coord.round if coord else 0,
            }

    verdict = {
        "within_budget": within,
        "total_estimate_usd": total,
        "ceiling_usd": ceiling,
        "overage_usd": overage,
        "breakdown": breakdown,
        "guidance": guidance,
        "inputs_from": ["transit: transit_plan", "stay: stay_plan",
                        "scout: destination_shortlist (priced day by day)"],
    }
    if ceiling <= 0:
        verdict_detail = f"${total:,.0f} total"
    elif within:
        verdict_detail = f"${total:,.0f} total · within your ${ceiling:,.0f} budget"
    else:
        verdict_detail = (f"${total:,.0f} total · ${overage:,.0f} over your "
                          f"${ceiling:,.0f} budget")
    await _publish("budget_verdict", verdict, verdict_detail,
                   within_budget=within, total_usd=total, ceiling_usd=ceiling,
                   overage_usd=overage)

    if directive is not None:
        # Bump the round first so the specialists' round-2 waits cannot be
        # satisfied by round-1 publications.
        if coord is not None:
            coord.next_round()
        await _publish("budget_directive", directive, guidance,
                       overage_usd=overage,
                       max_rounds=coord.max_rounds if coord is not None else 1)

    # Breaking the LoopAgent is the guardrail's decision, not the model's.
    if within or exhausted:
        tool_context.actions.escalate = True

    return verdict


# --------------------------------------------------------------- itinerary
async def itinerary_tool() -> dict:
    """Weave approved transit, stay and places into a timed day-by-day plan."""
    brief = _brief()
    pad = CURRENT_SCRATCHPAD.get()
    board: dict[str, Any] = {}
    if pad is not None:
        board = await pad.read_all(CURRENT_RUN_ID.get())

    shortlist = board.get("destination_shortlist", {}) or {}
    transit = board.get("transit_plan", {}) or {}
    stay = board.get("stay_plan", {}) or {}
    verdict = board.get("budget_verdict", {}) or {}

    flight = _selected(transit)
    hotel = _selected(stay)
    # Same inputs as the budget guardrail's call -- including the reading of
    # the note carried in the shortlist -- so the totals match exactly.
    plan = plan_days(shortlist, hotel, flight, brief) if shortlist else {"days": []}
    tailored = plan.get("tailored") or {}

    scout_line = f"Recommended basing in {(shortlist.get('base_area') or {}).get('name', '?')}"
    added = shortlist.get("added_for_you") or []
    if added:
        scout_line += "; added " + ", ".join(f"{a['name']} (for {a['for']})" for a in added)
    added_names = {a["name"] for a in added}
    matched = [n for n in shortlist.get("interest_matches") or [] if n not in added_names]
    if matched:
        scout_line += f"; prioritised {', '.join(matched[:3])}"

    itinerary = {
        "destination": shortlist.get("destination", brief.get("destination", "")),
        "summary": shortlist.get("season_summary", ""),
        "base_area": (shortlist.get("base_area") or {}).get("name"),
        "days": plan.get("days", []),
        "unscheduled": plan.get("unscheduled", []),
        "tailored": tailored,
        "flight": flight,
        "stay": hotel,
        "arrival_transfer": transit.get("arrival_transfer"),
        "total_estimate_usd": verdict.get("total_estimate_usd", 0.0),
        "breakdown": verdict.get("breakdown", {}),
        "within_budget": verdict.get("within_budget", True),
        "budget_guidance": verdict.get("guidance", ""),
        "collaboration": {
            "scout": scout_line,
            "stay": stay.get("rationale", ""),
            "transit": transit.get("rationale", ""),
            "budget": verdict.get("guidance", ""),
        },
    }
    await _publish("itinerary", itinerary, f"{len(itinerary['days'])} days planned")
    out: dict[str, Any] = {
        "days": [{"day": d["day"], "title": d["title"], "est_cost_usd": d["est_cost_usd"]}
                 for d in itinerary["days"]],
        "total_estimate_usd": itinerary["total_estimate_usd"],
        "within_budget": itinerary["within_budget"],
    }
    if tailored.get("note"):
        # For the supervisor's closing summary: what the note changed.
        out["tailored_to_note"] = {
            "picked_for_traveler": [
                f"{x['name']} (day {x['day']}, {x['reason']}"
                + (", added by the scout)" if x["added"] else ")")
                for x in tailored.get("for_you", [])
            ],
            "left_out_as_asked": [f"{s['name']} ({s['reason']})"
                                  for s in tailored.get("skipped", [])],
            "could_not_include": [f"{u['what']} ({u['reason']})"
                                  for u in tailored.get("unmet", [])],
        }
    return out


async def read_scratchpad(field: str) -> dict:
    """Read another agent's finding from the shared blackboard."""
    value = await _consume(field)
    return {"field": field, "value": value}
