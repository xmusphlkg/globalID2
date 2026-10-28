"""Export the notice database into the static Situation site data.

The sharded notice files keep the layout of
:mod:`src.services.global_sources.export_static`, so the existing disease and
country panels keep working. Two files are added next to them:

* ``events.json``: clustered disease x place events, newest first;
* ``digests/<period>.json`` plus ``digests/index.json``: published weekly
  briefs. Each point keeps the notice ids it cites.

Only peer-reviewed AI output is exported. A notice without a published
assessment still appears with its original English metadata.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.domain import SituationDigest, SituationEvent, SituationNotice, SituationSourceRun
from src.services.global_sources.catalogue import SOURCE_REGISTRY, compose_chinese_title, disease_catalogue, iso_week
from src.services.global_sources.export_static import DEFAULT_OUTPUT_DIR, DEFAULT_RECENT_LIMIT, SCHEMA_VERSION, export_events

from .ai import review_mode
from .pipeline import utc_now


def notice_record(row: SituationNotice) -> dict[str, Any]:
    """One notice in the public shape, with only published AI fields."""

    registry = SOURCE_REGISTRY.get(row.source_id, {})
    assessment = next((a for a in row.assessments if a.status == "published"), None)
    active = [e for e in row.entities if e.active]
    slugs = {item["disease_id"]: item.get("slug") or "" for item in disease_catalogue()}
    diseases = [
        {"disease_id": e.entity_id, "name_en": e.name_en, "name_zh": e.name_zh, "slug": slugs.get(e.entity_id, "")}
        for e in active
        if e.entity_type == "disease"
    ]
    countries = [
        {"code": e.entity_id, "name_en": e.name_en, "name_zh": e.name_zh}
        for e in active
        if e.entity_type == "country"
    ]
    keep_extract = row.redistribution != "metadata_only"
    record = {
        "event_id": row.notice_id,
        "source_id": row.source_id,
        "native_id": row.native_id,
        "record_type": row.record_type,
        "title_en": row.title_en,
        "title_zh": (assessment.title_zh if assessment else None)
        or compose_chinese_title(diseases, countries, row.source_id, row.published_date),
        "title_fr": assessment.title_fr if assessment else None,
        # AI summaries are written from metadata, so they are allowed even for
        # metadata-only sources; the agency's own extract is not.
        "summary_en": (assessment.summary_en if assessment else None) or (row.summary_en if keep_extract else None),
        "summary_zh": assessment.summary_zh if assessment else None,
        "summary_fr": assessment.summary_fr if assessment else None,
        "url": row.url,
        "source_label_en": (registry.get("label") or {}).get("en"),
        "source_label_zh": (registry.get("label") or {}).get("zh"),
        "agency_en": (registry.get("agency") or {}).get("en"),
        "agency_zh": (registry.get("agency") or {}).get("zh"),
        "language": row.language,
        "published_date": row.published_date.isoformat(),
        "updated_at_source": row.updated_at_source,
        "diseases": diseases,
        "countries": countries,
        "retrieved_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "fingerprint": row.content_hash[:16],
        "event_type": assessment.event_type if assessment else None,
        "risk_level": assessment.risk_level if assessment else None,
        "risk_rationale": assessment.risk_rationale if assessment else None,
        "ai": (
            {
                "author_model": assessment.author_model,
                "reviewer_model": assessment.reviewer_model,
                "review_mode": review_mode(assessment.author_model, assessment.reviewer_model),
                "verdict": assessment.review_verdict,
                "rounds": assessment.review_rounds,
                "prompt_version": assessment.prompt_version,
            }
            if assessment
            else None
        ),
        **iso_week(row.published_date),
    }
    return record


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest_record(row: SituationDigest) -> dict[str, Any]:
    return {
        "period_key": row.period_key,
        "headline_en": row.headline_en,
        "headline_zh": row.headline_zh,
        "headline_fr": row.headline_fr,
        "points": row.points or [],
        "notice_count": row.notice_count,
        "generated_at": row.created_at.isoformat() if row.created_at else None,
        "ai": {
            "author_model": row.author_model,
            "reviewer_model": row.reviewer_model,
            "review_mode": review_mode(row.author_model, row.reviewer_model),
            "verdict": row.review_verdict,
            "score": row.review_score,
            "prompt_version": row.prompt_version,
        },
    }


async def export_site(
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    since_days: int = 120,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or utc_now().date()
    since = as_of - timedelta(days=since_days)
    out = Path(output_dir)
    async with get_db() as db:
        notices = (
            await db.execute(
                select(SituationNotice)
                .options(selectinload(SituationNotice.entities), selectinload(SituationNotice.assessments))
                # A reviewer rejection means "not an infectious-disease event".
                .where(SituationNotice.published_date >= since, SituationNotice.ai_status != "rejected")
                .order_by(SituationNotice.published_date.desc(), SituationNotice.notice_id.desc())
            )
        ).scalars().all()
        records = [notice_record(row) for row in notices]
        records = [r for r in records if r["diseases"] or r["countries"]]

        latest_run = (
            await db.execute(select(SituationSourceRun.run_id).order_by(SituationSourceRun.started_at.desc()).limit(1))
        ).scalar_one_or_none()
        sources = []
        if latest_run:
            for run in (
                await db.execute(select(SituationSourceRun).where(SituationSourceRun.run_id == latest_run))
            ).scalars():
                registry = SOURCE_REGISTRY.get(run.source_id, {})
                sources.append(
                    {
                        "source_id": run.source_id,
                        "label_en": (registry.get("label") or {}).get("en"),
                        "label_zh": (registry.get("label") or {}).get("zh"),
                        "endpoint": registry.get("endpoint"),
                        "homepage": registry.get("homepage"),
                        "redistribution": registry.get("redistribution"),
                        "status": run.status,
                        "event_count": run.fetched_count,
                        "undated_records": 0,
                        "error": run.error,
                    }
                )

        events = (
            await db.execute(
                select(SituationEvent)
                .options(selectinload(SituationEvent.members))
                .where(SituationEvent.last_published >= since)
                .order_by(SituationEvent.last_published.desc())
            )
        ).scalars().all()
        digests = (
            await db.execute(
                select(SituationDigest)
                .where(SituationDigest.status == "published")
                .order_by(SituationDigest.period_key.desc())
            )
        ).scalars().all()

    summary = export_events(
        {
            "generated_at": utc_now().isoformat(),
            "since_days": since_days,
            "since_date": since.isoformat(),
            "as_of": as_of.isoformat(),
            "sources": sources,
            "events": records,
        },
        output_dir=out,
        recent_limit=recent_limit,
    )

    exported_ids = {r["event_id"] for r in records}
    names = {item["disease_id"]: item for item in disease_catalogue()}
    _write(
        out / "events.json",
        {
            "schema_version": SCHEMA_VERSION,
            "as_of": as_of.isoformat(),
            "events": [
                {
                    "event_id": event.event_id,
                    "disease_id": event.disease_id,
                    "disease_name_en": (names.get(event.disease_id) or {}).get("name_en"),
                    "disease_name_zh": (names.get(event.disease_id) or {}).get("name_zh"),
                    "disease_slug": (names.get(event.disease_id) or {}).get("slug"),
                    "country_codes": event.country_codes,
                    "first_published": event.first_published.isoformat(),
                    "last_published": event.last_published.isoformat(),
                    "notice_count": event.notice_count,
                    "notice_ids": [m.notice_id for m in event.members if m.notice_id in exported_ids],
                    "source_ids": event.source_ids,
                    "risk_level": event.risk_level,
                    "status": event.status,
                }
                for event in events
            ],
        },
    )

    digest_dir = out / "digests"
    digest_rows = []
    for row in digests:
        record = digest_record(row)
        # A digest may cite a notice the public export omits (no disease or
        # country). Keep only exported citations so every public point links
        # to a notice a reader can open; drop points left with none.
        points = []
        for point in record["points"]:
            cited = [nid for nid in point.get("notice_ids") or [] if nid in exported_ids]
            if cited:
                points.append({**point, "notice_ids": cited})
        if points:
            digest_rows.append({**record, "points": points})
    for record in digest_rows:
        _write(digest_dir / f"{record['period_key']}.json", {"schema_version": SCHEMA_VERSION, **record})
    _write(
        digest_dir / "index.json",
        {
            "schema_version": SCHEMA_VERSION,
            "periods": [
                {"period_key": r["period_key"], "headline_en": r["headline_en"], "headline_zh": r["headline_zh"], "headline_fr": r["headline_fr"]}
                for r in digest_rows
            ],
        },
    )
    summary["situation_events"] = len(events)
    summary["digests"] = len(digest_rows)
    return summary
