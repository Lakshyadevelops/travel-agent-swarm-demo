"""Agents must actually use each other's findings, not work in isolation."""

from __future__ import annotations

import pytest

from app.agents.orchestrator import run_swarm
from app.agents.planner import plan_days
from app.providers.geo import haversine_km
from tests.conftest import ARMS, BRIEF


async def test_stay_is_chosen_near_scouts_base():
    r = await run_swarm(BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0)
    board = r["scratchpad"]
    base = board["destination_shortlist"]["base_area"]
    stay = board["stay_plan"]
    chosen = stay["options"][stay["selected_index"]]

    assert "scout: destination_shortlist.base_area" in stay["inputs_from"]
    # The chosen stay is at least as close to the base as the median option.
    dists = sorted(o["km_to_base"] for o in stay["options"])
    assert chosen["km_to_base"] <= dists[len(dists) // 2]
    assert chosen["km_to_base"] == pytest.approx(
        haversine_km(base["lat"], base["lon"], chosen["lat"], chosen["lon"]), abs=0.01)


async def test_transit_plans_transfer_to_the_chosen_stay():
    r = await run_swarm(BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0)
    board = r["scratchpad"]
    stay = board["stay_plan"]["options"][board["stay_plan"]["selected_index"]]
    transfer = board["transit_plan"]["arrival_transfer"]
    assert transfer["to"] == stay["name"]
    assert transfer["distance_km"] > 0 and transfer["minutes"] > 0


async def test_parallel_agents_wait_on_each_other():
    r = await run_swarm(BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0)
    waits = {(t["agent"], t["field"]) for t in r["collaboration"] if t["action"] == "waited"}
    assert ("stay", "destination_shortlist") in waits
    assert ("transit", "stay_plan") in waits
    assert not [t for t in r["collaboration"] if t["action"] == "timed_out"]


async def test_budget_directive_caps_the_next_round():
    """Over budget -> guardrail issues caps -> specialists re-plan under them."""
    # A ceiling just below the unconstrained plan forces exactly one downshift.
    free = await run_swarm(dict(BRIEF, budget_total=100_000), "valkey",
                           llm_mode="fake", provider_latency_ms=0)
    ceiling = free["scratchpad"]["budget_verdict"]["total_estimate_usd"] - 50
    r = await run_swarm(dict(BRIEF, budget_total=ceiling), "valkey",
                        llm_mode="fake", provider_latency_ms=0)
    board = r["scratchpad"]

    directive = board["budget_directive"]
    assert r["budget_rounds"] >= 2
    stay = board["stay_plan"]["options"][board["stay_plan"]["selected_index"]]
    flight = board["transit_plan"]["options"][board["transit_plan"]["selected_index"]]
    if directive["max_nightly_usd"] is not None:
        assert stay["nightly_usd"] <= directive["max_nightly_usd"]
        assert "budget: budget_directive" in board["stay_plan"]["inputs_from"]
    if directive["max_flight_usd"] is not None:
        assert flight["price_usd"] <= directive["max_flight_usd"]
    assert board["budget_verdict"]["total_estimate_usd"] < free["scratchpad"][
        "budget_verdict"]["total_estimate_usd"]


async def test_itinerary_total_matches_budget_verdict():
    r = await run_swarm(BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0)
    board = r["scratchpad"]
    it = board["itinerary"]
    v = board["budget_verdict"]
    assert it["total_estimate_usd"] == v["total_estimate_usd"]
    ground = sum(d["est_cost_usd"] for d in it["days"])
    assert v["breakdown"]["ground_usd"] == pytest.approx(ground, abs=1.0)


async def test_collaboration_keeps_op_counts_identical_across_arms():
    """Waits are signalled in-process, so timing must not change store traffic."""
    counts = {}
    for arm in ARMS:
        r = await run_swarm(BRIEF, arm, llm_mode="fake", provider_latency_ms=0)
        counts[arm] = {op: v["n"] for op, v in r["ops_by_type"].items()}
    assert counts["valkey"] == counts["postgres"]


async def test_day_plan_has_timed_stops_with_legs():
    from app.providers.tools import scout_destination

    shortlist = (await scout_destination("Lisbon", "seed")).model_dump()
    brief = dict(BRIEF, nights=4)
    out = plan_days(shortlist, None, {"arrive": "2026-10-10 13:30", "carrier": "X"}, brief)

    stops = [i for d in out["days"] for i in d["items"] if i["kind"] == "stop"]
    assert stops, "no stops scheduled"
    for s in stops:
        assert s["leg"]["distance_km"] >= 0 and s["leg"]["minutes"] >= 0
        assert s["start"] < s["end"] or s["end"] < "04:00"
        assert s["why_this_time"]
    # Sunset stops land in the golden-hour window, not at noon.
    for d in out["days"]:
        for i in d["items"]:
            if i.get("best_time") == "sunset" and "after sunset" not in i["why_this_time"]:
                assert i["start"] >= "15:00"
