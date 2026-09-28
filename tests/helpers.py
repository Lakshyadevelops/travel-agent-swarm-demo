"""Shared test helpers."""

from __future__ import annotations

import json
from typing import Any

from app.agents.runtime import CURRENT_BRIEF, CURRENT_RUN_ID, CURRENT_SCRATCHPAD, CURRENT_SEED
from app.providers.tools import PROVIDER_LATENCY_MS


class MemPad:
    """Just enough of a ScratchpadStore; values round-trip through JSON like the real ones."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    async def write(self, run_id: str, field: str, value: Any) -> None:
        self.rows[(run_id, field)] = json.dumps(value)

    async def read(self, run_id: str, field: str) -> Any:
        raw = self.rows.get((run_id, field))
        return None if raw is None else json.loads(raw)

    async def read_all(self, run_id: str) -> dict[str, Any]:
        return {f: json.loads(v) for (r, f), v in self.rows.items() if r == run_id}


def bind(brief: dict[str, Any], run_id: str = "t") -> MemPad:
    """Bind a tool's run context. Each async test runs in its own task, so this can't leak."""
    pad = MemPad()
    CURRENT_SCRATCHPAD.set(pad)
    CURRENT_RUN_ID.set(run_id)
    CURRENT_SEED.set("seed")
    CURRENT_BRIEF.set(brief)
    PROVIDER_LATENCY_MS.set(0)
    return pad
