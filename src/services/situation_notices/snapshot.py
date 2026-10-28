"""Situation snapshot consumed by Research Radar.

Research Radar links published literature to current Situation events. It
reads a snapshot whose ``emerging`` section lists official events; this module
builds that snapshot from the notice-based tables so literature linking keeps
working without the retired statistical screen.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.domain import SituationEvent, SituationNotice
from src.services.global_sources.catalogue import country_catalogue, disease_catalogue

from .ai import NOTICE_PROMPT_VERSION
from .pipeline import utc_now

SNAPSHOT_SCHEMA_VERSION = "situation_notices.v1"
#: Only events with a notice inside this window count as current.
SNAPSHOT_WINDOW_DAYS = 120


async def latest_situation_snapshot(*, window_days: int = SNAPSHOT_WINDOW_DAYS) -> dict[str, Any] | None:
    """Current events as a literature-linking snapshot, or ``None`` when empty."""

    now = utc_now()
    since = now.date() - timedelta(days=window_days)
    async with get_db() as db:
        events = (
            await db.execute(
                select(SituationEvent)
                .options(selectinload(SituationEvent.members))
                .where(SituationEvent.last_published >= since)
                .order_by(SituationEvent.last_published.desc(), SituationEvent.event_id)
            )
        ).scalars().all()
        member_ids = {member.notice_id for event in events for member in event.members}
        notices = {
            row.notice_id: row
            for row in (
                await db.execute(select(SituationNotice).where(SituationNotice.notice_id.in_(member_ids)))
            ).scalars()
        } if member_ids else {}
    if not events:
        return None

    diseases = {row["disease_id"]: row for row in disease_catalogue()}
    countries = country_catalogue()
    emerging: list[dict[str, Any]] = []
    for event in events:
        latest = max(
            (notices[m.notice_id] for m in event.members if m.notice_id in notices),
            key=lambda row: (row.published_date, row.notice_id),
            default=None,
        )
        disease = diseases.get(event.disease_id) or {}
        emerging.append(
            {
                "id": event.event_id,
                "kind": "official_event",
                "title": latest.title_en if latest else disease.get("name_en") or event.disease_id,
                "disease_id": event.disease_id,
                "disease_name": disease.get("name_en") or event.disease_id,
                "geographies": [
                    {"code": code, "name": (countries.get(code) or {}).get("name_en") or code}
                    for code in event.country_codes or []
                ],
                "published_at": event.last_published.isoformat(),
                "source": latest.source_id if latest else None,
                "risk": {"level": event.risk_level},
                "status": event.status,
            }
        )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "public_enabled": True,
        "snapshot_id": f"situation-notices-{now.date().isoformat()}",
        "generated_at": now.isoformat(),
        "data_through": max(event.last_published for event in events).isoformat(),
        "method_version": NOTICE_PROMPT_VERSION,
        "increasing": [],
        "unusual": [],
        "emerging": emerging,
    }
