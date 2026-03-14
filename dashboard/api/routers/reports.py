"""Reports router – list, detail, sections, conversations, AI insights."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas.report import (
    AIConversationOut,
    AIInteractionOut,
    AIInteractionSummaryOut,
    ReportDetailOut,
    ReportOut,
    ReportSectionOut,
    ReportSectionRunOut,
)
from src.domain.country import Country
from src.domain.report import AIConversation, Report, ReportSection, ReportSectionRun

router = APIRouter()


def _to_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_total_tokens(tokens: Optional[dict]) -> int:
    if not isinstance(tokens, dict):
        return 0

    for key in ("total", "total_tokens", "sum"):
        v = _to_float(tokens.get(key))
        if v is not None:
            return int(v)

    total = 0
    for v in tokens.values():
        num = _to_float(v)
        if num is not None:
            total += int(num)
    return total


def _extract_quality_overall(quality_scores: Optional[dict]) -> Optional[float]:
    if not isinstance(quality_scores, dict):
        return None

    for key in ("overall", "total", "quality", "score"):
        v = _to_float(quality_scores.get(key))
        if v is not None:
            return v
    return None


@router.get("/reports", response_model=List[ReportOut])
async def list_reports(
    country_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(
            Report,
            Country.name.label("country_name"),
            func.count(ReportSection.id).label("section_count"),
        )
        .join(Country, Report.country_id == Country.id)
        .outerjoin(ReportSection, ReportSection.report_id == Report.id)
        .group_by(Report.id, Country.name)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )

    if country_id is not None:
        q = q.where(Report.country_id == country_id)
    if status:
        q = q.where(Report.status == status)

    rows = (await db.execute(q)).all()

    return [
        ReportOut(
            id=r.Report.id,
            report_uuid=str(r.Report.report_uuid),
            title=r.Report.title,
            report_type=r.Report.report_type,
            status=r.Report.status,
            country_id=r.Report.country_id,
            country_name=r.country_name,
            period_start=r.Report.period_start,
            period_end=r.Report.period_end,
            quality_score=r.Report.quality_score,
            generation_time=r.Report.generation_time,
            section_count=r.section_count,
            created_at=r.Report.created_at,
        )
        for r in rows
    ]


@router.get("/reports/{report_uuid}", response_model=ReportDetailOut)
async def get_report(report_uuid: str, db: AsyncSession = Depends(get_db)):
    q = (
        select(Report, Country.name.label("country_name"))
        .join(Country, Report.country_id == Country.id)
        .where(Report.report_uuid == report_uuid)
    )
    row = (await db.execute(q)).one_or_none()
    if not row:
        raise HTTPException(404, "Report not found")

    report = row.Report
    # Eager-load sections
    sec_q = (
        select(ReportSection)
        .where(ReportSection.report_id == report.id)
        .order_by(ReportSection.section_order)
    )
    sections = (await db.execute(sec_q)).scalars().all()

    return ReportDetailOut(
        id=report.id,
        report_uuid=str(report.report_uuid),
        title=report.title,
        report_type=report.report_type,
        status=report.status,
        country_id=report.country_id,
        country_name=row.country_name,
        period_start=report.period_start,
        period_end=report.period_end,
        summary=report.summary,
        key_findings=report.key_findings or [],
        recommendations=report.recommendations or [],
        quality_score=report.quality_score,
        generation_time=report.generation_time,
        token_usage=report.token_usage,
        ai_model_used=report.ai_model_used,
        html_path=report.html_path,
        pdf_path=report.pdf_path,
        markdown_path=report.markdown_path,
        error_message=report.error_message,
        created_at=report.created_at,
        sections=[
            ReportSectionOut(
                id=s.id,
                section_type=s.section_type,
                section_order=s.section_order,
                title=s.title,
                content=s.content,
                ai_model=s.ai_model,
                generation_time=s.generation_time,
                data_sources=s.data_sources,
                charts=s.charts,
                created_at=s.created_at,
            )
            for s in sections
        ],
    )


@router.get("/reports/{report_uuid}/runs", response_model=List[ReportSectionRunOut])
async def get_report_runs(report_uuid: str, db: AsyncSession = Depends(get_db)):
    report_q = select(Report.id).where(Report.report_uuid == report_uuid)
    report_id = (await db.execute(report_q)).scalar_one_or_none()
    if report_id is None:
        raise HTTPException(404, "Report not found")

    q = (
        select(ReportSectionRun)
        .where(ReportSectionRun.report_id == report_id)
        .order_by(ReportSectionRun.created_at)
    )
    return (await db.execute(q)).scalars().all()


@router.get(
    "/reports/{report_uuid}/sections/{section_id}/conversations",
    response_model=List[AIConversationOut],
)
async def get_section_conversations(
    report_uuid: str,
    section_id: int,
    db: AsyncSession = Depends(get_db),
):
    # Validate report exists
    report_q = select(Report.id).where(Report.report_uuid == report_uuid)
    report_id = (await db.execute(report_q)).scalar_one_or_none()
    if report_id is None:
        raise HTTPException(404, "Report not found")

    # Find matching run(s) for this section
    run_q = select(ReportSectionRun.id).where(
        ReportSectionRun.report_id == report_id,
        ReportSectionRun.section_id == section_id,
    )
    run_ids = (await db.execute(run_q)).scalars().all()
    if not run_ids:
        return []

    q = (
        select(AIConversation)
        .where(AIConversation.run_id.in_(run_ids))
        .order_by(AIConversation.timestamp)
    )
    return (await db.execute(q)).scalars().all()


@router.get("/ai/interactions", response_model=List[AIInteractionOut])
async def list_ai_interactions(
    country_id: Optional[int] = Query(None),
    report_uuid: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    disease: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    report_id = None
    if report_uuid:
        report_id = (
            await db.execute(select(Report.id).where(Report.report_uuid == report_uuid))
        ).scalar_one_or_none()
        if report_id is None:
            raise HTTPException(404, "Report not found")

    q = (
        select(AIConversation, ReportSectionRun, Report, ReportSection)
        .join(ReportSectionRun, AIConversation.run_id == ReportSectionRun.id)
        .join(Report, AIConversation.report_id == Report.id)
        .outerjoin(ReportSection, AIConversation.section_id == ReportSection.id)
        .order_by(AIConversation.timestamp.desc())
        .limit(limit)
    )

    if country_id is not None:
        q = q.where(Report.country_id == country_id)
    if report_id is not None:
        q = q.where(AIConversation.report_id == report_id)
    if agent:
        q = q.where(AIConversation.agent == agent)
    if model:
        q = q.where(AIConversation.model == model)
    if disease:
        q = q.where(ReportSectionRun.disease_name.ilike(f"%{disease}%"))

    rows = (await db.execute(q)).all()
    return [
        AIInteractionOut(
            id=row.AIConversation.id,
            report_id=row.Report.id,
            report_uuid=str(row.Report.report_uuid),
            report_status=row.Report.status,
            report_title=row.Report.title,
            country_id=row.Report.country_id,
            section_id=row.AIConversation.section_id,
            section_type=row.ReportSectionRun.section_type,
            section_title=row.ReportSection.title if row.ReportSection else None,
            disease_name=row.ReportSectionRun.disease_name,
            run_id=row.ReportSectionRun.id,
            run_uuid=row.ReportSectionRun.run_uuid,
            run_status=row.ReportSectionRun.status,
            run_model=row.ReportSectionRun.model,
            run_provider=row.ReportSectionRun.provider,
            run_temperature=row.ReportSectionRun.temperature,
            agent=row.AIConversation.agent,
            role=row.AIConversation.role,
            timestamp=row.AIConversation.timestamp,
            model=row.AIConversation.model,
            provider=row.AIConversation.provider,
            tokens=row.AIConversation.tokens,
            total_tokens=_extract_total_tokens(row.AIConversation.tokens),
            duration=row.AIConversation.duration,
            quality_scores=row.ReportSectionRun.quality_scores,
            quality_overall=_extract_quality_overall(row.ReportSectionRun.quality_scores),
            system_prompt=row.AIConversation.system_prompt,
            prompt=row.AIConversation.prompt,
            response=row.AIConversation.response,
            temperature=row.AIConversation.temperature,
        )
        for row in rows
    ]


@router.get("/ai/interactions/summary", response_model=AIInteractionSummaryOut)
async def get_ai_interactions_summary(
    country_id: Optional[int] = Query(None),
    report_uuid: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    disease: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    report_id = None
    if report_uuid:
        report_id = (
            await db.execute(select(Report.id).where(Report.report_uuid == report_uuid))
        ).scalar_one_or_none()
        if report_id is None:
            raise HTTPException(404, "Report not found")

    q = (
        select(AIConversation, ReportSectionRun, Report)
        .join(ReportSectionRun, AIConversation.run_id == ReportSectionRun.id)
        .join(Report, AIConversation.report_id == Report.id)
    )

    if country_id is not None:
        q = q.where(Report.country_id == country_id)
    if report_id is not None:
        q = q.where(AIConversation.report_id == report_id)
    if agent:
        q = q.where(AIConversation.agent == agent)
    if model:
        q = q.where(AIConversation.model == model)
    if disease:
        q = q.where(ReportSectionRun.disease_name.ilike(f"%{disease}%"))

    rows = (await db.execute(q)).all()

    if not rows:
        return AIInteractionSummaryOut(
            total_interactions=0,
            total_tokens=0,
            avg_tokens=0,
            avg_duration=0,
            avg_quality=None,
            by_agent={},
            by_model={},
        )

    total_tokens = 0
    durations: list[float] = []
    qualities: list[float] = []
    by_agent: dict[str, int] = {}
    by_model: dict[str, int] = {}

    for row in rows:
        total_tokens += _extract_total_tokens(row.AIConversation.tokens)

        duration = _to_float(row.AIConversation.duration)
        if duration is not None:
            durations.append(duration)

        quality = _extract_quality_overall(row.ReportSectionRun.quality_scores)
        if quality is not None:
            qualities.append(quality)

        agent_name = row.AIConversation.agent or "unknown"
        model_name = row.AIConversation.model or "unknown"
        by_agent[agent_name] = by_agent.get(agent_name, 0) + 1
        by_model[model_name] = by_model.get(model_name, 0) + 1

    total = len(rows)
    return AIInteractionSummaryOut(
        total_interactions=total,
        total_tokens=total_tokens,
        avg_tokens=total_tokens / total if total else 0,
        avg_duration=(sum(durations) / len(durations)) if durations else 0,
        avg_quality=(sum(qualities) / len(qualities)) if qualities else None,
        by_agent=by_agent,
        by_model=by_model,
    )
