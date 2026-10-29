"""Model-center ``Completer`` for Situation; the implementation is shared.

See :mod:`src.ai.peer_review_completer`. Kept so existing imports keep working.
"""

from __future__ import annotations

from src.ai.peer_review_completer import ModelCenterCompleter, PeerReviewAgent

#: Historic name.
SituationAgent = PeerReviewAgent

__all__ = ["ModelCenterCompleter", "PeerReviewAgent", "SituationAgent"]
