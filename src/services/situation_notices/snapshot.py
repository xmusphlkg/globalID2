"""Situation snapshot consumed by Research Radar.

Research Radar links published literature to current Situation events. It
reads a snapshot whose ``emerging`` section lists official outbreaks; this
module builds that snapshot from the alert tables.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from src.core.database import get_db
from src.domain import SituationAlert, SituationOutbreak
from src.services.global_sources.catalogue import country_catalogue, disease_catalogue

from .ai import EXTRACTION_PROMPT_VERSION
from .pipeline import utc_now

SNAPSHOT_SCHEMA_VERSION = "situation_alerts.v1"
#: Only outbreaks with an alert inside this window count as current.
SNAPSHOT_WINDOW_DAYS = 120


async def latest_situation_snapshot(*, window_days: int = SNAPSHOT_WINDOW_DAYS) -> dict[str, Any] | None:
    """Current outbreaks as a literature-linking snapshot, or ``None`` when empty."""

    now = utc_now()
    since = now.date() - timedelta(days=window_days)
    async with get_db() as db:
        outbreaks = (
            await db.execute(
                select(SituationOutbreak)
                .where(SituationOutbreak.last_published >= since)
                .order_by(SituationOutbreak.last_published.desc(), SituationOutbreak.outbreak_id)
            )
        ).scalars().all()
        ids = [row.outbreak_id for row in outbreaks]
        alerts = (
            (await db.execute(select(SituationAlert).where(SituationAlert.outbreak_id.in_(ids)))).scalars().all()
            if ids
            else []
        )
    if not outbreaks:
        return None

    latest_alert: dict[str, SituationAlert] = {}
    for alert in sorted(alerts, key=lambda item: (item.published_date, item.alert_id)):
        latest_alert[alert.outbreak_id] = alert

    diseases = {row["disease_id"]: row for row in disease_catalogue()}
    countries = country_catalogue()
    emerging: list[dict[str, Any]] = []
    for outbreak in outbreaks:
        latest = latest_alert.get(outbreak.outbreak_id)
        disease = diseases.get(outbreak.disease_id or "") or {}
        name = disease.get("name_en") or outbreak.disease_label
        emerging.append(
            {
                "id": outbreak.outbreak_id,
                "kind": "official_event",
                "title": latest.summary_en if latest and latest.summary_en else name,
                "disease_id": outbreak.disease_id,
                "disease_name": name,
                "geographies": [
                    {"code": code, "name": (countries.get(code) or {}).get("name_en") or code}
                    for code in outbreak.country_codes or []
                ],
                "published_at": outbreak.last_published.isoformat(),
                "source": latest.source_id if latest else None,
                "risk": {},
                "status": outbreak.status,
            }
        )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "public_enabled": True,
        "snapshot_id": f"situation-alerts-{now.date().isoformat()}",
        "generated_at": now.isoformat(),
        "data_through": max(row.last_published for row in outbreaks).isoformat(),
        "method_version": EXTRACTION_PROMPT_VERSION,
        "increasing": [],
        "unusual": [],
        "emerging": emerging,
    }
