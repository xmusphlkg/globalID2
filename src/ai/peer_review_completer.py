"""Model-center backed ``Completer`` for :mod:`src.ai.peer_review`.

Author and reviewer calls go through :class:`~src.ai.agents.base.BaseAgent`, so
they share the model center's routing, admission control, cooldowns and call
logging.  The two roles are pinned to *different* active routes: the author
prefers the highest-priority route and the reviewer the next one.  With one
route the same model plays both roles and outputs are labelled
``single_model``.
"""

from __future__ import annotations

from typing import Any

from src.ai.agents.base import BaseAgent
from src.ai.model_center import get_active_model_routes

from .peer_review import AIOutputError, Completion


class PeerReviewAgent(BaseAgent):
    def __init__(self, name: str, *, max_tokens: int = 2400) -> None:
        super().__init__(name=name, temperature=0.1, max_tokens=max_tokens)

    async def process(self, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover - not used
        return {"raw_response": await self.complete(prompt=kwargs["prompt"], system=kwargs.get("system"))}


def _route_key(route: dict[str, Any]) -> str:
    return str(route.get("model_key") or route.get("model_name") or "")


class ModelCenterCompleter:
    """Callable ``(role, system, prompt) -> Completion`` with distinct role routes."""

    def __init__(
        self,
        author_route: str,
        reviewer_route: str,
        *,
        allow_single_model: bool = False,
        line: str = "situation_notice",
        max_tokens: int = 2400,
    ) -> None:
        if author_route == reviewer_route and not allow_single_model:
            raise AIOutputError("peer review needs two different model routes")
        #: Read by :func:`~src.ai.peer_review.peer_review`: one model plays both
        #: roles with separate prompts and separate calls, and exports say so.
        self.single_model = author_route == reviewer_route
        self.line = line
        self.preferences = {"author": [author_route], "reviewer": [reviewer_route]}
        self.agents = {
            "author": PeerReviewAgent(f"{line}_author", max_tokens=max_tokens),
            "reviewer": PeerReviewAgent(f"{line}_reviewer", max_tokens=max_tokens),
        }

    @property
    def review_mode(self) -> str:
        return "single_model" if self.single_model else "cross_model"

    @classmethod
    async def from_model_center(
        cls,
        *,
        allow_single_model: bool = True,
        line: str = "situation_notice",
        max_tokens: int = 2400,
    ) -> "ModelCenterCompleter":
        """Cross-model review whenever two routes exist; otherwise one model self-reviews.

        Adding a second route in the model center upgrades every later run to
        cross-model review without a code change.
        """

        routes = [_route_key(route) for route in await get_active_model_routes()]
        distinct = list(dict.fromkeys(key for key in routes if key))
        if len(distinct) >= 2:
            return cls(distinct[0], distinct[1], line=line, max_tokens=max_tokens)
        if distinct and allow_single_model:
            return cls(distinct[0], distinct[0], allow_single_model=True, line=line, max_tokens=max_tokens)
        raise AIOutputError(f"peer review needs at least two active model routes; found {len(distinct)}")

    @classmethod
    async def for_reviewing(
        cls,
        author_model: str | None,
        *,
        allow_single_model: bool = True,
        line: str = "situation_notice",
        max_tokens: int = 2400,
    ) -> "ModelCenterCompleter":
        """Review a draft another pipeline already wrote with ``author_model``.

        The author role (used for revisions) stays on the drafting route when
        it is active; the reviewer takes the first other route.
        """

        routes = [_route_key(route) for route in await get_active_model_routes()]
        distinct = list(dict.fromkeys(key for key in routes if key))
        if not distinct:
            raise AIOutputError("peer review needs at least one active model route; found 0")

        def matches(key: str) -> bool:
            # Conversations may report a bare model name; route keys are provider:model.
            return bool(author_model) and (key == author_model or key.split(":", 1)[-1] == author_model)

        author = next((key for key in distinct if matches(key)), distinct[0])
        reviewer = next((key for key in distinct if key != author), None)
        if reviewer is not None:
            return cls(author, reviewer, line=line, max_tokens=max_tokens)
        if allow_single_model:
            return cls(author, author, allow_single_model=True, line=line, max_tokens=max_tokens)
        raise AIOutputError("peer review needs a second active model route")

    async def __call__(self, role: str, system: str, prompt: str) -> Completion:
        agent = self.agents[role]
        text = await agent.complete(
            prompt=prompt,
            system=system,
            # Re-running after a content change must hit the model again.
            use_cache=False,
            preferred_models=self.preferences[role],
            wait_for_model_recovery=False,
            max_attempts_per_model=2,
        )
        conversation = agent.get_latest_conversation() or {}
        return Completion(text=text, model=str(conversation.get("model") or self.preferences[role][0]))


__all__ = ["ModelCenterCompleter", "PeerReviewAgent"]
