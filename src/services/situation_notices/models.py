"""Model-center backed ``Completer`` for the peer-review flow.

Author and reviewer calls go through :class:`~src.ai.agents.base.BaseAgent`, so
they share the model center's routing, admission control, cooldowns and call
logging.  The two roles are pinned to *different* active routes: the author
prefers the highest-priority route and the reviewer the next one.
"""

from __future__ import annotations

from typing import Any

from src.ai.agents.base import BaseAgent
from src.ai.model_center import get_active_model_routes

from .ai import AIOutputError, Completion


class SituationAgent(BaseAgent):
    def __init__(self, name: str) -> None:
        super().__init__(name=name, temperature=0.1, max_tokens=2400)

    async def process(self, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover - not used
        return {"raw_response": await self.complete(prompt=kwargs["prompt"], system=kwargs.get("system"))}


def _route_key(route: dict[str, Any]) -> str:
    return str(route.get("model_key") or route.get("model_name") or "")


class ModelCenterCompleter:
    """Callable ``(role, system, prompt) -> Completion`` with distinct role routes."""

    def __init__(self, author_route: str, reviewer_route: str, *, allow_single_model: bool = False) -> None:
        if author_route == reviewer_route and not allow_single_model:
            raise AIOutputError("peer review needs two different model routes")
        #: Read by :func:`~.ai.peer_review`: one model plays both roles with
        #: separate prompts and separate calls, and exports say so.
        self.single_model = author_route == reviewer_route
        self.preferences = {"author": [author_route], "reviewer": [reviewer_route]}
        self.agents = {
            "author": SituationAgent("situation_notice_author"),
            "reviewer": SituationAgent("situation_notice_reviewer"),
        }

    @classmethod
    async def from_model_center(cls, *, allow_single_model: bool = True) -> "ModelCenterCompleter":
        """Cross-model review whenever two routes exist; otherwise one model self-reviews.

        Adding a second route in the model center upgrades every later run to
        cross-model review without a code change.
        """

        routes = [_route_key(route) for route in await get_active_model_routes()]
        distinct = list(dict.fromkeys(key for key in routes if key))
        if len(distinct) >= 2:
            return cls(distinct[0], distinct[1])
        if distinct and allow_single_model:
            return cls(distinct[0], distinct[0], allow_single_model=True)
        raise AIOutputError(
            f"peer review needs at least two active model routes; found {len(distinct)}"
        )

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
