"""State layer: pluggable session services and scratchpad stores."""

from app.state.base import ScratchpadStore
from app.state.registry import BACKEND_SPECS, BackendSpec, backends

__all__ = ["BACKEND_SPECS", "BackendSpec", "ScratchpadStore", "backends"]
