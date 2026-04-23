"""
Report Service

Encapsulates all business logic for AI-driven report generation,
decoupling it from the CLI layer in main.py.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from sqlalchemy import func, select

from src.core import get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import (
    Country,
    DiseaseRecord,
    Report,
    ReportSection,
    ReportStatus,
    ReportType,
    Task,
)
from src.services.exceptions import TaskCancelledError

logger = get_logger(__name__)


@dataclass
class ReportResult:
    report_id: int
    report_uuid: str
    status: str
    output_files: List[str] = field(default_factory=list)
    sections_count: int = 0
    reused: bool = False
    email_delivery: Optional[dict[str, Any]] = None


DataSignature = Tuple[int, int, Optional[datetime], Optional[datetime], str]


@dataclass
class ReuseCandidate:
    report: Report
    section_count: int
    signature: DataSignature
    score: float
    reason: str


class ReportService:
    """Orchestrates the multi-phase AI report-generation pipeline."""

    async def execute(
        self,
        task: Task,
        country_code: str,
        report_type: str,
        period_start_iso: Optional[str],
        period_end_iso: Optional[str],
        language: str,
        days: int,
        enable_review: bool,
        send_email: bool,
        reuse_from_failed: bool = True,
        reuse_strategy: str = "auto",
        reuse_report_id: Optional[int] = None,
        report_id_ref: Optional[list] = None,
    ) -> ReportResult:
        """
        Run the full report-generation pipeline and return a summary.

        Progress is reported via task_manager (0 → 100 %).
        Raises on unrecoverable errors (caller handles via task_lifecycle).
        """
        from src.ai.model_check import ensure_available_models_checked_async
        from src.generation import ReportGenerator

        language = (language or "en").strip().lower()
        if language not in {"zh", "en"}:
            language = "en"

        # Check model availability once
        await ensure_available_models_checked_async()

        async with get_database() as db:
            # ── Resolve effective time range ──────────────────────────────────
            period_end = (
                datetime.fromisoformat(period_end_iso).replace(tzinfo=timezone.utc)
                if period_end_iso
                else datetime.now(timezone.utc)
            )
            period_start = (
                datetime.fromisoformat(period_start_iso).replace(tzinfo=timezone.utc)
                if period_start_iso
                else period_end - timedelta(days=days)
            )

            # ── Phase 0: Resolve country / type and prepare data window ──────
            country_obj = await self._get_country(db, country_code)
            report_type_enum = ReportType[report_type.upper()]

            # ── Phase 1: Data preparation (0 → 15 %) ─────────────────────────
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 1/5: Data Preparation",
                content="Fetching country and disease records...",
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 5)

            period_start, period_end = await self._adjust_period_to_available_data(
                db, task, country_obj, period_start, period_end, days
            )
            await self._ensure_task_not_cancelled(task.task_uuid)

            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Data Preparation Complete",
                content=(
                    f"Country: {country_obj.name}\n"
                    f"Period: {period_start.date()} to {period_end.date()}"
                ),
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 15)

            strategy = self._normalize_reuse_strategy(reuse_strategy)
            if not reuse_from_failed:
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="info",
                    title="Resume Disabled",
                    content=(
                        "Reuse from failed tasks is disabled for this run. "
                        "Only approved reports are eligible for reuse."
                    ),
                    content_type="text",
                )

            attached_report = None
            if reuse_from_failed:
                attached_report = await self._resolve_task_attached_report(
                    db,
                    task,
                    country_obj=country_obj,
                    report_type=report_type_enum,
                )

            selected_candidate, evaluated_candidates = await self._select_reuse_candidate(
                db,
                country=country_obj,
                report_type=report_type_enum,
                period_start=period_start,
                period_end=period_end,
                reuse_from_failed=reuse_from_failed,
                reuse_strategy=strategy,
                reuse_report_id=reuse_report_id,
                attached_report=attached_report,
            )

            if evaluated_candidates:
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="info",
                    title="Reuse Candidates Evaluated",
                    content="\n".join(
                        self._format_candidate_summary(c) for c in evaluated_candidates[:8]
                    ),
                    content_type="text",
                )

            if strategy == "manual" and reuse_report_id is None:
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="warning",
                    title="Manual Reuse Requires Report ID",
                    content=(
                        "Reuse strategy is manual but reuse_report_id is missing. "
                        "Proceeding with fresh generation."
                    ),
                    content_type="text",
                )

            resumable_report = None
            if selected_candidate:
                selected = selected_candidate.report
                if report_id_ref is not None:
                    report_id_ref[0] = selected.id
                await task_manager.link_task_report(task.task_uuid, selected.id)

                if self._status_str(selected.status) == ReportStatus.APPROVED.value:
                    output_files = self._collect_output_files(selected)
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="success",
                        title="Report Reused",
                        content=(
                            f"Reused report #{selected.id} ({selected.status}) via strategy={strategy}.\n"
                            f"Reason: {selected_candidate.reason}"
                        ),
                        content_type="text",
                    )
                    email_delivery = None
                    if send_email:
                        email_delivery = await self._send_reused_report_email(db, selected)
                        await self._log_report_email_delivery(task.task_uuid, email_delivery)
                    await task_manager.update_task_progress(task.task_uuid, 100)
                    return ReportResult(
                        report_id=selected.id,
                        report_uuid=str(selected.report_uuid),
                        status=str(selected.status),
                        output_files=output_files,
                        sections_count=selected_candidate.section_count,
                        reused=True,
                        email_delivery=email_delivery,
                    )

                resumable_report = selected
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="warning",
                    title="Resuming Partial Report",
                    content=(
                        f"Resuming report #{selected.id} ({selected.status}) via strategy={strategy}.\n"
                        f"Reason: {selected_candidate.reason}"
                    ),
                    content_type="text",
                )

            await self._ensure_task_not_cancelled(task.task_uuid)

            # ── Phase 2: AI analysis + content generation (15 → 80 %) ────────
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 2/5: AI Analysis & Generation",
                content="Starting multi-agent AI workflow (Analyst → Writer → Reviewer)...",
                content_type="text",
            )

            generator = ReportGenerator()

            async def _ai_progress(stage, current, total, message):
                stage_base = {
                    "data_extraction": 15,
                    "analysis": 40,
                    "writing": 60,
                    "review": 75,
                }
                base = stage_base.get(stage, 15)
                pct = base + int((current / total) * 10) if total > 0 else base
                await task_manager.update_task_progress(task.task_uuid, min(pct, 80))
                if current % 5 == 0 or current == total:
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="info",
                        title=f"{stage.replace('_', ' ').title()} Progress",
                        content=f"{message} ({current}/{total})",
                        content_type="text",
                    )

            report = await generator.generate(
                country_id=country_obj.id,
                report_type=report_type_enum,
                period_start=period_start,
                period_end=period_end,
                language=language,
                existing_report=resumable_report,
                send_email=send_email,
                enable_review=enable_review,
                progress_callback=_ai_progress,
                task_uuid=task.task_uuid,
                db=db,
            )

            if report_id_ref is not None:
                report_id_ref[0] = report.id
            await task_manager.link_task_report(task.task_uuid, report.id)

            if report.status == ReportStatus.FAILED:
                raise RuntimeError(
                    f"Report generation failed: {report.error_message or 'Unknown error'}"
                )

            await self._ensure_task_not_cancelled(task.task_uuid)

            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="AI Generation Complete",
                content=f"Report ID: {report.id}",
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 80)

            # ── Phase 3: Format & export (80 → 90 %) ─────────────────────────
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 3/5: Format & Export",
                content="Generating Markdown / HTML / PDF outputs...",
                content_type="text",
            )
            output_files = self._collect_output_files(report)
            email_delivery = self._email_delivery_from_report(report) if send_email else None
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Export Complete",
                content="\n".join(output_files) if output_files else "No files exported",
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 90)

            # ── Phase 4: Email (90 → 95 %) ────────────────────────────────────
            if send_email:
                await self._log_report_email_delivery(task.task_uuid, email_delivery)
            await task_manager.update_task_progress(task.task_uuid, 95)

            # ── Phase 5: Finalise ─────────────────────────────────────────────
            sections_count = await self._get_report_section_count(db, report.id)
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Report Generation Completed",
                content=f"Report ID: {report.id}\nStatus: {report.status}\nFiles: {len(output_files)}",
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 100)

            return ReportResult(
                report_id=report.id,
                report_uuid=str(report.report_uuid),
                status=str(report.status),
                output_files=output_files,
                sections_count=sections_count,
                email_delivery=email_delivery,
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_country(self, db, country_code: str) -> Country:
        result = await db.execute(select(Country).where(Country.code == country_code))
        country = result.scalar_one_or_none()
        if not country:
            raise ValueError(f"Country not found: {country_code}")
        return country

    @staticmethod
    def _email_delivery_from_report(report: Report) -> Optional[dict[str, Any]]:
        generation_config = report.generation_config if isinstance(report.generation_config, dict) else {}
        delivery = generation_config.get("email_delivery") if isinstance(generation_config, dict) else None
        return dict(delivery) if isinstance(delivery, dict) else None

    async def _log_report_email_delivery(
        self,
        task_uuid: str,
        email_delivery: Optional[dict[str, Any]],
    ) -> None:
        if not email_delivery:
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="warning",
                title="Email Skipped",
                content="Email was requested, but no delivery result was recorded.",
                content_type="text",
            )
            return

        recipients = ", ".join(email_delivery.get("recipients") or []) or "-"
        subject = str(email_delivery.get("subject") or "-")
        detail = str(email_delivery.get("message") or email_delivery.get("reason") or "No detail")
        checked_at = str(email_delivery.get("checked_at") or "-")

        if email_delivery.get("sent"):
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="success",
                title="Email Sent",
                content=(
                    f"Recipients: {recipients}\n"
                    f"Subject: {subject}\n"
                    f"Checked At: {checked_at}\n"
                    f"Result: {detail}"
                ),
                content_type="text",
            )
            return

        reason = str(email_delivery.get("reason") or "send_failed")
        entry_type = "warning" if reason in {"smtp_not_configured", "missing_recipients"} else "error"
        title = "Email Skipped" if entry_type == "warning" else "Email Failed"
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type=entry_type,
            title=title,
            content=(
                f"Recipients: {recipients}\n"
                f"Subject: {subject}\n"
                f"Checked At: {checked_at}\n"
                f"Reason: {detail}"
            ),
            content_type="text",
        )

    async def _send_reused_report_email(self, db, report: Report) -> dict[str, Any]:
        from src.generation.email_service import EmailService
        from src.generation.formatter import ReportFormatter

        try:
            if report.html_path and Path(report.html_path).exists():
                html_content = Path(report.html_path).read_text(encoding="utf-8")
            else:
                sections = (
                    await db.execute(
                        select(ReportSection)
                        .where(ReportSection.report_id == report.id)
                        .order_by(ReportSection.section_order.asc())
                    )
                ).scalars().all()
                country = await db.get(Country, report.country_id)
                formatter = ReportFormatter()
                html_content = formatter.format_html(
                    [
                        {
                            "title": section.title,
                            "content": section.content,
                            "content_html": section.content_html,
                        }
                        for section in sections
                    ],
                    {
                        "title": report.title,
                        "generated_at": (report.updated_at or report.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                        "period_start": report.period_start.date().isoformat(),
                        "period_end": report.period_end.date().isoformat(),
                        "country": country.name_en if country else "-",
                    },
                )
            delivery = EmailService().send_report_to_settings_recipients(
                report_title=report.title,
                report_html=html_content,
                pdf_path=report.pdf_path,
            )
        except Exception as exc:
            delivery = {
                "requested": True,
                "sent": False,
                "recipients": [],
                "subject": f"[GlobalID] {report.title}",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "reason": "render_failed",
                "message": f"Failed to prepare reused report email: {exc}",
            }

        generation_config = dict(report.generation_config or {})
        generation_config["email_delivery"] = delivery
        report.generation_config = generation_config
        db.add(report)
        await db.commit()
        await db.refresh(report)
        return delivery

    @staticmethod
    def _normalize_reuse_strategy(value: str) -> str:
        normalized = (value or "auto").strip().lower()
        allowed = {"auto", "safe", "resume", "manual"}
        if normalized not in allowed:
            joined = ", ".join(sorted(allowed))
            raise ValueError(f"Invalid reuse_strategy '{value}'. Allowed values: {joined}")
        return normalized

    async def _select_reuse_candidate(
        self,
        db,
        *,
        country: Country,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
        reuse_from_failed: bool,
        reuse_strategy: str,
        reuse_report_id: Optional[int],
        attached_report: Optional[Report],
    ) -> tuple[Optional[ReuseCandidate], list[ReuseCandidate]]:
        target_signature = await self._compute_data_signature(
            db,
            country_id=country.id,
            period_start=period_start,
            period_end=period_end,
        )

        statuses: list[ReportStatus] = [ReportStatus.APPROVED]
        if reuse_from_failed and reuse_strategy in {"auto", "resume", "manual"}:
            statuses.extend([ReportStatus.GENERATING, ReportStatus.FAILED])

        candidates = await self._collect_reuse_candidates(
            db,
            country=country,
            report_type=report_type,
            statuses=statuses,
            target_signature=target_signature,
            strategy=reuse_strategy,
        )

        if attached_report and attached_report.id not in {c.report.id for c in candidates}:
            attached_sig = await self._compute_data_signature(
                db,
                country_id=country.id,
                period_start=attached_report.period_start,
                period_end=attached_report.period_end,
            )
            if attached_sig == target_signature:
                attached_sections = await self._get_report_section_count(db, attached_report.id)
                attached_candidate = ReuseCandidate(
                    report=attached_report,
                    section_count=attached_sections,
                    signature=attached_sig,
                    score=self._score_reuse_candidate(attached_report, attached_sections, reuse_strategy),
                    reason="attached-to-task",
                )
                candidates.append(attached_candidate)

        candidates.sort(key=lambda c: c.score, reverse=True)

        if reuse_strategy == "manual":
            if reuse_report_id is None:
                return None, candidates
            manual = next((c for c in candidates if c.report.id == reuse_report_id), None)
            if manual:
                manual.reason = f"manual-selected id={reuse_report_id}"
                return manual, candidates

            explicit = await db.get(Report, reuse_report_id)
            if explicit is None:
                raise ValueError(f"Manual reuse report not found: {reuse_report_id}")
            if explicit.country_id != country.id or explicit.report_type != report_type:
                raise ValueError(
                    f"Report #{reuse_report_id} does not match country/type scope for current task"
                )
            explicit_sig = await self._compute_data_signature(
                db,
                country_id=country.id,
                period_start=explicit.period_start,
                period_end=explicit.period_end,
            )
            if explicit_sig != target_signature:
                raise ValueError(
                    f"Report #{reuse_report_id} does not match current analysis data signature"
                )
            if (
                self._status_str(explicit.status)
                in {ReportStatus.GENERATING.value, ReportStatus.FAILED.value}
                and not reuse_from_failed
            ):
                raise ValueError(
                    f"Report #{reuse_report_id} is {explicit.status} but reuse_from_failed is disabled"
                )
            explicit_sections = await self._get_report_section_count(db, explicit.id)
            return ReuseCandidate(
                report=explicit,
                section_count=explicit_sections,
                signature=explicit_sig,
                score=self._score_reuse_candidate(explicit, explicit_sections, reuse_strategy),
                reason=f"manual-selected id={reuse_report_id}",
            ), candidates

        if not candidates:
            return None, []

        if reuse_strategy == "safe":
            safe_candidate = next(
                (c for c in candidates if self._status_str(c.report.status) == ReportStatus.APPROVED.value),
                None,
            )
            return safe_candidate, candidates

        if reuse_strategy == "resume":
            resume_candidate = next(
                (
                    c
                    for c in candidates
                    if self._status_str(c.report.status)
                    in {ReportStatus.GENERATING.value, ReportStatus.FAILED.value}
                ),
                None,
            )
            return resume_candidate or candidates[0], candidates

        # auto: highest score across approved/generating/failed after filters.
        return candidates[0], candidates

    async def _collect_reuse_candidates(
        self,
        db,
        *,
        country: Country,
        report_type: ReportType,
        statuses: list[ReportStatus],
        target_signature: DataSignature,
        strategy: str,
    ) -> list[ReuseCandidate]:
        if not statuses:
            return []

        rows = (
            await db.execute(
                select(Report, func.count(ReportSection.id).label("section_count"))
                .outerjoin(ReportSection, ReportSection.report_id == Report.id)
                .where(
                    Report.country_id == country.id,
                    Report.report_type == report_type,
                    Report.status.in_(statuses),
                )
                .group_by(Report.id)
                .order_by(Report.updated_at.desc(), Report.created_at.desc())
                .limit(40)
            )
        ).all()

        candidates: list[ReuseCandidate] = []
        for row in rows:
            report = row[0]
            section_count = int(row[1] or 0)
            sig = await self._compute_data_signature(
                db,
                country_id=country.id,
                period_start=report.period_start,
                period_end=report.period_end,
            )
            if sig != target_signature:
                continue
            if (
                self._status_str(report.status) == ReportStatus.FAILED.value
                and self._is_hard_failure(report.error_message)
            ):
                continue
            candidates.append(
                ReuseCandidate(
                    report=report,
                    section_count=section_count,
                    signature=sig,
                    score=self._score_reuse_candidate(report, section_count, strategy),
                    reason="data-signature-match",
                )
            )
        return candidates

    @staticmethod
    def _is_hard_failure(error_message: Optional[str]) -> bool:
        text = (error_message or "").lower()
        hard_markers = [
            "no data",
            "column",
            "schema",
            "validation",
            "integrity",
            "syntax",
            "json decode",
        ]
        return any(marker in text for marker in hard_markers)

    @staticmethod
    def _status_str(status_value: object) -> str:
        if isinstance(status_value, ReportStatus):
            return status_value.value
        if isinstance(status_value, str):
            text = status_value.strip().lower()
            if text.startswith("reportstatus."):
                return text.split(".", 1)[1]
            return text
        return str(status_value).strip().lower()

    def _score_reuse_candidate(self, report: Report, section_count: int, strategy: str) -> float:
        status_value = self._status_str(report.status)
        if strategy == "resume":
            status_weight = {
                "generating": 130,
                "failed": 110,
                "approved": 90,
            }
        else:
            status_weight = {
                "approved": 140,
                "generating": 110,
                "failed": 70,
            }

        score = float(status_weight.get(status_value, 0))
        score += min(section_count, 100) * 0.5
        if isinstance(report.quality_score, (int, float)):
            score += float(report.quality_score) * 25

        updated_at = report.updated_at or report.created_at
        if isinstance(updated_at, datetime):
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600.0)
            score += max(0.0, 48.0 - age_hours) / 6.0

        if status_value == "failed" and self._is_hard_failure(report.error_message):
            score -= 100.0

        return score

    @staticmethod
    def _format_candidate_summary(candidate: ReuseCandidate) -> str:
        report = candidate.report
        updated_at = report.updated_at or report.created_at
        ts = updated_at.isoformat() if isinstance(updated_at, datetime) else "unknown"
        sig = candidate.signature
        return (
            f"#{report.id} status={report.status} score={candidate.score:.2f} "
            f"sections={candidate.section_count} quality={report.quality_score} updated={ts} "
            f"records={sig[0]} diseases={sig[1]} fp={sig[4]} reason={candidate.reason}"
        )

    async def _get_report_section_count(self, db, report_id: int) -> int:
        q = select(func.count()).select_from(ReportSection).where(ReportSection.report_id == report_id)
        return int((await db.execute(q)).scalar() or 0)

    async def _find_reusable_report(
        self,
        db,
        country: Country,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
    ) -> Optional[Report]:
        try:
            return await self._find_report_with_period_fallback(
                db,
                country=country,
                report_type=report_type,
                period_start=period_start,
                period_end=period_end,
                statuses=[ReportStatus.APPROVED],
            )
        except Exception as e:
            logger.warning(f"Failed to check for reusable report: {e}")
            return None

    async def _find_resumable_report(
        self,
        db,
        country: Country,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
    ) -> Optional[Report]:
        try:
            return await self._find_report_with_period_fallback(
                db,
                country=country,
                report_type=report_type,
                period_start=period_start,
                period_end=period_end,
                statuses=[
                    ReportStatus.GENERATING,
                    ReportStatus.FAILED,
                ],
            )
        except Exception as e:
            logger.warning(f"Failed to check for resumable report: {e}")
            return None

    @staticmethod
    def _utc_day_bounds(dt: datetime) -> tuple[datetime, datetime]:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)
        start = dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start, end

    async def _find_report_with_period_fallback(
        self,
        db,
        *,
        country: Country,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
        statuses: list[ReportStatus],
    ) -> Optional[Report]:
        # First, prefer exact datetime match to avoid reusing near-miss ranges.
        exact_q = (
            select(Report)
            .where(
                Report.country_id == country.id,
                Report.period_start == period_start,
                Report.period_end == period_end,
                Report.report_type == report_type,
                Report.status.in_(statuses),
            )
            .order_by(Report.updated_at.desc(), Report.created_at.desc())
            .limit(1)
        )
        exact = (await db.execute(exact_q)).scalar_one_or_none()
        if exact:
            return exact

        # Data-centric match: compare actual analysis data signatures instead of
        # relying on operation timestamp-derived period boundaries.
        current_sig = await self._compute_data_signature(
            db,
            country_id=country.id,
            period_start=period_start,
            period_end=period_end,
        )
        if current_sig[0] > 0:
            sig_match = await self._find_report_by_data_signature(
                db,
                country=country,
                report_type=report_type,
                statuses=statuses,
                target_signature=current_sig,
            )
            if sig_match:
                logger.info(
                    "Matched report by data signature: "
                    f"report_id={sig_match.id} country={country.code} "
                    f"type={report_type} signature={current_sig}"
                )
                return sig_match

        # Fallback: match by UTC calendar day to tolerate second-level drift.
        start_day_start, start_day_end = self._utc_day_bounds(period_start)
        end_day_start, end_day_end = self._utc_day_bounds(period_end)

        fallback_q = (
            select(Report)
            .where(
                Report.country_id == country.id,
                Report.report_type == report_type,
                Report.status.in_(statuses),
                Report.period_start >= start_day_start,
                Report.period_start < start_day_end,
                Report.period_end >= end_day_start,
                Report.period_end < end_day_end,
            )
            .order_by(Report.updated_at.desc(), Report.created_at.desc())
            .limit(1)
        )
        matched = (await db.execute(fallback_q)).scalar_one_or_none()
        if matched:
            logger.info(
                "Matched report by date fallback: "
                f"report_id={matched.id} country={country.code} "
                f"type={report_type} statuses={[str(s) for s in statuses]}"
            )
        return matched

    async def _compute_data_signature(
        self,
        db,
        *,
        country_id: int,
        period_start: datetime,
        period_end: datetime,
    ) -> DataSignature:
        q = select(
            func.count(),
            func.count(func.distinct(DiseaseRecord.disease_id)),
            func.min(DiseaseRecord.time),
            func.max(DiseaseRecord.time),
        ).where(
            DiseaseRecord.country_id == country_id,
            DiseaseRecord.time >= period_start,
            DiseaseRecord.time <= period_end,
        )
        count_all, disease_count, min_time, max_time = (await db.execute(q)).one()

        disease_ids = (
            await db.execute(
                select(DiseaseRecord.disease_id)
                .where(
                    DiseaseRecord.country_id == country_id,
                    DiseaseRecord.time >= period_start,
                    DiseaseRecord.time <= period_end,
                )
                .distinct()
                .order_by(DiseaseRecord.disease_id.asc())
            )
        ).scalars().all()
        disease_fp = hashlib.sha1(
            ",".join(str(int(did)) for did in disease_ids).encode("utf-8")
        ).hexdigest()[:12]

        return int(count_all or 0), int(disease_count or 0), min_time, max_time, disease_fp

    async def _find_report_by_data_signature(
        self,
        db,
        *,
        country: Country,
        report_type: ReportType,
        statuses: list[ReportStatus],
        target_signature: DataSignature,
    ) -> Optional[Report]:
        candidates_q = (
            select(Report)
            .where(
                Report.country_id == country.id,
                Report.report_type == report_type,
                Report.status.in_(statuses),
            )
            .order_by(Report.updated_at.desc(), Report.created_at.desc())
            .limit(30)
        )
        candidates = (await db.execute(candidates_q)).scalars().all()
        for candidate in candidates:
            candidate_sig = await self._compute_data_signature(
                db,
                country_id=country.id,
                period_start=candidate.period_start,
                period_end=candidate.period_end,
            )
            if candidate_sig == target_signature:
                return candidate
        return None

    async def _resolve_task_attached_report(
        self,
        db,
        task: Task,
        *,
        country_obj: Country,
        report_type: ReportType,
    ) -> Optional[Report]:
        report_id = task.report_id
        output_data = task.output_data if isinstance(task.output_data, dict) else {}
        if report_id is None and isinstance(output_data.get("report_id"), int):
            report_id = int(output_data["report_id"])

        if report_id is None:
            return None

        report = await db.get(Report, report_id)
        if report is None:
            return None
        if report.country_id != country_obj.id or report.report_type != report_type:
            return None
        if self._status_str(report.status) not in {
            ReportStatus.GENERATING.value,
            ReportStatus.FAILED.value,
        }:
            return None
        return report

    async def _adjust_period_to_available_data(
        self,
        db,
        task: Task,
        country: Country,
        period_start: datetime,
        period_end: datetime,
        days: int,
    ) -> tuple[datetime, datetime]:
        """If no data exists in the requested range, shift to the latest available."""
        count_q = select(func.count()).select_from(DiseaseRecord).where(
            DiseaseRecord.country_id == country.id,
            DiseaseRecord.time >= period_start,
            DiseaseRecord.time <= period_end,
        )
        data_count = (await db.execute(count_q)).scalar()

        if data_count and data_count > 0:
            return period_start, period_end

        latest_q = select(func.max(DiseaseRecord.time)).where(
            DiseaseRecord.country_id == country.id
        )
        latest_date = (await db.execute(latest_q)).scalar()

        if not latest_date:
            raise ValueError(
                f"No disease data found for {country.name}. Please run crawl first."
            )

        if latest_date.tzinfo is None:
            latest_date = latest_date.replace(tzinfo=timezone.utc)
        adjusted_end = latest_date
        adjusted_start = adjusted_end - timedelta(days=days)

        msg = (
            "No data in requested period. Using latest available data: "
            f"{adjusted_start.date()} to {adjusted_end.date()}"
        )
        logger.warning(msg)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="warning",
            title="Time Range Adjusted",
            content=msg,
            content_type="text",
        )
        return adjusted_start, adjusted_end

    @staticmethod
    def _collect_output_files(report: Report) -> List[str]:
        files = []
        if getattr(report, "markdown_path", None):
            files.append(f"Markdown: {report.markdown_path}")
        if getattr(report, "html_path", None):
            files.append(f"HTML: {report.html_path}")
        if getattr(report, "pdf_path", None):
            files.append(f"PDF: {report.pdf_path}")
        return files

    async def _ensure_task_not_cancelled(self, task_uuid: str) -> None:
        if await task_manager.is_cancel_requested(task_uuid):
            raise TaskCancelledError("Cancellation requested by user")
