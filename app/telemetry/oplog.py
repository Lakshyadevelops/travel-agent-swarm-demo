"""Append-only JSONL op log.

Every StateOp is persisted raw so that any published metric can be recomputed
offline instead of requiring a re-run. Writes go through an asyncio queue drained
by a background task, so disk I/O never lands inside the measured code path.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import RUNS_DIR
from app.telemetry.metrics import StateOp


class OpLog:
    """Buffered append-only writer for one run."""

    def __init__(self, run_id: str, base_dir: Path | None = None) -> None:
        self.run_id = run_id
        self.dir = (base_dir or RUNS_DIR) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "ops.jsonl"
        self._queue: asyncio.Queue[StateOp | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._drain())

    def submit(self, op: StateOp) -> None:
        self._queue.put_nowait(op)

    async def _drain(self) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            while True:
                op = await self._queue.get()
                if op is None:
                    fh.flush()
                    return
                fh.write(json.dumps(op.to_json(), separators=(",", ":")) + "\n")
                if self._queue.empty():
                    fh.flush()

    async def close(self) -> None:
        if self._task is not None:
            await self._queue.put(None)
            await self._task
            self._task = None

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        """Pin exactly what produced these numbers."""
        (self.dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8"
        )
