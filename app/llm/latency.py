"""LLM latency model: realistic end-to-end timing without calling the API.

Load-testing a multi-agent app against the real model is a non-starter: every
virtual user costs ~10-20 model calls per session, so a few hundred users
would burn a day's quota in minutes -- and you'd be benchmarking Gemini's rate
limiter, not your state layer.

Instead we RECORD real model latency once, then REPLAY it:

  1. Calibrate: run the real swarm a handful of times (`scripts/calibrate_llm.py`).
     Every agent invocation's model time is appended to `runs/llm_trace.jsonl`,
     split into `tool_ms` (thinking up to the tool call) and `final_ms` (writing
     the closing message after the tool result).
  2. Replay: the deterministic FakeLlm sleeps for a sampled (tool_ms, final_ms)
     PAIR from the same agent role before each response. Pairs are sampled
     together so the correlation between the two halves is preserved.

The storage layer sees exactly the traffic, timing and concurrency it would see
in production; the only thing not exercised is Gemini itself. If no trace
exists yet, a lognormal fit to measured p50/p95 is used and flagged as such.
"""

from __future__ import annotations

import json
import math
import random
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT, RUNS_DIR

TRACE_PATH = RUNS_DIR / "llm_trace.jsonl"
COMMITTED_TRACE_PATH = REPO_ROOT / "results" / "llm_trace.jsonl"

# Fallback when no trace exists: gemini-3.5-flash probe, n=12, 0 failures.
FALLBACK_P50_MS = 6200.0
FALLBACK_P95_MS = 13400.0


@dataclass
class LatencyModel:
    # role -> list of (tool_ms, final_ms) pairs from real invocations
    pairs: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    source: str = "none"
    scale: float = 1.0
    lognormal: tuple[float, float] | None = None  # (mu, sigma) of per-call ms

    def sample(self, rng: random.Random, role: str) -> tuple[float, float]:
        """(tool_ms, final_ms) for one agent invocation."""
        pool = self.pairs.get(role) or self.pairs.get("*")
        if pool:
            t, f = rng.choice(pool)
        elif self.lognormal:
            mu, sigma = self.lognormal
            t, f = rng.lognormvariate(mu, sigma), rng.lognormvariate(mu, sigma)
        else:
            t, f = 0.0, 0.0
        return t * self.scale, f * self.scale

    def describe(self) -> dict[str, Any]:
        n = sum(len(v) for k, v in self.pairs.items() if k != "*")
        per_role = {}
        for role, pool in self.pairs.items():
            if role == "*":
                continue
            totals = sorted(t + f for t, f in pool)
            per_role[role] = {
                "n": len(pool),
                "p50_ms": round(totals[len(totals) // 2], 1),
                "max_ms": round(totals[-1], 1),
            }
        return {"source": self.source, "scale": self.scale, "invocations": n,
                "per_role": per_role}

    # ---- constructors ----------------------------------------------------
    @classmethod
    def from_trace(cls, path: Path = TRACE_PATH, scale: float = 1.0) -> "LatencyModel":
        pairs: dict[str, list[tuple[float, float]]] = {}
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("error"):
                continue
            pair = (float(row["tool_ms"]), float(row["final_ms"]))
            pairs.setdefault(row["role"], []).append(pair)
            pairs.setdefault("*", []).append(pair)
        if not pairs:
            raise ValueError(f"no usable rows in {path}")
        return cls(pairs=pairs, source=f"trace:{path.name}", scale=scale)

    @classmethod
    def from_percentiles(cls, p50_ms: float, p95_ms: float, scale: float = 1.0) -> "LatencyModel":
        mu = math.log(p50_ms)
        sigma = math.log(p95_ms / p50_ms) / 1.645
        return cls(source=f"lognormal(p50={p50_ms:.0f},p95={p95_ms:.0f})",
                   scale=scale, lognormal=(mu, sigma))

    @classmethod
    def default(cls, scale: float = 1.0) -> "LatencyModel":
        # Local recordings first, then the trace committed with the repo, so a
        # fresh clone can load-test without ever touching the API.
        for path in (TRACE_PATH, COMMITTED_TRACE_PATH):
            if path.exists():
                try:
                    return cls.from_trace(path, scale)
                except ValueError:
                    pass
        return cls.from_percentiles(FALLBACK_P50_MS, FALLBACK_P95_MS, scale)


# (model, session seed). The seed comes from the session's position in the
# workload, never the arm, so every arm replays the identical latency sequence.
CURRENT_LLM_LATENCY: ContextVar[tuple[LatencyModel, str] | None] = ContextVar(
    "current_llm_latency", default=None
)
