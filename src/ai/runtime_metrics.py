"""Shared model occupancy derived from durable leases.

The legacy ``*_inflight`` API fields describe occupied queue permits. They do
not count live HTTP sockets: a leased call may still be preparing its request,
and a health probe may issue several provider requests. Catalogue projections
supply configured limits; this module supplies cross-process observations.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

from sqlalchemy import func, select

from src.core import get_database, get_logger
from src.domain.ai_model_center import AIModelConfig
from src.domain.ai_work_item import AIWorkItem

logger = get_logger(__name__)
Route = Mapping[str, Any]


@dataclass(frozen=True)
class RuntimeCapacitySnapshot:
    sampled_at: datetime
    models: dict[str, dict[str, int]]
    providers: dict[str, dict[str, int]]
    route_leases: dict[str, int]
    unassigned_leases: int = 0

    def heartbeat_metadata(self) -> dict[str, Any]:
        return {
            "ai_model_capacities": self.models,
            "ai_provider_capacities": self.providers,
            "ai_capacity_source": "database_leases",
            "ai_capacity_sampled_at": self.sampled_at.isoformat(),
            "ai_unassigned_model_leases": self.unassigned_leases,
        }

    def apply_to_routes(self, routes: Sequence[Route]) -> list[dict[str, Any]]:
        """Overlay occupancy without mutating the shared catalogue snapshot."""
        return [
            {
                **route,
                **self.providers.get(str(route.get("provider_id")), {}),
                **self.models.get(str(route.get("model_id")), {}),
            }
            for route in routes
        ]


async def read_runtime_metrics(
    routes: Sequence[Route], *, now: datetime | None = None
) -> RuntimeCapacitySnapshot:
    """Read one aggregate of unexpired text leases across all dispatchers.

    The model join also attributes a lease whose route was disabled after the
    request started. Such a call remains part of provider occupancy until its
    lease finishes or expires. Unknown/deleted route identities are counted
    separately instead of being silently treated as idle.
    """
    sampled_at = now or datetime.now(timezone.utc)
    async with get_database() as db:
        rows = (
            await db.execute(
                select(
                    AIWorkItem.route_key,
                    AIModelConfig.id,
                    AIModelConfig.provider_id,
                    func.count(),
                )
                .outerjoin(AIModelConfig, AIModelConfig.model_key == AIWorkItem.route_key)
                .where(
                    AIWorkItem.status == "leased",
                    AIWorkItem.lease_expires_at > sampled_at,
                    AIWorkItem.capability != "health",
                )
                .group_by(AIWorkItem.route_key, AIModelConfig.id, AIModelConfig.provider_id)
            )
        ).all()

    models: dict[str, dict[str, int]] = {}
    providers: dict[str, dict[str, int]] = {}
    identities: dict[str, tuple[str, str]] = {}
    for route in routes:
        model_id, provider_id = str(route["model_id"]), str(route["provider_id"])
        identities[str(route["model_key"])] = model_id, provider_id
        models[model_id] = {
            "runtime_model_capacity": max(0, int(route.get("runtime_model_capacity") or 0)),
            "runtime_model_inflight": 0,
        }
        # Catalogue capacity is already aggregated across the provider's routes.
        providers[provider_id] = {
            "runtime_provider_capacity": max(0, int(route.get("runtime_provider_capacity") or 0)),
            "runtime_provider_max_capacity": max(0, int(route.get("runtime_provider_max_capacity") or 0)),
            "runtime_provider_inflight": 0,
        }

    route_leases: dict[str, int] = {}
    unassigned = 0
    for key, model_id, provider_id, count in rows:
        count = int(count)
        if key is not None:
            route_leases[str(key)] = count
        identity = identities.get(str(key))
        if identity is None and model_id is not None and provider_id is not None:
            identity = str(model_id), str(provider_id)
        if identity is None:
            unassigned += count
            continue
        model_key, provider_key = identity
        model = models.setdefault(model_key, {"runtime_model_capacity": 0, "runtime_model_inflight": 0})
        provider = providers.setdefault(provider_key, {
            "runtime_provider_capacity": 0,
            "runtime_provider_max_capacity": 0,
            "runtime_provider_inflight": 0,
        })
        model["runtime_model_inflight"] += count
        provider["runtime_provider_inflight"] += count
    return RuntimeCapacitySnapshot(sampled_at, models, providers, route_leases, unassigned)


class RuntimeMetricsMonitor:
    """Refresh observations asynchronously for the synchronous heartbeat thread."""

    def __init__(
        self,
        route_loader: Callable[[], Awaitable[list[dict[str, Any]]]],
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        self.route_loader = route_loader
        self.interval_seconds = interval_seconds
        self.snapshot: RuntimeCapacitySnapshot | None = None

    def heartbeat_metadata(self) -> dict[str, Any]:
        # The thread reads an immutable reference; never query an async engine
        # from the guard thread or mutate an already-published snapshot.
        snapshot = self.snapshot
        return snapshot.heartbeat_metadata() if snapshot is not None else {
            "ai_model_capacities": {},
            "ai_provider_capacities": {},
            "ai_capacity_source": "unavailable",
            "ai_capacity_sampled_at": None,
        }

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                self.snapshot = await read_runtime_metrics(await self.route_loader())
            except Exception:
                # Retain its original timestamp so consumers can identify stale
                # telemetry. A failed observation never manufactures zero load.
                logger.exception("Could not refresh shared model occupancy")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
