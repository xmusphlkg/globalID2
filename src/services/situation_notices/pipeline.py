"""Database pipeline for the notice-based Situation module.

Stages, each idempotent and safe to re-run:

1. :func:`store_notices` upserts collected authority records and their rule
   matched entities, and records per-source run health.
2. :func:`enrich_pending` runs peer-reviewed AI enrichment on every notice whose
   content changed since its last published assessment.
3. :func:`rebuild_events` clusters notices into disease x place events.
4. :func:`write_weekly_digest` writes the peer-reviewed brief for one ISO week.

The pure helpers (hashing, clustering) carry no database access so they can be
tested offline.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core import get_logger
from src.domain import (
    SituationAIAssessment,
    SituationDigest,
    SituationEvent,
    SituationEventNotice,
    SituationNotice,
    SituationNoticeEntity,
    SituationSourceRun,
)
from src.services.global_sources.catalogue import SOURCE_REGISTRY, country_catalogue, disease_catalogue

from .ai import (
    DIGEST_PROMPT_VERSION,
    NOTICE_PROMPT_VERSION,
    RISK_LEVELS,
    AIOutputError,
    Completer,
    enrich_notice,
    notice_input,
    write_digest,
)

logger = get_logger(__name__)

#: Notices on the same disease and place within this gap belong to one event.
EVENT_GAP_DAYS = 60
#: An event with no notice for this long is shown as quiet.
EVENT_QUIET_DAYS = 30
MAX_AI_ATTEMPTS = 4


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def content_hash(notice: dict[str, Any]) -> str:
    """Hash of exactly what the AI reads, so only real changes re-queue a notice."""

    payload = json.dumps(notice_input(notice), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _retry_delay(attempts: int) -> timedelta:
    return timedelta(minutes=min(24 * 60, 15 * 2 ** max(0, attempts - 1)))


# --- 1. store ---


async def store_notices(run: dict[str, Any], *, run_id: str) -> dict[str, int]:
    """Upsert one collection envelope; returns new/updated/unchanged counts."""

    now = utc_now()
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    per_source: dict[str, dict[str, int]] = {}
    async with get_db() as db:
        for event in run.get("events") or []:
            published = event.get("published_date")
            if not published:
                continue
            source_id = str(event["source_id"])
            source_counts = per_source.setdefault(source_id, {"new": 0, "updated": 0})
            notice_id = str(event["event_id"])
            digest = content_hash(event)
            row = (
                await db.execute(
                    select(SituationNotice)
                    .options(selectinload(SituationNotice.entities))
                    .where(SituationNotice.notice_id == notice_id)
                )
            ).scalar_one_or_none()
            if row is None:
                row = SituationNotice(
                    notice_id=notice_id,
                    source_id=source_id,
                    native_id=str(event["native_id"]),
                    first_seen_at=now,
                    ai_status="pending",
                    ai_attempts=0,
                    entities=[],
                )
                db.add(row)
                counts["new"] += 1
                source_counts["new"] += 1
            elif row.content_hash != digest:
                # New content: the AI must look again from scratch.
                row.ai_status = "pending"
                row.ai_attempts = 0
                row.ai_next_attempt_at = None
                counts["updated"] += 1
                source_counts["updated"] += 1
            else:
                counts["unchanged"] += 1
            row.record_type = str(event.get("record_type") or "other")
            row.title_en = str(event.get("title_en") or "")[:1000]
            row.summary_en = event.get("summary_en")
            row.url = str(event.get("url") or "")[:1500]
            row.language = str(event.get("language") or "en")
            row.redistribution = str((SOURCE_REGISTRY.get(source_id) or {}).get("redistribution") or "metadata_only")
            row.published_date = date.fromisoformat(str(published))
            row.updated_at_source = event.get("updated_at_source")
            row.iso_week_key = str(event.get("iso_week_key"))
            row.content_hash = digest
            row.last_seen_at = now
            _sync_rule_entities(row, event)
        for status in run.get("sources") or []:
            source_id = str(status.get("source_id"))
            db.add(
                SituationSourceRun(
                    run_id=run_id,
                    source_id=source_id,
                    started_at=now,
                    finished_at=utc_now(),
                    status=str(status.get("status") or "ok"),
                    fetched_count=int(status.get("event_count") or 0),
                    new_count=per_source.get(source_id, {}).get("new", 0),
                    updated_count=per_source.get(source_id, {}).get("updated", 0),
                    error=status.get("error"),
                )
            )
    return counts


def _sync_rule_entities(row: SituationNotice, event: dict[str, Any]) -> None:
    """Replace rule matches; AI-method entities are owned by the enrichment step."""

    wanted = {("disease", d["disease_id"]): d for d in event.get("diseases") or []}
    wanted.update({("country", c["code"]): c for c in event.get("countries") or []})
    existing = {(e.entity_type, e.entity_id): e for e in row.entities}
    for key, entity in existing.items():
        if entity.method == "rule" and key not in wanted:
            row.entities.remove(entity)
    for (entity_type, entity_id), source in wanted.items():
        if (entity_type, entity_id) in existing:
            continue
        row.entities.append(
            SituationNoticeEntity(
                entity_type=entity_type,
                entity_id=entity_id,
                name_en=source.get("name_en"),
                name_zh=source.get("name_zh"),
                method="rule",
                active=True,
            )
        )


# --- 2. AI enrichment ---


def allowed_diseases() -> list[dict[str, str]]:
    return [
        {"disease_id": row["disease_id"], "name_en": row["name_en"]}
        for row in disease_catalogue()
    ]


def _notice_payload(row: SituationNotice) -> dict[str, Any]:
    return {
        "notice_id": row.notice_id,
        "source_id": row.source_id,
        "source_label_en": (SOURCE_REGISTRY.get(row.source_id, {}).get("label") or {}).get("en"),
        "record_type": row.record_type,
        "published_date": row.published_date.isoformat(),
        "title_en": row.title_en,
        "summary_en": row.summary_en,
        "diseases": [
            {"disease_id": e.entity_id} for e in row.entities if e.entity_type == "disease" and e.method == "rule"
        ],
        "countries": [
            {"code": e.entity_id} for e in row.entities if e.entity_type == "country" and e.method == "rule"
        ],
    }


def apply_assessment_entities(row: SituationNotice, disease_ids: list[str], country_codes: list[str]) -> None:
    """Make the peer-reviewed extraction authoritative over keyword matches."""

    diseases = {item["disease_id"]: item for item in disease_catalogue()}
    countries = country_catalogue()
    reviewed = {("disease", d) for d in disease_ids} | {("country", c) for c in country_codes}
    existing = {(e.entity_type, e.entity_id): e for e in row.entities}
    for key, entity in existing.items():
        entity.active = key in reviewed
    for entity_type, entity_id in reviewed - set(existing):
        if entity_type == "disease":
            source = diseases.get(entity_id) or {}
            name_en, name_zh = source.get("name_en"), source.get("name_zh")
        else:
            source = countries.get(entity_id) or {}
            name_en, name_zh = source.get("name_en"), source.get("name_zh")
        row.entities.append(
            SituationNoticeEntity(
                entity_type=entity_type,
                entity_id=entity_id,
                name_en=name_en,
                name_zh=name_zh,
                method="ai",
                active=True,
            )
        )


async def requeue_stale_prompts() -> int:
    """Send notices assessed under an older prompt version back to the AI queue.

    Without this a prompt fix would only reach notices whose content changes.
    """

    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationNotice)
                .options(selectinload(SituationNotice.assessments))
                .where(SituationNotice.ai_status.in_(("enriched", "rejected")))
            )
        ).scalars().all()
        requeued = 0
        for row in rows:
            latest = max(row.assessments, key=lambda a: a.id or 0, default=None)
            if latest is not None and latest.prompt_version != NOTICE_PROMPT_VERSION:
                row.ai_status = "pending"
                row.ai_attempts = 0
                row.ai_next_attempt_at = None
                requeued += 1
    return requeued


async def enrich_pending(complete: Completer, *, limit: int = 50) -> dict[str, int]:
    """Peer-review every due notice; one failing notice never stops the batch."""

    counts = {"published": 0, "rejected": 0, "failed": 0}
    disease_rows = allowed_diseases()
    country_codes = set(country_catalogue())
    now = utc_now()
    async with get_db() as db:
        due = (
            await db.execute(
                select(SituationNotice.notice_id)
                .where(
                    SituationNotice.ai_status.in_(("pending", "failed")),
                    SituationNotice.ai_attempts < MAX_AI_ATTEMPTS,
                    (SituationNotice.ai_next_attempt_at.is_(None))
                    | (SituationNotice.ai_next_attempt_at <= now),
                )
                .order_by(SituationNotice.published_date.desc())
                .limit(limit)
            )
        ).scalars().all()

    for notice_id in due:
        # Each notice commits on its own so a crash keeps finished work.
        async with get_db() as db:
            row = (
                await db.execute(
                    select(SituationNotice)
                    .options(selectinload(SituationNotice.entities), selectinload(SituationNotice.assessments))
                    .where(SituationNotice.notice_id == notice_id)
                )
            ).scalar_one()
            row.ai_attempts += 1
            try:
                result = await enrich_notice(
                    complete,
                    _notice_payload(row),
                    allowed_diseases=disease_rows,
                    allowed_country_codes=country_codes,
                )
            except Exception as exc:  # noqa: BLE001 - recorded and retried with backoff
                logger.warning("Situation notice enrichment failed | notice={} error={}", notice_id, exc)
                row.ai_status = "failed"
                row.ai_last_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
                row.ai_next_attempt_at = utc_now() + _retry_delay(row.ai_attempts)
                counts["failed"] += 1
                continue
            for previous in row.assessments:
                if previous.status == "published":
                    previous.status = "superseded"
            payload = result.payload
            row.assessments.append(
                SituationAIAssessment(
                    content_hash=row.content_hash,
                    prompt_version=NOTICE_PROMPT_VERSION,
                    status=result.status,
                    title_zh=payload.get("title_zh"),
                    title_fr=payload.get("title_fr"),
                    summary_en=payload.get("summary_en"),
                    summary_zh=payload.get("summary_zh"),
                    summary_fr=payload.get("summary_fr"),
                    event_type=payload.get("event_type"),
                    risk_level=payload.get("risk_level"),
                    risk_rationale=payload.get("risk_rationale"),
                    disease_ids=payload.get("disease_ids") or [],
                    country_codes=payload.get("country_codes") or [],
                    author_model=result.author_model,
                    reviewer_model=result.reviewer_model,
                    review_verdict=result.verdict,
                    review_notes=result.notes,
                    review_rounds=result.rounds,
                )
            )
            if result.status == "published":
                apply_assessment_entities(row, payload.get("disease_ids") or [], payload.get("country_codes") or [])
                row.ai_status = "enriched"
                counts["published"] += 1
            else:
                # A reviewer rejection is a final answer for this content.
                row.ai_status = "rejected"
                counts["rejected"] += 1
            row.ai_last_error = None
            row.ai_next_attempt_at = None
    return counts


# --- 3. events ---


def _risk_rank(level: str | None) -> int:
    return RISK_LEVELS.index(level) if level in RISK_LEVELS else -1


def cluster_notices(notices: Iterable[dict[str, Any]], *, as_of: date) -> list[dict[str, Any]]:
    """Group notices into events keyed by disease and place, split by time gaps.

    A notice naming no country joins the disease's regional/global stream
    (``place == "*"``). Input rows: ``notice_id, source_id, published_date (date),
    disease_ids, country_codes, risk_level``.
    """

    streams: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for notice in notices:
        places = notice.get("country_codes") or ["*"]
        for disease_id in notice.get("disease_ids") or []:
            for place in places:
                streams.setdefault((disease_id, place), []).append(notice)

    events: list[dict[str, Any]] = []
    for (disease_id, place), rows in streams.items():
        rows.sort(key=lambda row: (row["published_date"], row["notice_id"]))
        current: list[dict[str, Any]] = []
        for row in rows:
            if current and (row["published_date"] - current[-1]["published_date"]).days > EVENT_GAP_DAYS:
                events.append(_event(disease_id, place, current, as_of))
                current = []
            current.append(row)
        if current:
            events.append(_event(disease_id, place, current, as_of))
    events.sort(key=lambda event: (event["last_published"], event["event_id"]), reverse=True)
    return events


def _event(disease_id: str, place: str, rows: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    first, last = rows[0]["published_date"], rows[-1]["published_date"]
    risk = max((row.get("risk_level") for row in rows), key=_risk_rank, default=None)
    return {
        # Stable across runs: the start date anchors the id.
        "event_id": f"{disease_id}:{place}:{first.isoformat()}",
        "disease_id": disease_id,
        "country_codes": [] if place == "*" else [place],
        "first_published": first,
        "last_published": last,
        "notice_ids": [row["notice_id"] for row in rows],
        "source_ids": sorted({row["source_id"] for row in rows}),
        "risk_level": risk if _risk_rank(risk) >= 0 else None,
        "status": "active" if (as_of - last).days <= EVENT_QUIET_DAYS else "quiet",
    }


async def _cluster_inputs(db: Any, since: date) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(SituationNotice)
            .options(selectinload(SituationNotice.entities), selectinload(SituationNotice.assessments))
            .where(SituationNotice.published_date >= since, SituationNotice.ai_status != "rejected")
        )
    ).scalars().all()
    inputs = []
    for row in rows:
        published = next((a for a in row.assessments if a.status == "published"), None)
        active = [e for e in row.entities if e.active]
        inputs.append(
            {
                "notice_id": row.notice_id,
                "source_id": row.source_id,
                "published_date": row.published_date,
                "disease_ids": [e.entity_id for e in active if e.entity_type == "disease"],
                "country_codes": [e.entity_id for e in active if e.entity_type == "country"],
                "risk_level": published.risk_level if published else None,
            }
        )
    return inputs


async def rebuild_events(*, since_days: int = 365, as_of: date | None = None) -> int:
    as_of = as_of or utc_now().date()
    since = as_of - timedelta(days=since_days)
    async with get_db() as db:
        clusters = cluster_notices(await _cluster_inputs(db, since), as_of=as_of)
        # Events are derived data: rebuild the window wholesale.
        await db.execute(delete(SituationEvent).where(SituationEvent.last_published >= since))
        for cluster in clusters:
            db.add(
                SituationEvent(
                    event_id=cluster["event_id"],
                    disease_id=cluster["disease_id"],
                    country_codes=cluster["country_codes"],
                    first_published=cluster["first_published"],
                    last_published=cluster["last_published"],
                    notice_count=len(cluster["notice_ids"]),
                    source_ids=cluster["source_ids"],
                    risk_level=cluster["risk_level"],
                    status=cluster["status"],
                    members=[SituationEventNotice(notice_id=nid) for nid in cluster["notice_ids"]],
                )
            )
    return len(clusters)


# --- 4. weekly digest ---


def digest_input_hash(notices: list[dict[str, Any]]) -> str:
    payload = json.dumps(notices, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{DIGEST_PROMPT_VERSION}|{payload}".encode("utf-8")).hexdigest()


async def write_weekly_digest(complete: Completer, period_key: str) -> str:
    """Write (or keep) the digest for ``period_key``; returns what happened."""

    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationNotice)
                .options(selectinload(SituationNotice.assessments))
                .where(SituationNotice.iso_week_key == period_key, SituationNotice.ai_status == "enriched")
                .order_by(SituationNotice.published_date.desc())
            )
        ).scalars().all()
        notices = []
        for row in rows:
            assessment = next((a for a in row.assessments if a.status == "published"), None)
            if assessment is None:
                continue
            notices.append(
                {
                    "notice_id": row.notice_id,
                    "source_id": row.source_id,
                    "published_date": row.published_date.isoformat(),
                    "title_en": row.title_en,
                    "summary_en": assessment.summary_en,
                    "event_type": assessment.event_type,
                    "risk_level": assessment.risk_level,
                    "disease_ids": assessment.disease_ids,
                    "country_codes": assessment.country_codes,
                }
            )
        if not notices:
            return "no_input"
        input_hash = digest_input_hash(notices)
        existing = (
            await db.execute(
                select(SituationDigest).where(
                    SituationDigest.period_key == period_key,
                    SituationDigest.input_hash == input_hash,
                    SituationDigest.status.in_(("published", "rejected")),
                )
            )
        ).scalars().first()
        if existing is not None:
            # Same input, same prompt: the reviewed answer already exists.
            return "unchanged"

    try:
        result = await write_digest(complete, period_key, notices)
    except (AIOutputError, Exception) as exc:  # noqa: BLE001 - next run retries
        logger.warning("Situation digest failed | period={} error={}", period_key, exc)
        return "failed"

    async with get_db() as db:
        if result.status == "published":
            for previous in (
                await db.execute(
                    select(SituationDigest).where(
                        SituationDigest.period_key == period_key, SituationDigest.status == "published"
                    )
                )
            ).scalars():
                previous.status = "superseded"
        payload = result.payload
        db.add(
            SituationDigest(
                period_key=period_key,
                input_hash=input_hash,
                prompt_version=DIGEST_PROMPT_VERSION,
                status=result.status,
                headline_en=payload.get("headline_en"),
                headline_zh=payload.get("headline_zh"),
                headline_fr=payload.get("headline_fr"),
                points=payload.get("points") or [],
                notice_count=len(notices),
                author_model=result.author_model,
                reviewer_model=result.reviewer_model,
                review_verdict=result.verdict,
                review_notes=result.notes,
                review_score=result.score,
            )
        )
    return result.status
