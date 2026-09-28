"""Per-invocation runtime context for the agent swarm.

Tools need the active scratchpad, run id and sweep settings. Threading those
through ADK's tool signatures would leak benchmark plumbing into the agent API,
so they travel via contextvars instead.
"""

from __future__ import annotations

from contextvars import ContextVar

from app.state.base import ScratchpadStore

CURRENT_SCRATCHPAD: ContextVar[ScratchpadStore | None] = ContextVar(
    "current_scratchpad", default=None
)
CURRENT_RUN_ID: ContextVar[str] = ContextVar("current_run_id", default="")
CURRENT_BRIEF: ContextVar[dict] = ContextVar("current_brief", default={})

# Seed for the mock providers. Deliberately NOT derived from run_id: run ids
# embed the backend name, which would hand each arm a different workload (and
# therefore different payload sizes) and quietly invalidate the comparison.
# Derived from the brief instead, so every arm plans byte-identical data.
CURRENT_SEED: ContextVar[str] = ContextVar("current_seed", default="default")

# Primary benchmark sweep axis. Controlled here rather than by LLM whim so that
# every iteration issues an identical number of state operations -- otherwise the
# measurement would vary with the model's mood.
WRITES_PER_STEP: ContextVar[int] = ContextVar("writes_per_step", default=1)

# Emits step events to the SSE stream feeding the UI's live execution log.
STEP_SINK: ContextVar[object | None] = ContextVar("step_sink", default=None)

# True on the live model: specialists research the destination with Google
# Search (providers/research.py). False for the scripted model (benchmarks and
# tests), which plans from the curated catalog with no network calls. Either
# way the tools issue the same store operations.
LIVE_RESEARCH: ContextVar[bool] = ContextVar("live_research", default=False)
