"""Database pipeline for the alert-based Situation module.

Stages, each idempotent and safe to re-run:

1. :func:`store_notices` upserts collected reports and records per-source health.
2. :func:`fetch_pending_documents` downloads each report's full text (and the
   original PDF when there is one).
3. :func:`extract_pending` has two models read each document and turn it into
   alerts: one disease in one place, with the counts and a quoted sentence.
4. :func:`rebuild_outbreaks` groups alerts into outbreaks across places.

The pure helpers (hashing, ids, grouping) touch no database so they can be
tested offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import selectinload

from src.ai.attachments import PDF, Attachment
from src.ai.agents.base import NoModelAvailable
from src.ai.peer_review import SameModelReviewError
from src.ai.peer_review_completer import ReviewRoutePool
from src.core import get_logger
from src.core.database import get_db
from src.domain import (
    SituationAlert,
    SituationDocument,
    SituationExtraction,
    SituationNotice,
    SituationOutbreak,
    SituationSourceRun,
)
from src.services.global_sources.catalogue import SOURCE_REGISTRY, country_catalogue, disease_catalogue

from .ai import EXTRACTION_PROMPT_VERSION, Completer, extract_alerts, normalise
from src.services.global_sources.client import HostUnreachable

from .documents import DocumentUnavailable, FetchedDocument, fetch_document, load_original, store_original

logger = get_logger(__name__)

#: Alerts of one disease in one country within this gap belong to one outbreak.
OUTBREAK_GAP_DAYS = 60
#: An outbreak with no alert for this long is shown as quiet.
OUTBREAK_QUIET_DAYS = 30
MAX_DOCUMENT_ATTEMPTS = 4
MAX_AI_ATTEMPTS = 4
SAME_MODEL_RETRIES = 3
#: Reports downloaded at once; the HTTP client additionally caps each host.
DOWNLOAD_WORKERS = 6


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def content_hash(event: dict[str, Any]) -> str:
    """Hash of the catalogue fields; a change sends the report back through the pipeline."""

    payload = {
        key: event.get(key)
        for key in ("title_en", "summary_en", "published_date", "updated_at_source", "url", "document_ref")
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _retry_delay(attempts: int) -> timedelta:
    return timedelta(minutes=min(24 * 60, 15 * 2 ** max(0, attempts - 1)))


def _is_due(column: Any, now: datetime) -> Any:
    return or_(column.is_(None), column <= now)


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
            ref = dict(event.get("document_ref") or {})
            row = (
                await db.execute(
                    select(SituationNotice)
                    .options(selectinload(SituationNotice.document))
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
                )
                row.document = SituationDocument(ref=ref, status="pending", attempts=0)
                db.add(row)
                counts["new"] += 1
                source_counts["new"] += 1
            elif row.content_hash != digest:
                row.ai_status = "pending"
                row.ai_attempts = 0
                row.ai_next_attempt_at = None
                _requeue_document(row, ref)
                counts["updated"] += 1
                source_counts["updated"] += 1
            else:
                counts["unchanged"] += 1
            if not ref:
                # No body to read: the report stays in the catalogue only.
                row.document.status = "unavailable"
                row.ai_status = "unavailable"
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


def _requeue_document(row: SituationNotice, ref: dict[str, Any]) -> None:
    """A changed report is downloaded again; the old text stays until the new one arrives."""

    if row.document is None:
        row.document = SituationDocument(ref=ref, status="pending", attempts=0)
        return
    row.document.ref = ref
    row.document.status = "pending"
    row.document.attempts = 0
    row.document.next_attempt_at = None


# --- 2. documents ---


def _file_name(notice_id: str, media_type: str | None) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", notice_id).strip("-") or "report"
    return f"{stem}.pdf" if media_type == PDF else f"{stem}.txt"


async def fetch_pending_documents(
    *, limit: int = 40, fetch: Callable[[dict[str, Any] | None], FetchedDocument] = fetch_document
) -> dict[str, int]:
    """Download the body of every due report; one failing source never stops the batch."""

    counts = {"ready": 0, "unavailable": 0, "failed": 0}
    now = utc_now()
    async with get_db() as db:
        due = (
            await db.execute(
                select(SituationDocument.notice_id, SituationDocument.ref)
                .join(SituationNotice, SituationNotice.notice_id == SituationDocument.notice_id)
                .where(
                    SituationDocument.status.in_(("pending", "failed")),
                    SituationDocument.attempts < MAX_DOCUMENT_ATTEMPTS,
                    _is_due(SituationDocument.next_attempt_at, now),
                )
                .order_by(SituationNotice.published_date.desc())
                .limit(limit)
            )
        ).all()

    workers = asyncio.Semaphore(DOWNLOAD_WORKERS)

    async def download(notice_id: str, ref: dict[str, Any]) -> tuple[str, FetchedDocument | None, str | None, bool, float]:
        """``(notice_id, document, error, final, defer_seconds)``; never raises."""

        async with workers:
            try:
                # ``fetch`` is synchronous HTTP; each host is bounded by the HTTP client's guard.
                return notice_id, await asyncio.to_thread(fetch, ref), None, False, 0.0
            except DocumentUnavailable as exc:
                return notice_id, None, str(exc)[:500], True, 0.0
            except HostUnreachable as exc:
                return notice_id, None, str(exc)[:500], False, exc.retry_after
            except Exception as exc:  # noqa: BLE001 - recorded and retried with backoff
                logger.warning("Situation document fetch failed | notice={} error={}", notice_id, exc)
                return notice_id, None, f"{type(exc).__name__}: {str(exc)[:1000]}", False, 0.0

    for finished in asyncio.as_completed([download(notice_id, dict(ref or {})) for notice_id, ref in due]):
        notice_id, fetched, error, final, defer_seconds = await finished

        async with get_db() as db:
            row = (
                await db.execute(
                    select(SituationNotice)
                    .options(selectinload(SituationNotice.document))
                    .where(SituationNotice.notice_id == notice_id)
                )
            ).scalar_one()
            doc = row.document
            if defer_seconds:
                # The host could not be reached: that is not this report's fault,
                # so no attempt is spent; look again once the host may be back.
                doc.error = error
                doc.next_attempt_at = utc_now() + timedelta(seconds=defer_seconds)
                counts["deferred"] = counts.get("deferred", 0) + 1
                continue
            doc.attempts += 1
            if fetched is not None:
                digest = hashlib.sha256(fetched.text.encode("utf-8")).hexdigest()
                if doc.text_hash and doc.text_hash != digest:
                    # The agency changed the report: read it again.
                    row.ai_status = "pending"
                    row.ai_attempts = 0
                    row.ai_next_attempt_at = None
                doc.status = "ready"
                doc.media_type = fetched.media_type
                doc.text = fetched.text
                doc.text_hash = digest
                doc.page_count = fetched.page_count
                doc.byte_size = len(fetched.data) if fetched.data else len(fetched.text.encode("utf-8"))
                doc.cache_path = store_original(notice_id, fetched.data) if fetched.data else None
                doc.fetched_at = utc_now()
                doc.error = None
                doc.next_attempt_at = None
                counts["ready"] += 1
            elif final:
                doc.status = "unavailable"
                doc.error = error
                row.ai_status = "unavailable"
                counts["unavailable"] += 1
            else:
                doc.status = "failed"
                doc.error = error
                doc.next_attempt_at = utc_now() + _retry_delay(doc.attempts)
                counts["failed"] += 1
    return counts


# --- 3. extraction ---


def allowed_diseases() -> list[dict[str, str]]:
    return [{"disease_id": row["disease_id"], "name_en": row["name_en"]} for row in disease_catalogue()]


def disease_key(disease_id: str | None, label: str) -> str:
    """Identity of a disease for grouping: the catalogue id, else the report's own label."""

    return disease_id or f"label:{normalise(label)}"


def alert_id(notice_id: str, key: str, country: str, admin_area: str | None) -> str:
    raw = "|".join((notice_id, key, country, normalise(admin_area or "")))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _notice_payload(row: SituationNotice) -> dict[str, Any]:
    return {
        "notice_id": row.notice_id,
        "source_id": row.source_id,
        "source_label_en": (SOURCE_REGISTRY.get(row.source_id, {}).get("label") or {}).get("en"),
        "record_type": row.record_type,
        "published_date": row.published_date.isoformat(),
        "title_en": row.title_en,
    }


def _attachment(row: SituationNotice) -> Attachment:
    doc = row.document
    media_type = doc.media_type or "text/plain"
    data = load_original(doc.cache_path) if media_type == PDF else None
    return Attachment(name=_file_name(row.notice_id, media_type), text=doc.text or "", media_type=media_type, data=data)


def build_alerts(row: SituationNotice, payload: dict[str, Any]) -> list[SituationAlert]:
    alerts = []
    for item in payload.get("alerts") or []:
        key = disease_key(item.get("disease_id"), item["disease_label"])
        as_of = item.get("as_of")
        alerts.append(
            SituationAlert(
                alert_id=alert_id(row.notice_id, key, item["country_code"], item.get("admin_area")),
                notice_id=row.notice_id,
                disease_id=item.get("disease_id"),
                disease_label=item["disease_label"],
                country_code=item["country_code"],
                admin_area=item.get("admin_area"),
                status=item["status"],
                cases=item.get("cases"),
                deaths=item.get("deaths"),
                count_basis=item.get("count_basis"),
                as_of=date.fromisoformat(as_of) if as_of else None,
                quote=item["quote"],
                summary_en=item.get("summary_en"),
                summary_zh=item.get("summary_zh"),
                summary_fr=item.get("summary_fr"),
                published_date=row.published_date,
                source_id=row.source_id,
            )
        )
    return alerts


async def requeue_stale_prompts() -> int:
    """Send reports read under an older prompt version back to the AI queue.

    Without this a prompt fix would only reach reports whose text changes.
    """

    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationNotice)
                .options(selectinload(SituationNotice.extractions))
                .where(SituationNotice.ai_status.in_(("extracted", "rejected")))
            )
        ).scalars().all()
        requeued = 0
        for row in rows:
            latest = max(row.extractions, key=lambda item: item.id or 0, default=None)
            if latest is not None and latest.prompt_version != EXTRACTION_PROMPT_VERSION:
                row.ai_status = "pending"
                row.ai_attempts = 0
                row.ai_next_attempt_at = None
                requeued += 1
    return requeued


async def _extract_with_retry(complete: Completer | ReviewRoutePool, *args: Any, **kwargs: Any) -> Any:
    """With a route pool, redo a review that failover collapsed onto one model."""

    if not isinstance(complete, ReviewRoutePool):
        return await extract_alerts(complete, *args, **kwargs)
    avoid: set[str] = set()
    for attempt in range(SAME_MODEL_RETRIES + 1):
        try:
            return await extract_alerts(complete.completer(avoid), *args, **kwargs)
        except SameModelReviewError as exc:
            if attempt >= SAME_MODEL_RETRIES:
                raise
            avoid.add(exc.model)
    raise AssertionError("unreachable")


async def extract_pending(complete: Completer | ReviewRoutePool, *, limit: int = 30) -> dict[str, int]:
    """Peer-review every due report; one failing report never stops the batch."""

    counts = {"published": 0, "rejected": 0, "failed": 0, "unchanged": 0}
    diseases = allowed_diseases()
    country_codes = set(country_catalogue())
    now = utc_now()
    async with get_db() as db:
        due = (
            await db.execute(
                select(SituationNotice.notice_id)
                .join(SituationDocument, SituationDocument.notice_id == SituationNotice.notice_id)
                .where(
                    SituationNotice.ai_status.in_(("pending", "failed")),
                    SituationNotice.ai_attempts < MAX_AI_ATTEMPTS,
                    _is_due(SituationNotice.ai_next_attempt_at, now),
                    SituationDocument.status == "ready",
                )
                .order_by(SituationNotice.published_date.desc())
                .limit(limit)
            )
        ).scalars().all()

    for notice_id in due:
        # Each report commits on its own so a crash keeps finished work.
        async with get_db() as db:
            row = (
                await db.execute(
                    select(SituationNotice)
                    .options(selectinload(SituationNotice.document), selectinload(SituationNotice.extractions))
                    .where(SituationNotice.notice_id == notice_id)
                )
            ).scalar_one()
            row.ai_attempts += 1
            current = next((item for item in row.extractions if item.status == "published"), None)
            if (
                current is not None
                and current.document_hash == row.document.text_hash
                and current.prompt_version == EXTRACTION_PROMPT_VERSION
            ):
                row.ai_status = "extracted"
                counts["unchanged"] += 1
                continue
            try:
                result = await _extract_with_retry(
                    complete,
                    _notice_payload(row),
                    _attachment(row),
                    allowed_diseases=diseases,
                    allowed_country_codes=country_codes,
                )
            except NoModelAvailable as exc:
                # Every route is cooling down: this report did nothing wrong, so it
                # keeps its attempt and the rest of the batch waits for the next run.
                logger.warning("Situation extraction paused, no model route available | {}", exc)
                row.ai_attempts -= 1
                counts["deferred"] = counts.get("deferred", 0) + 1
                break
            except Exception as exc:  # noqa: BLE001 - recorded and retried with backoff
                logger.warning("Situation extraction failed | notice={} error={}", notice_id, exc)
                row.ai_status = "failed"
                row.ai_last_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
                row.ai_next_attempt_at = utc_now() + _retry_delay(row.ai_attempts)
                counts["failed"] += 1
                continue

            payload = result.payload
            extraction = SituationExtraction(
                notice_id=row.notice_id,
                document_hash=row.document.text_hash,
                prompt_version=EXTRACTION_PROMPT_VERSION,
                status=result.status,
                document_type=payload.get("document_type"),
                input_mode=result.input_mode,
                alert_count=len(payload.get("alerts") or []) if result.published else 0,
                author_model=result.author_model,
                reviewer_model=result.reviewer_model,
                review_verdict=result.verdict,
                review_notes=result.notes,
                review_rounds=result.rounds,
                review_score=result.score,
            )
            if result.published:
                # The new reading replaces the old one; a rejected reading never
                # takes down alerts an earlier accepted reading published.
                for previous in row.extractions:
                    if previous.status == "published":
                        previous.status = "superseded"
                await db.execute(delete(SituationAlert).where(SituationAlert.notice_id == row.notice_id))
                extraction.alerts = build_alerts(row, payload)
                row.ai_status = "extracted"
                counts["published"] += 1
            else:
                row.ai_status = "rejected"
                counts["rejected"] += 1
            row.extractions.append(extraction)
            row.ai_last_error = None
            row.ai_next_attempt_at = None
    return counts


# --- 4. outbreaks ---


def cluster_alerts(alerts: Iterable[dict[str, Any]], *, as_of: date) -> list[dict[str, Any]]:
    """Group alerts into outbreaks.

    Two alerts of one disease belong together when one report names both (a
    notice about three countries is one outbreak) or when they are in the same
    country within :data:`OUTBREAK_GAP_DAYS` of each other.  Input rows:
    ``alert_id, notice_id, source_id, published_date (date), disease_id,
    disease_label, country_code, status``.  Routine alerts are not outbreaks.
    """

    by_disease: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for alert in alerts:
        if alert["status"] == "routine":
            continue
        by_disease[disease_key(alert.get("disease_id"), alert["disease_label"])].append(alert)

    outbreaks: list[dict[str, Any]] = []
    for key, rows in by_disease.items():
        rows.sort(key=lambda item: (item["published_date"], item["alert_id"]))
        parent = list(range(len(rows)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            parent[find(right)] = find(left)

        first_in_notice: dict[str, int] = {}
        latest_in_country: dict[str, int] = {}
        for index, row in enumerate(rows):
            # Only a report about one event ties its countries together; a
            # periodic roundup lists many unrelated national situations.
            if row.get("document_type", "event_report") == "event_report":
                if row["notice_id"] in first_in_notice:
                    union(first_in_notice[row["notice_id"]], index)
                else:
                    first_in_notice[row["notice_id"]] = index
            # An ended alert is the last word for that place: a later alert there
            # starts a new outbreak, and an ended place never joins a live one.
            earlier = latest_in_country.get(row["country_code"])
            if earlier is not None and (row["published_date"] - rows[earlier]["published_date"]).days <= OUTBREAK_GAP_DAYS:
                union(earlier, index)
            if row["status"] == "ended":
                latest_in_country.pop(row["country_code"], None)
            else:
                latest_in_country[row["country_code"]] = index

        groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for index, row in enumerate(rows):
            groups[find(index)].append(row)
        for members in groups.values():
            outbreaks.append(_outbreak(key, members, as_of))
    outbreaks.sort(key=lambda item: (item["last_published"], item["outbreak_id"]), reverse=True)
    return outbreaks


def _outbreak(key: str, members: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    anchor = members[0]
    last = members[-1]["published_date"]
    latest_by_country: dict[str, dict[str, Any]] = {}
    for member in members:
        latest_by_country[member["country_code"]] = member
    over = all(item["status"] == "ended" for item in latest_by_country.values())
    catalogue_ids = Counter(item["disease_id"] for item in members if item.get("disease_id"))
    return {
        # Anchored on the earliest alert, so the id survives later additions.
        "outbreak_id": "OB-" + hashlib.sha1(f"{key}|{anchor['alert_id']}".encode("utf-8")).hexdigest()[:12],
        "disease_id": catalogue_ids.most_common(1)[0][0] if catalogue_ids else None,
        "disease_label": anchor["disease_label"],
        "first_published": anchor["published_date"],
        "last_published": last,
        "country_codes": sorted(latest_by_country),
        "alert_ids": [item["alert_id"] for item in members],
        "notice_ids": sorted({item["notice_id"] for item in members}),
        "source_ids": sorted({item["source_id"] for item in members}),
        "status": "quiet" if over or (as_of - last).days > OUTBREAK_QUIET_DAYS else "active",
    }


async def rebuild_outbreaks(*, since_days: int = 365, as_of: date | None = None) -> int:
    """Derive outbreaks from the alerts; derived data, so the table is rebuilt wholesale."""

    as_of = as_of or utc_now().date()
    since = as_of - timedelta(days=since_days)
    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationAlert)
                .options(selectinload(SituationAlert.extraction))
                .where(SituationAlert.published_date >= since)
            )
        ).scalars().all()
        clusters = cluster_alerts(
            (
                {
                    "alert_id": row.alert_id,
                    "notice_id": row.notice_id,
                    "source_id": row.source_id,
                    "published_date": row.published_date,
                    "disease_id": row.disease_id,
                    "disease_label": row.disease_label,
                    "country_code": row.country_code,
                    "status": row.status,
                    "document_type": (row.extraction.document_type if row.extraction else None) or "other",
                }
                for row in rows
            ),
            as_of=as_of,
        )
        await db.execute(update(SituationAlert).values(outbreak_id=None))
        await db.execute(delete(SituationOutbreak))
        for cluster in clusters:
            db.add(
                SituationOutbreak(
                    outbreak_id=cluster["outbreak_id"],
                    disease_id=cluster["disease_id"],
                    disease_label=cluster["disease_label"],
                    first_published=cluster["first_published"],
                    last_published=cluster["last_published"],
                    country_codes=cluster["country_codes"],
                    alert_count=len(cluster["alert_ids"]),
                    notice_count=len(cluster["notice_ids"]),
                    source_ids=cluster["source_ids"],
                    status=cluster["status"],
                )
            )
        await db.flush()
        for cluster in clusters:
            await db.execute(
                update(SituationAlert)
                .where(SituationAlert.alert_id.in_(cluster["alert_ids"]))
                .values(outbreak_id=cluster["outbreak_id"])
            )
    return len(clusters)
