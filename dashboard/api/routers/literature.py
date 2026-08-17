"""Research Radar catalogue, review, and synchronization endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.api.deps import get_db
from dashboard.api.schemas.control_plane import DataResponse, PaginationMeta, ResponseMeta
from dashboard.api.schemas.literature import (
    LiteratureArticleOut,
    LiteratureArticleUpdate,
    LiteratureAutomationRequest,
    LiteratureDashboardOut,
    LiteratureEnrichmentRequest,
    LiteratureEvidenceGapOut,
    LiteratureEvidenceLinkReview,
    LiteratureGapDiscoveryRequest,
    LiteratureGapUpdate,
    LiteratureIngestRunOut,
    LiteratureSyncOut,
    LiteratureSyncRequest,
)
from src.domain import (
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    LiteratureEvidenceGap,
    LiteratureIngestRun,
    LiteratureSignalArticleLink,
    LiteratureStatusEvent,
    LiteratureSummary,
    LiteratureTopicLink,
    StandardDisease,
)
from src.generation.site_data_literature import build_surveillance_evidence
from src.services.literature_service import literature_service
from src.services.literature_gap_service import literature_gap_service
from src.services.literature_automation_service import literature_automation_service
from src.services.situation_room import latest_snapshot


router = APIRouter(prefix="/research-radar")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _publication_blockers(article: LiteratureArticle, *, now: datetime) -> list[str]:
    blockers: list[str] = []
    if article.integrity_status != "current":
        blockers.append(f"integrity status is {article.integrity_status}")
    published_at = _aware(article.published_at)
    if published_at is None:
        blockers.append("publication date is missing")
    elif published_at > now:
        blockers.append("publication date is in the future")
    if article.peer_review_status != "peer_reviewed":
        blockers.append("record is not peer reviewed")
    if not (article.doi or article.pmid or article.pmcid):
        blockers.append("DOI/PMID/PMCID is missing")
    if len(str(article.title or "").strip()) < 20:
        blockers.append("title is incomplete")
    if not str(article.journal or "").strip():
        blockers.append("journal is missing")
    if not (article.authors or []):
        blockers.append("authors are missing")
    return blockers


def _meta(request: Request, *, page: int | None = None, page_size: int | None = None, total: int | None = None) -> ResponseMeta:
    pagination = None
    if page is not None and page_size is not None and total is not None:
        pagination = PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=math.ceil(total / page_size) if total else 0,
        )
    return ResponseMeta(request_id=getattr(request.state, "request_id", None), pagination=pagination)


def _editor_identity(request: Request) -> str:
    actor = getattr(request.state, "user", None)
    return str(getattr(actor, "email", None) or getattr(actor, "id", None) or "control-plane-editor")


async def _project_articles(db: AsyncSession, articles: list[LiteratureArticle]) -> list[dict]:
    ids = [article.article_id for article in articles]
    if not ids:
        return []
    disease_rows = (
        await db.execute(
            select(LiteratureDiseaseLink, StandardDisease)
            .join(StandardDisease, StandardDisease.disease_id == LiteratureDiseaseLink.disease_id)
            .where(LiteratureDiseaseLink.article_id.in_(ids))
        )
    ).all()
    country_rows = (
        await db.execute(select(LiteratureCountryLink).where(LiteratureCountryLink.article_id.in_(ids)))
    ).scalars().all()
    topic_rows = (
        await db.execute(select(LiteratureTopicLink).where(LiteratureTopicLink.article_id.in_(ids)))
    ).scalars().all()
    summary_rows = (
        await db.execute(select(LiteratureSummary).where(LiteratureSummary.article_id.in_(ids)))
    ).scalars().all()
    diseases: dict[str, list[dict]] = {article_id: [] for article_id in ids}
    countries: dict[str, list[dict]] = {article_id: [] for article_id in ids}
    topics: dict[str, list[dict]] = {article_id: [] for article_id in ids}
    summaries: dict[str, list[dict]] = {article_id: [] for article_id in ids}
    for link, disease in disease_rows:
        diseases[link.article_id].append({
            "disease_id": disease.disease_id,
            "name_en": disease.standard_name_en,
            "name_zh": disease.standard_name_zh,
            "confidence": link.confidence,
        })
    for link in country_rows:
        countries[link.article_id].append({
            "country_code": link.country_code,
            "country_name": link.country_name,
            "confidence": link.confidence,
        })
    for link in topic_rows:
        topics[link.article_id].append({"topic": link.topic, "confidence": link.confidence})
    for summary in summary_rows:
        summaries[summary.article_id].append({
            "language": summary.language,
            "research_question": summary.research_question,
            "study_design": summary.study_design,
            "population_setting": summary.population_setting,
            "main_findings": summary.main_findings,
            "public_health_relevance": summary.public_health_relevance,
            "limitations": summary.limitations,
            "gids_interpretation": summary.gids_interpretation,
            "status": summary.status,
            "generated_by": summary.generated_by,
            "model": summary.model,
            "provider": summary.provider,
            "quality_score": summary.quality_score,
            "evidence_map": summary.evidence_map or {},
            "generated_at": summary.generated_at,
            "review_notes": summary.review_notes,
        })
    return [
        {
            "article_id": article.article_id,
            "slug": article.slug,
            "doi": article.doi,
            "pmid": article.pmid,
            "title": article.title,
            "journal": article.journal,
            "publisher": article.publisher,
            "authors": article.authors or [],
            "article_type": article.article_type,
            "study_type": article.study_type,
            "published_at": article.published_at,
            "indexed_at": article.indexed_at,
            "open_access_status": article.open_access_status,
            "peer_review_status": article.peer_review_status,
            "integrity_status": article.integrity_status,
            "relevance_score": article.relevance_score,
            "public_health_score": article.public_health_score,
            "discovery_score": article.discovery_score,
            "publication_status": article.publication_status,
            "is_featured": article.is_featured,
            "diseases": diseases[article.article_id],
            "countries": countries[article.article_id],
            "topics": topics[article.article_id],
            "summaries": summaries[article.article_id],
            "created_at": article.created_at,
            "updated_at": article.updated_at,
        }
        for article in articles
    ]


@router.get("/dashboard", response_model=DataResponse[LiteratureDashboardOut])
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    counts = (
        await db.execute(
            select(
                func.count(LiteratureArticle.id).label("total"),
                func.count().filter(LiteratureArticle.publication_status == "published").label("published"),
                func.count().filter(LiteratureArticle.publication_status == "review").label("review"),
                func.count().filter(LiteratureArticle.publication_status == "excluded").label("excluded"),
                func.count().filter(LiteratureArticle.is_featured.is_(True)).label("featured"),
                func.count().filter(
                    LiteratureArticle.publication_status == "published",
                    LiteratureArticle.published_at >= cutoff,
                    LiteratureArticle.published_at <= now,
                ).label("recent"),
            )
        )
    ).one()
    latest = (
        await db.execute(select(LiteratureArticle).order_by(LiteratureArticle.indexed_at.desc()).limit(8))
    ).scalars().all()
    runs = (
        await db.execute(select(LiteratureIngestRun).order_by(LiteratureIngestRun.started_at.desc()).limit(6))
    ).scalars().all()
    summaries_awaiting_review = int((await db.execute(
        select(func.count()).select_from(LiteratureSummary).where(LiteratureSummary.status == "review")
    )).scalar_one() or 0)
    published_for_context = (
        await db.execute(
            select(LiteratureArticle)
            .where(
                LiteratureArticle.publication_status == "published",
                LiteratureArticle.integrity_status.notin_(("retracted", "expression_of_concern")),
                LiteratureArticle.peer_review_status == "peer_reviewed",
                LiteratureArticle.published_at.is_not(None),
                LiteratureArticle.published_at <= now,
            )
            .order_by(LiteratureArticle.published_at.desc())
            .limit(500)
        )
    ).scalars().all()
    situation_snapshot = await latest_snapshot()
    relation_decisions = (
        await db.execute(
            select(LiteratureSignalArticleLink).where(
                LiteratureSignalArticleLink.status.in_(("confirmed", "rejected"))
            )
        )
    ).scalars().all()
    surveillance_projection = build_surveillance_evidence(
        await _project_articles(db, published_for_context),
        situation_snapshot,
        relation_decisions=[
            {
                "signal_id": link.signal_id,
                "article_id": link.article_id,
                "relation_level": link.relation_level,
                "status": link.status,
            }
            for link in relation_decisions
        ],
    )
    gap_counts = (
        await db.execute(
            select(
                func.count(LiteratureEvidenceGap.id).filter(
                    LiteratureEvidenceGap.status.in_(("open", "searching", "no_results", "error"))
                ).label("open"),
                func.count(LiteratureEvidenceGap.id).filter(LiteratureEvidenceGap.status == "review").label("review"),
                func.count(LiteratureEvidenceGap.id).filter(LiteratureEvidenceGap.status == "covered").label("covered"),
                func.count(LiteratureEvidenceGap.id).filter(LiteratureEvidenceGap.status == "error").label("error"),
            )
        )
    ).one()
    link_review_count = int((
        await db.execute(
            select(func.count()).select_from(LiteratureSignalArticleLink).where(
                LiteratureSignalArticleLink.status == "review"
            )
        )
    ).scalar_one() or 0)
    schedule = await literature_service.snapshot_async()
    automation = await literature_automation_service.snapshot()
    payload = LiteratureDashboardOut(
        total_articles=int(counts.total or 0),
        published_articles=int(counts.published or 0),
        review_queue=int(counts.review or 0),
        excluded_articles=int(counts.excluded or 0),
        featured_articles=int(counts.featured or 0),
        published_last_7_days=int(counts.recent or 0),
        summaries_awaiting_review=summaries_awaiting_review,
        surveillance_context={
            "available": surveillance_projection["available"],
            "visibility": surveillance_projection["visibility"],
            "snapshot_id": surveillance_projection["snapshot_id"],
            "data_through": surveillance_projection["data_through"],
            "method_version": surveillance_projection["method_version"],
            "metrics": surveillance_projection["metrics"],
            "gap_lifecycle": {
                "open": int(gap_counts.open or 0),
                "review": int(gap_counts.review or 0),
                "covered": int(gap_counts.covered or 0),
                "error": int(gap_counts.error or 0),
                "links_awaiting_review": link_review_count,
            },
        },
        latest_articles=[LiteratureArticleOut.model_validate(item) for item in await _project_articles(db, latest)],
        latest_runs=[LiteratureIngestRunOut.model_validate(run, from_attributes=True) for run in runs],
        schedule=schedule,
        automation=automation,
    )
    return DataResponse(data=payload, meta=_meta(request))


@router.get("/articles", response_model=DataResponse[list[LiteratureArticleOut]])
async def list_articles(
    request: Request,
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if status:
        if status not in {"review", "published", "excluded"}:
            raise HTTPException(400, "Unsupported publication status")
        filters.append(LiteratureArticle.publication_status == status)
    if search:
        term = f"%{search.strip()}%"
        filters.append(LiteratureArticle.title.ilike(term) | LiteratureArticle.doi.ilike(term) | LiteratureArticle.journal.ilike(term))
    count_query = select(func.count()).select_from(LiteratureArticle)
    query = select(LiteratureArticle).order_by(LiteratureArticle.indexed_at.desc())
    if filters:
        count_query = count_query.where(*filters)
        query = query.where(*filters)
    total = int((await db.execute(count_query)).scalar_one() or 0)
    rows = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    data = [LiteratureArticleOut.model_validate(item) for item in await _project_articles(db, rows)]
    return DataResponse(data=data, meta=_meta(request, page=page, page_size=page_size, total=total))


@router.patch("/articles/{article_id}", response_model=DataResponse[LiteratureArticleOut])
async def update_article(
    article_id: str,
    body: LiteratureArticleUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    article = (
        await db.execute(select(LiteratureArticle).where(LiteratureArticle.article_id == article_id).with_for_update())
    ).scalar_one_or_none()
    if article is None:
        raise HTTPException(404, "Article not found")
    now = datetime.now(timezone.utc)
    publication_blockers = (
        _publication_blockers(article, now=now)
        if body.publication_status == "published"
        else []
    )
    if publication_blockers:
        raise HTTPException(409, "Article cannot be published: " + "; ".join(publication_blockers))
    previous_status = article.publication_status
    if body.publication_status is not None:
        article.publication_status = body.publication_status
    if body.is_featured is not None:
        if body.is_featured and article.publication_status != "published":
            raise HTTPException(409, "Only published articles can be featured")
        article.is_featured = body.is_featured
    metadata = dict(article.metadata_ or {})
    editor = _editor_identity(request)
    metadata["editorial_locked"] = True
    metadata["editorial_updated_at"] = now.isoformat()
    metadata["editorial_actor"] = editor
    if body.editorial_note:
        metadata["editorial_note"] = body.editorial_note
    article.metadata_ = metadata
    if body.summary is not None or body.summary_status is not None:
        if body.summary_language is None:
            raise HTTPException(400, "summary_language is required with summary changes")
        summary = (
            await db.execute(
                select(LiteratureSummary).where(
                    LiteratureSummary.article_id == article_id,
                    LiteratureSummary.language == body.summary_language,
                )
            )
        ).scalar_one_or_none()
        if summary is None:
            if body.summary is None:
                raise HTTPException(404, "Summary not found")
            summary = LiteratureSummary(article_id=article_id, language=body.summary_language)
            db.add(summary)
        allowed = {
            "research_question", "study_design", "population_setting", "main_findings",
            "public_health_relevance", "limitations", "gids_interpretation",
        }
        for key, value in (body.summary or {}).items():
            if key in allowed:
                setattr(summary, key, value)
        if body.summary_status == "published" and article.publication_status != "published":
            raise HTTPException(409, "Publish the article before publishing its evidence summary")
        if body.summary_status is not None:
            summary.status = body.summary_status
            generation_metadata = dict(summary.generation_metadata or {})
            generation_metadata["editorial_reviewed_at"] = now.isoformat()
            generation_metadata["editorial_decision"] = body.summary_status
            generation_metadata["editorial_actor"] = editor
            summary.generation_metadata = generation_metadata
        elif body.summary is not None:
            summary.status = "published" if article.publication_status == "published" else "draft"
        if body.summary is not None:
            summary.generated_by = "control-plane-editor"
    if previous_status != article.publication_status:
        db.add(LiteratureStatusEvent(
            article_id=article.article_id,
            event_type="publication_status_changed",
            previous_status=previous_status,
            current_status=article.publication_status,
            source="control-plane",
            effective_at=now,
            metadata_={
                "actor": editor,
                "note": body.editorial_note,
                "publication_gate": "manual-control-plane-gate",
            },
        ))
    await db.commit()
    await db.refresh(article)
    projection = (await _project_articles(db, [article]))[0]
    if previous_status != article.publication_status:
        await literature_gap_service.refresh_from_snapshot()
    return DataResponse(data=LiteratureArticleOut.model_validate(projection), meta=_meta(request))


@router.post("/sync", response_model=DataResponse[LiteratureSyncOut], status_code=202)
async def sync(body: LiteratureSyncRequest, request: Request):
    result = await literature_service.trigger_job(
        literature_service.JOB_ID,
        manual=True,
        since=body.since,
    )
    return DataResponse(data=LiteratureSyncOut.model_validate(result), meta=_meta(request))


@router.post("/automation/run", response_model=DataResponse[dict])
async def run_automation(body: LiteratureAutomationRequest, request: Request):
    result = await literature_automation_service.reconcile(
        dry_run=body.dry_run,
        export=body.export,
    )
    return DataResponse(data=result, meta=_meta(request))


@router.post("/enrich", response_model=DataResponse[LiteratureSyncOut], status_code=202)
async def enrich(body: LiteratureEnrichmentRequest, request: Request):
    try:
        result = await literature_service.trigger_enrichment(
            article_ids=body.article_ids,
            languages=list(body.languages),
            limit=body.limit,
            force=body.force,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return DataResponse(data=LiteratureSyncOut.model_validate(result), meta=_meta(request))


@router.post("/articles/{article_id}/enrich", response_model=DataResponse[LiteratureSyncOut], status_code=202)
async def enrich_article(article_id: str, request: Request):
    try:
        result = await literature_service.trigger_enrichment(article_ids=[article_id], limit=1)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return DataResponse(data=LiteratureSyncOut.model_validate(result), meta=_meta(request))


@router.get("/gaps", response_model=DataResponse[list[LiteratureEvidenceGapOut]])
async def list_evidence_gaps(
    request: Request,
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
):
    allowed = {"open", "searching", "review", "covered", "no_results", "dismissed", "error", "inactive"}
    if status and status not in allowed:
        raise HTTPException(400, "Unsupported evidence-gap status")
    total = await literature_gap_service.count_gaps(status=status)
    gaps = await literature_gap_service.list_gaps(status=status, limit=page_size, offset=(page - 1) * page_size)
    return DataResponse(
        data=[LiteratureEvidenceGapOut.model_validate(item) for item in gaps],
        meta=_meta(request, page=page, page_size=page_size, total=total),
    )


@router.post("/gaps/refresh", response_model=DataResponse[dict])
async def refresh_evidence_gaps(request: Request):
    result = await literature_gap_service.refresh_from_snapshot()
    return DataResponse(data=result, meta=_meta(request))


@router.post("/gaps/discover", response_model=DataResponse[LiteratureSyncOut], status_code=202)
async def discover_evidence_gaps(body: LiteratureGapDiscoveryRequest, request: Request):
    try:
        result = await literature_service.trigger_gap_discovery(
            manual=True,
            gap_ids=body.gap_ids,
            limit=body.limit,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return DataResponse(data=LiteratureSyncOut.model_validate(result), meta=_meta(request))


@router.patch("/gaps/{gap_id}", response_model=DataResponse[LiteratureEvidenceGapOut])
async def update_evidence_gap(gap_id: str, body: LiteratureGapUpdate, request: Request):
    try:
        result = await literature_gap_service.update_gap(gap_id, status=body.status, note=body.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return DataResponse(data=LiteratureEvidenceGapOut.model_validate(result), meta=_meta(request))


@router.patch("/evidence-links/{link_id}", response_model=DataResponse[dict])
async def review_evidence_link(
    link_id: int,
    body: LiteratureEvidenceLinkReview,
    request: Request,
):
    actor = getattr(request.state, "user", None)
    reviewer = str(getattr(actor, "email", None) or getattr(actor, "id", None) or "control-plane-editor")
    try:
        result = await literature_gap_service.review_link(
            link_id,
            status=body.status,
            relation_level=body.relation_level,
            reviewer=reviewer,
            note=body.note,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return DataResponse(data=result, meta=_meta(request))


__all__ = ["router"]
