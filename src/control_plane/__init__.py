"""GIDS control-plane application boundary.

The control plane owns dashboard-oriented queries, runtime coordination, and
delivery events.  Data collection, disease semantics, and report generation
remain in their existing domain packages and are consumed through adapters.
"""

from .events import control_plane_events
from .runtime import runtime_registry

__all__ = ["control_plane_events", "runtime_registry"]
