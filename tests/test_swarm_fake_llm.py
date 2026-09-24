"""End-to-end swarm behaviour under the deterministic model."""

from __future__ import annotations

import pytest

from app.agents.orchestrator import run_swarm
from tests.conftest import ARMS, BRIEF

EXPECTED_AGENTS = {
    "supervisor_intake",
    "destination_scout",
    "transit_agent",
    "stay_agent",
    "budget_guardrail",
    "itinerary_assembly",
    "supervisor_final",
}


@pytest.mark.parametrize("arm", ARMS)
async def test_full_swarm_runs(arm):
    r = await run_swarm(BRIEF, arm, llm_mode="fake", provider_latency_ms=0)

    fired = {s["agent"] for s in r["agent_steps"]}
    assert fired == EXPECTED_AGENTS, f"{arm} missing agents: {EXPECTED_AGENTS - fired}"
    assert r["errors"] == 0

    itinerary = r["itinerary"]
    assert itinerary is not None
    assert len(itinerary["days"]) == 4
    assert itinerary["total_estimate_usd"] > 0
    assert itinerary["flight"] is not None
    assert itinerary["stay"] is not None

    board = r["scratchpad"]
    assert {"destination_shortlist", "transit_plan", "stay_plan",
            "budget_verdict", "itinerary"} <= set(board)

    assert r["io_overhead_pct"] <= 100.0


async def test_all_arms_produce_identical_plans():
    """Workload parity: differing plans would mean differing payloads per arm."""
    plans = {}
    for arm in ARMS:
        r = await run_swarm(BRIEF, arm, llm_mode="fake", provider_latency_ms=0)
        plans[arm] = r["itinerary"]

    first = plans[ARMS[0]]
    for arm, plan in plans.items():
        assert plan["total_estimate_usd"] == first["total_estimate_usd"], (
            f"{arm} planned a different trip (${plan['total_estimate_usd']} vs "
            f"${first['total_estimate_usd']}) -- arms are not comparing equal work"
        )
        assert plan["flight"] == first["flight"]
        assert plan["stay"] == first["stay"]


async def test_budget_guardrail_retries_when_over_ceiling():
    tight = dict(BRIEF, budget_total=900)
    r = await run_swarm(tight, "valkey", llm_mode="fake", provider_latency_ms=0)

    guardrail_runs = [s for s in r["agent_steps"] if s["agent"] == "budget_guardrail"]
    assert len(guardrail_runs) >= 2, "loop did not retry on an over-budget plan"

    verdict = r["scratchpad"]["budget_verdict"]
    assert verdict["ceiling_usd"] == 900
    assert verdict["overage_usd"] > 0


async def test_generous_budget_exits_loop_immediately():
    generous = dict(BRIEF, budget_total=100_000)
    r = await run_swarm(generous, "valkey", llm_mode="fake", provider_latency_ms=0)

    guardrail_runs = [s for s in r["agent_steps"] if s["agent"] == "budget_guardrail"]
    assert len(guardrail_runs) == 1, "guardrail should escalate on the first pass"
    assert r["scratchpad"]["budget_verdict"]["within_budget"] is True


@pytest.mark.parametrize("writes", [1, 10])
async def test_write_amplification_scales_op_count(writes):
    """The sweep knob must actually change the state workload."""
    r = await run_swarm(
        BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0, writes_per_step=writes
    )
    assert r["writes_per_step"] == writes
    assert r["ops_total"] > 0

    if writes == 10:
        baseline = await run_swarm(
            BRIEF, "valkey", llm_mode="fake", provider_latency_ms=0, writes_per_step=1
        )
        assert r["ops_total"] > baseline["ops_total"], (
            "write amplification had no effect on op count"
        )
