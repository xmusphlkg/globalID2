"""Model-center backed ``Completer`` for :mod:`src.ai.peer_review`.

Author and reviewer calls go through :class:`~src.ai.agents.base.BaseAgent`, so
they share the model center's routing, admission control, cooldowns and call
logging.  The two roles are pinned to *different* active routes: the author
prefers the highest-priority route and the reviewer the next one.  With one
route the same model plays both roles and outputs are labelled
``single_model``.
"""

from __future__ import annotations

import random
from collections import deque
from typing import Any, Iterable, Sequence

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
        recorder: "ReviewRoutePool | None" = None,
        author_fallbacks: Sequence[str] = (),
        reviewer_fallbacks: Sequence[str] = (),
    ) -> None:
        #: Read by :func:`~src.ai.peer_review.peer_review`: one model plays both
        #: roles with separate prompts and separate calls, and exports say so.
        self.single_model = author_route == reviewer_route
        self.line = line
        self.recorder = recorder
        self.preferences = {
            "author": [author_route, *author_fallbacks],
            "reviewer": [reviewer_route, *reviewer_fallbacks],
        }
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

    async def __call__(
        self, role: str, system: str, prompt: str, *, attachments: Sequence[Any] | None = None
    ) -> Completion:
        agent = self.agents[role]
        extra = {"attachments": list(attachments)} if attachments else {}
        preferred = self.preferences[role][0]
        if self.recorder:
            self.recorder.begin(preferred)
        completed = False
        try:
            text = await agent.complete(
                prompt=prompt,
                system=system,
                **extra,
                # Re-running after a content change must hit the model again.
                use_cache=False,
                preferred_models=self.preferences[role],
                wait_for_model_recovery=False,
                max_attempts_per_model=2,
            )
            completed = True
        finally:
            if self.recorder and not completed:
                self.recorder.finish(preferred, served_by=None)
        conversation = agent.get_latest_conversation() or {}
        served_by = str(conversation.get("model") or preferred)
        if self.recorder:
            self.recorder.finish(preferred, served_by=served_by)
        return Completion(
            text=text,
            model=served_by,
            input_mode=getattr(agent, "_last_input_mode", None) if attachments else None,
        )


class ReviewRoutePool:
    """Hands each concurrent review item its own distinct author/reviewer routes.

    Routes are weighted by free request permits and the square of their recent
    success rate, so a route that keeps failing is picked less often (but never
    starved, which lets it recover) and a saturated route stops attracting work.
    """

    WINDOW = 50

    def __init__(
        self,
        capacities: dict[str, int],
        *,
        line: str,
        max_tokens: int,
        rng: random.Random | None = None,
    ) -> None:
        self._capacity = dict(capacities)
        self._inflight = {key: 0 for key in capacities}
        self._outcomes: dict[str, deque[bool]] = {key: deque(maxlen=self.WINDOW) for key in capacities}
        self._external: dict[str, int] = {}
        self._line = line
        self._max_tokens = max_tokens
        self._rng = rng or random.Random()

    @staticmethod
    async def _load_routes() -> tuple[dict[str, int], dict[str, str]]:
        """Panel-configured permits per route key, plus each key's model id."""

        capacities: dict[str, int] = {}
        model_ids: dict[str, str] = {}
        for route in await get_active_model_routes():
            key = _route_key(route)
            if key and key not in capacities:
                capacities[key] = max(1, int(route.get("runtime_model_capacity") or 1))
                model_ids[key] = str(route.get("model_id"))
        if not capacities:
            raise AIOutputError("peer review needs at least one active model route; found 0")
        return capacities, model_ids

    @staticmethod
    async def _worker_inflight() -> dict[str, int]:
        """Calls the task worker has in flight per model id, from its heartbeat.

        Permits are per process, so the worker and this process each admit up to
        the panel limit. Subtracting the worker's share keeps the combined load
        near that limit. Unreadable heartbeats count as zero.
        """

        try:
            from src.control_plane.runtime import runtime_registry

            services, _available = await runtime_registry.list_services()
        except Exception:  # noqa: BLE001 - telemetry only
            return {}
        workers = [item for item in services if item.get("service") == "worker"]
        workers.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
        metadata = workers[0].get("metadata") if workers else None
        snapshots = metadata.get("ai_model_capacities") if isinstance(metadata, dict) else None
        if not isinstance(snapshots, dict):
            return {}
        inflight: dict[str, int] = {}
        for model_id, snapshot in snapshots.items():
            try:
                inflight[str(model_id)] = max(0, int(snapshot.get("runtime_model_inflight") or 0))
            except (AttributeError, TypeError, ValueError):
                continue
        return inflight

    @classmethod
    async def from_model_center(
        cls, *, line: str = "content_governance", max_tokens: int = 4000
    ) -> "ReviewRoutePool":
        capacities, model_ids = await cls._load_routes()
        pool = cls(capacities, line=line, max_tokens=max_tokens)
        await pool._apply_worker_load(model_ids)
        return pool

    async def _apply_worker_load(self, model_ids: dict[str, str]) -> None:
        worker = await self._worker_inflight()
        self._external = {key: worker.get(model_id, 0) for key, model_id in model_ids.items()}

    async def refresh(self) -> None:
        """Follow the model center's active routes while keeping learned outcomes.

        Calls already in flight finish against their recorded route key, so state
        for a route that left the active set is kept, just no longer chosen.
        """

        capacities, model_ids = await self._load_routes()
        await self._apply_worker_load(model_ids)
        for key, capacity in capacities.items():
            self._capacity[key] = capacity
            self._inflight.setdefault(key, 0)
            self._outcomes.setdefault(key, deque(maxlen=self.WINDOW))
        for key in [key for key in self._capacity if key not in capacities]:
            del self._capacity[key]

    def failure_rate(self, key: str) -> float:
        window = self._outcomes[key]
        return (len(window) - sum(window)) / len(window) if window else 0.0

    def weight(self, key: str) -> float:
        window = self._outcomes[key]
        success = (sum(window) + 2) / (len(window) + 2)
        free = max(self._capacity[key] - self._inflight[key] - self._external.get(key, 0), 1)
        return free * success**2

    def _choose(self, keys: list[str]) -> str:
        return self._rng.choices(keys, weights=[self.weight(key) for key in keys])[0]

    def completer(self, avoid: Iterable[str] = ()) -> ModelCenterCompleter:
        # Author and reviewer are different models. Any pair is fine (a fast model may
        # review another fast model); load and failure rate decide, so a saturated
        # or failing route stops attracting work. ``avoid`` holds models a failed
        # attempt collapsed onto (route key or bare model name); they are skipped
        # while at least two other routes remain.
        keys = [key for key in self._capacity if not BaseAgent._is_route_cooling_down(key)] or list(self._capacity)
        avoided = {name for name in avoid if name}
        if avoided:
            remaining = [key for key in keys if key not in avoided and key.split(":", 1)[-1] not in avoided]
            if len(remaining) >= 2:
                keys = remaining
        author = self._choose(keys)
        others = [key for key in keys if key != author]
        reviewer = self._choose(others) if others else author
        ranked = sorted(keys, key=self.weight, reverse=True)
        return ModelCenterCompleter(
            author,
            reviewer,
            allow_single_model=True,
            line=self._line,
            max_tokens=self._max_tokens,
            recorder=self,
            # Failover stays off the other role's model, which is tried last for the
            # author and never for the reviewer, so a failover cannot make both
            # roles one model while a third route exists.
            author_fallbacks=[key for key in ranked if key not in (author, reviewer)] + ([reviewer] if reviewer != author else []),
            reviewer_fallbacks=[key for key in ranked if key not in (author, reviewer)],
        )

    def begin(self, key: str) -> None:
        if key in self._inflight:
            self._inflight[key] += 1

    def finish(self, key: str, *, served_by: str | None) -> None:
        """Record one call: ``served_by`` is the model that answered, ``None`` if it failed."""

        if key not in self._inflight:
            return
        self._inflight[key] = max(0, self._inflight[key] - 1)
        if served_by is None:
            self._outcomes[key].append(False)
            return
        if served_by == key or served_by == key.split(":", 1)[-1]:
            self._outcomes[key].append(True)
            return
        # Failover answered instead: the preferred route did not deliver.
        self._outcomes[key].append(False)
        for other in self._outcomes:
            if other == served_by or other.split(":", 1)[-1] == served_by:
                self._outcomes[other].append(True)


__all__ = ["ModelCenterCompleter", "PeerReviewAgent", "ReviewRoutePool"]
