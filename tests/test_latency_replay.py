"""Replayed LLM latency must be identical across arms, or load-test pairs are invalid."""

from __future__ import annotations

from app.agents.orchestrator import run_swarm
from app.llm.latency import CURRENT_LLM_LATENCY, LatencyModel
from tests.conftest import BRIEF


class RecordingModel(LatencyModel):
    def sample(self, rng, role):  # type: ignore[override]
        t, f = super().sample(rng, role)
        self.log.append((role, round(t, 3), round(f, 3)))
        return t, f


async def _samples(arm: str) -> list[tuple[str, float, float]]:
    m = RecordingModel(pairs={"*": [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0)]},
                       source="test")
    m.log = []  # type: ignore[attr-defined]
    CURRENT_LLM_LATENCY.set((m, "llm:7:3"))
    try:
        await run_swarm(BRIEF, arm, llm_mode="fake", provider_latency_ms=0)
    finally:
        CURRENT_LLM_LATENCY.set(None)
    return sorted(m.log)  # type: ignore[attr-defined]


async def test_latency_replay_is_identical_across_arms():
    a = await _samples("valkey")
    b = await _samples("postgres")
    assert a, "latency model was never consulted"
    assert a == b


def test_trace_model_samples_same_role():
    m = LatencyModel(pairs={"stay_agent": [(10.0, 20.0)], "*": [(1.0, 1.0)]})
    import random
    assert m.sample(random.Random(0), "stay_agent") == (10.0, 20.0)
    assert m.sample(random.Random(0), "unknown") == (1.0, 1.0)
