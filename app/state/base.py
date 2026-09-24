"""State-layer protocols shared by every backend arm."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class ScratchpadStore(Protocol):
    """The inter-agent blackboard.

    This is the concurrency-heavy path: intermediate tool outputs, JSON blobs and
    sub-goals passed between workers that run simultaneously under ParallelAgent.
    """

    backend: str

    async def write(self, run_id: str, field: str, value: Any) -> None: ...

    async def write_many(self, run_id: str, values: Mapping[str, Any]) -> None: ...

    async def read(self, run_id: str, field: str) -> Any | None: ...

    async def read_all(self, run_id: str) -> dict[str, Any]: ...

    async def delete(self, run_id: str) -> None: ...

    async def close(self) -> None: ...
