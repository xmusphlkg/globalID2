"""Common route weighting for model scheduling and peer review."""

from collections import deque
from collections.abc import Iterable


def route_weight(capacity: int, inflight: int, outcomes: Iterable[bool] = ()) -> float:
    """Remaining permits times smoothed success rate squared (last 50 calls)."""
    recent = deque(outcomes, maxlen=50)
    success = (sum(recent) + 2) / (len(recent) + 2)
    return max(0, capacity - inflight) * success**2
