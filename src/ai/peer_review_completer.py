"""Peer-review roles submit durable work; the dispatcher chooses each model."""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from src.ai.agents.base import BaseAgent
from src.ai.model_identity import model_identity
from src.ai.runtime.contracts import RoutingRequirements

from .peer_review import Completion


class PeerReviewAgent(BaseAgent):
    def __init__(self, name: str, *, max_tokens: int = 2400) -> None:
        super().__init__(name=name, temperature=0.1, max_tokens=max_tokens)

    async def process(self, **kwargs: Any) -> dict[str, Any]:
        return {"raw_response": await self.complete(prompt=kwargs["prompt"], system=kwargs.get("system"))}


class ModelCenterCompleter:
    """Separate role histories with explicit model-diversity requirements.

    Model availability is evaluated only when durable work is claimed. Creating
    a business reviewer never probes routes or guesses another process's load.
    """

    def __init__(
        self, *, line: str = "situation_notice", max_tokens: int = 2400,
        allow_single_model: bool = False, author_model: str | None = None,
        avoid_models: Iterable[str] = (),
    ) -> None:
        self.agents = {
            role: PeerReviewAgent(f"{line}_{role}", max_tokens=max_tokens)
            for role in ("author", "reviewer")
        }
        self.author_model = author_model
        self.avoid_models = tuple(name for name in avoid_models if name)
        self.allow_single_model = allow_single_model
        self.single_model = False

    @property
    def review_mode(self) -> str:
        return "single_model" if self.single_model else "cross_model"

    @classmethod
    async def from_model_center(
        cls, *, allow_single_model: bool = False, line: str = "situation_notice", max_tokens: int = 2400,
    ) -> "ModelCenterCompleter":
        return cls(line=line, max_tokens=max_tokens, allow_single_model=allow_single_model)

    @classmethod
    async def for_reviewing(
        cls, author_model: str | None, *, allow_single_model: bool = False,
        line: str = "situation_notice", max_tokens: int = 2400,
    ) -> "ModelCenterCompleter":
        return cls(line=line, max_tokens=max_tokens, allow_single_model=allow_single_model, author_model=author_model)

    async def __call__(
        self, role: str, system: str, prompt: str, *, attachments: Sequence[Any] | None = None,
    ) -> Completion:
        agent = self.agents[role]
        avoid = self.avoid_models
        if role == "reviewer" and self.author_model:
            avoid = (*avoid, self.author_model, model_identity(self.author_model))
        routing = RoutingRequirements(
            avoid_models=tuple(dict.fromkeys(avoid)),
            preferred_models=(self.author_model,) if role == "author" and self.author_model else (),
            require_distinct_model=role == "reviewer" and not self.allow_single_model,
        )
        extra = {"attachments": list(attachments)} if attachments else {}
        text = await agent.complete(prompt=prompt, system=system, use_cache=False, routing=routing, **extra)
        conversation = agent.get_latest_conversation() or {}
        route = (conversation.get("metadata") or {}).get("runtime_route") or {}
        served_by = str(route.get("model_key") or conversation.get("model") or "unknown")
        if role == "author":
            self.author_model = served_by
        else:
            self.single_model = model_identity(served_by) == model_identity(self.author_model)
        return Completion(
            text=text, model=served_by,
            input_mode=getattr(agent, "_last_input_mode", None) if attachments else None,
        )
