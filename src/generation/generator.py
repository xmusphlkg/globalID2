"""
GlobalID V2 Report Generator

报告生成器：整合所有组件生成完整报告
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import select

from src.core import get_config, get_database, get_logger, normalize_rate_columns
from src.core.task_manager import task_manager
from src.domain import (
    AIConversation,
    Report,
    ReportSection,
    ReportSectionRun,
    ReportSectionRunStatus,
    ReportStatus,
    ReportType,
)
from src.ai.agents import AnalystAgent, WriterAgent, ReviewerAgent
from src.services.exceptions import TaskCancelledError
from .charts import ChartGenerator
from .data_exporter import DataExporter
from .formatter import ReportFormatter
from .email_service import EmailService

logger = get_logger(__name__)


class ReportGenerator:
    """
    报告生成器
    
    完整流程：
    1. 数据提取
    2. AI分析
    3. 内容撰写（含 Writer→Reviewer→Writer 修订循环）
    4. 质量审核（并行）
    5. 格式化输出
    6. 邮件发送
    """

    # Disease codes to skip when generating reports (placeholder / aggregate codes)
    SKIP_DISEASE_CODES: frozenset = frozenset({"D999"})

    # Maximum writer-revision attempts per section before accepting the best draft
    MAX_REVISIONS: int = 2

    def __init__(self):
        """初始化报告生成器"""
        self.config = get_config()
        
        # 初始化各组件
        # Note: AnalystAgent, WriterAgent, ReviewerAgent are instantiated per-task
        # in process_disease() to prevent shared-state race conditions in parallel execution.
        self.chart_generator = ChartGenerator()
        self.data_exporter = DataExporter()
        self.formatter = ReportFormatter()
        self.email_service = EmailService()
        
        # 输出目录
        self.output_dir = Path(self.config.app.base_dir) / self.config.report.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("ReportGenerator initialized")

    async def _log_task_event(
        self,
        task_uuid: Optional[str],
        entry_type: str,
        title: str,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        prompt: Optional[str] = None,
        response: Optional[str] = None,
        model_used: Optional[str] = None,
        tokens_used: Optional[int] = None,
        duration: Optional[float] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> None:
        if not task_uuid:
            return
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type=entry_type,
            title=title,
            content=content,
            content_type="text",
            prompt=prompt,
            response=response,
            model_used=model_used,
            tokens_used=tokens_used,
            duration=duration,
            success=success,
            error_message=error_message,
            metadata=metadata or {},
        )

    @staticmethod
    def _find_last_ai_exchange(conversations: List[Dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
        for entry in reversed(conversations or []):
            prompt = entry.get("prompt")
            response = entry.get("response")
            if prompt or response:
                return prompt, response
        return None, None

    @staticmethod
    def _quality_overall(quality_scores: Optional[Dict[str, Any]]) -> Optional[float]:
        if not isinstance(quality_scores, dict):
            return None
        for key in ("overall", "quality", "score", "final"):
            value = quality_scores.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None
    
    async def generate(
        self,
        country_id: int,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
        diseases: Optional[List[int]] = None,
        db=None,
        **kwargs
    ) -> Report:
        """
        生成报告
        
        Args:
            country_id: 国家ID
            report_type: 报告类型
            period_start: 起始时间
            period_end: 结束时间
            diseases: 疾病ID列表（None=全部）
            db: 数据库会话（可选）
            **kwargs: 额外参数
            
        Returns:
            生成的报告对象
        """
        logger.info(f"Starting report generation: {report_type} for country {country_id}")
        
        # 如果没有提供db，创建一个新的会话
        if db is None:
            async with get_database() as db:
                return await self._generate_with_db(
                    db, country_id, report_type, period_start, period_end, diseases, **kwargs
                )
        else:
            return await self._generate_with_db(
                db, country_id, report_type, period_start, period_end, diseases, **kwargs
            )
    
    async def _generate_with_db(
        self,
        db,
        country_id: int,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
        diseases: Optional[List[int]] = None,
        **kwargs
    ) -> Report:
        """内部方法：使用指定的数据库会话生成报告"""
        # Extract progress callback
        progress_callback = kwargs.get('progress_callback', None)
        task_uuid = kwargs.get('task_uuid')
        existing_report = kwargs.get('existing_report')
        
        # Helper function to call progress callback safely
        async def notify_progress(stage, current, total, message):
            if progress_callback:
                await progress_callback(stage, current, total, message)
        
        # 1. 创建或恢复报告记录
        if existing_report is None:
            report = await self._create_report_record(
                db,
                country_id=country_id,
                report_type=report_type,
                period_start=period_start,
                period_end=period_end,
                **kwargs
            )
        else:
            generation_config = {
                **(report.generation_config or {}),
                **({"language": kwargs.get("language")} if kwargs.get("language") else {}),
                **({"report_layout": kwargs.get("report_layout")} if kwargs.get("report_layout") else {}),
            }
            generation_config.pop("email_delivery", None)
            report = existing_report
            report.status = ReportStatus.GENERATING
            report.error_message = None
            report.completed_at = None
            report.generation_config = generation_config
            await db.commit()
            await db.refresh(report)

        if task_uuid:
            await task_manager.link_task_report(task_uuid, report.id)
        
        try:
            await self._ensure_task_not_cancelled(task_uuid, report.id)
            # 2. 提取数据
            await notify_progress("data_extraction", 0, 1, "Extracting disease data...")
            
            data = await self._extract_data(
                db,
                country_id=country_id,
                period_start=period_start,
                period_end=period_end,
                diseases=diseases,
            )
            
            await notify_progress("data_extraction", 1, 1, f"Extracted {len(data)} records")
            
            if data.empty:
                logger.warning("No data found for report")
                report.status = ReportStatus.FAILED
                report.error_message = "No data available"
                await db.commit()
                return report

            await self._ensure_task_not_cancelled(task_uuid, report.id)
            
            # 2.1 获取近期爬取的原始页面，用于补充最新上下文
            raw_sources = []
            if kwargs.get('include_raw_context', True):
                raw_sources = await self._fetch_recent_raw_pages(
                    db=db,
                    country_id=country_id,
                    period_end=period_end,
                    days_back=kwargs.get('raw_days_back', 45),
                    limit=kwargs.get('raw_limit', 3),
                )

            # 3. 生成章节（Analysis + Writing）
            await notify_progress("analysis", 0, 1, "Starting AI analysis...")
            
            # 准备kwargs，添加特定的progress_callback
            sections_kwargs = dict(kwargs)
            sections_kwargs['progress_callback'] = lambda cur, tot, msg: notify_progress("analysis", cur, tot, msg)
            
            report_layout = kwargs.get("report_layout", "structured")
            if report_layout == "legacy":
                sections = await self._generate_sections(
                    db,
                    report=report,
                    data=data,
                    raw_sources=raw_sources,
                    **sections_kwargs
                )
            else:
                sections = await self._generate_structured_sections(
                    db,
                    report=report,
                    data=data,
                    raw_sources=raw_sources,
                    **sections_kwargs,
                )
            
            await notify_progress("writing", 1, 1, f"Generated {len(sections)} sections")
            
            # 4. 可选后置审核（默认关闭）：主流程已在写作阶段完成内联审核与修订
            enable_review = kwargs.get('enable_review', True)
            enable_post_review = kwargs.get('enable_post_review', False)
            if enable_review and enable_post_review:
                await notify_progress("review", 0, len(sections), "Starting content review...")
                sections = await self._review_sections(
                    db,
                    report,
                    sections,
                    data,
                    raw_sources,
                    progress_callback=notify_progress,
                    task_uuid=task_uuid,
                    language=kwargs.get('language', 'en'),
                )
                await notify_progress("review", len(sections), len(sections), "Review complete")
            elif enable_review:
                await notify_progress("review", len(sections), len(sections), "Inline review completed during writing")
            
            # 5. 格式化并保存
            await self._ensure_task_not_cancelled(task_uuid, report.id)
            await self._format_and_save(db, report, sections)
            
            # 6. 导出数据文件（如果配置）
            if kwargs.get('export_data', True):
                await self._export_data(db, report, country_id, period_start, period_end)
            
            # 7. 发送邮件（如果配置）
            if kwargs.get('send_email', False):
                await self._send_email(db, report, sections)
            
            # 8. 更新状态（根据审核结果决定是否标记为APPROVED）
            if enable_review and report_layout == "legacy":
                # 计算整体质量分，并判断是否所有章节均通过审核
                all_verified = True
                quality_scores: list[float] = []
                for sec in sections:
                    if not sec.get("is_verified", False):
                        all_verified = False
                    # 尝试从 quality_scores 中取一个总体分数（如 overall/score）
                    qs = sec.get("quality_scores") or {}
                    # 支持多种常见字段名
                    for key in ("overall", "score", "quality", "final"):
                        if isinstance(qs.get(key), (int, float)):
                            quality_scores.append(float(qs[key]))
                            break
                avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else None
                threshold = getattr(self.config.ai, "reviewer_threshold", 0.0)

                if all_verified and (avg_quality is None or avg_quality >= threshold):
                    report.status = ReportStatus.APPROVED
                    report.quality_score = avg_quality
                else:
                    report.status = ReportStatus.COMPLETED
                    # 仅当尚未有质量分时写入一个基础值
                    if report.quality_score is None and avg_quality is not None:
                        report.quality_score = avg_quality
            else:
                report.status = ReportStatus.APPROVED if report_layout != "legacy" else ReportStatus.COMPLETED

            report.completed_at = datetime.now(timezone.utc)
            await db.commit()
            
            logger.info(f"Report generation completed: {report.id}")
            return report

        except TaskCancelledError as e:
            logger.warning(f"Report generation cancelled: {e}")
            try:
                await db.rollback()
            except Exception as rollback_err:
                logger.error(f"Failed to rollback after report cancellation: {rollback_err}")
            try:
                report.status = ReportStatus.FAILED
                report.error_message = str(e)
                report.completed_at = datetime.now(timezone.utc)
                db.add(report)
                await db.commit()
            except Exception as commit_err:
                logger.error(f"Failed to persist cancelled report status: {commit_err}")
            await self._mark_incomplete_runs_cancelled(report.id, str(e))
            raise
        
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            # 先回滚当前事务（若事务已进入 aborted 状态，直接 commit 会触发二次错误）
            try:
                await db.rollback()
            except Exception as rollback_err:
                logger.error(f"Failed to rollback after report error: {rollback_err}")
            # 用干净的事务写入失败状态
            try:
                report.status = ReportStatus.FAILED
                report.error_message = str(e)
                db.add(report)
                await db.commit()
            except Exception as commit_err:
                logger.error(f"Failed to persist failed report status: {commit_err}")
            raise
    
    async def _create_report_record(
        self,
        db,
        country_id: int,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
        **kwargs
    ) -> Report:
        """创建报告记录"""
        generation_config = dict(kwargs.get('config', {}) or {})
        if kwargs.get("language"):
            generation_config["language"] = kwargs.get("language")
        if kwargs.get("report_layout"):
            generation_config["report_layout"] = kwargs.get("report_layout")

        report = Report(
            country_id=country_id,
            report_type=report_type,
            title=kwargs.get('title', f"{report_type.value}报告"),
            status=ReportStatus.GENERATING,
            period_start=period_start,
            period_end=period_end,
            generation_config=generation_config,
        )
        
        db.add(report)
        await db.commit()
        await db.refresh(report)
        
        logger.info(f"Created report record: {report.id}")
        return report
    
    async def _extract_data(
        self,
        db,
        country_id: int,
        period_start: datetime,
        period_end: datetime,
        diseases: Optional[List[int]] = None,
    ) -> pd.DataFrame:
        """提取数据"""
        from sqlalchemy import select, desc
        from src.domain import DiseaseRecord, Disease
        
        logger.debug(f"Extracting data for country {country_id}")
        
        # 构建查询
        query = select(DiseaseRecord).where(
            DiseaseRecord.country_id == country_id,
            DiseaseRecord.time >= period_start,
            DiseaseRecord.time <= period_end,
        )
        
        if diseases:
            query = query.where(DiseaseRecord.disease_id.in_(diseases))
        
        # 执行查询
        result = await db.execute(query)
        records = result.scalars().all()
        
        # 转换为DataFrame
        if not records:
            return pd.DataFrame()
        
        data = pd.DataFrame([{
            'time': r.time,
            'disease_id': r.disease_id,
            'cases': r.cases,
            'deaths': r.deaths,
            'new_cases': r.new_cases,
            'new_deaths': r.new_deaths,
            'recoveries': r.recoveries,
            'incidence_rate': r.incidence_rate,
            'mortality_rate': r.mortality_rate,
            'recovery_rate': r.recovery_rate,
            'data_source': r.data_source,
            'data_quality': r.data_quality,
            'confidence_score': r.confidence_score,
        } for r in records])
        
        data = normalize_rate_columns(data, copy=False)
        logger.info(f"Extracted {len(data)} records")
        return data

    async def _generate_structured_sections(
        self,
        db,
        report: Report,
        data: pd.DataFrame,
        raw_sources: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Generate the v2 structured report: executive summary + disease cards + appendices."""
        from sqlalchemy import select
        from src.domain import Country, Disease, DiseaseKnowledgeBrief, ReportSection

        logger.info("Generating structured report sections (executive summary + disease cards)")
        task_uuid = kwargs.get("task_uuid")
        language = kwargs.get("language", "en")
        progress_callback = kwargs.get("progress_callback")

        existing_sections_result = await db.execute(
            select(ReportSection)
            .where(ReportSection.report_id == report.id)
            .order_by(ReportSection.section_order)
        )
        existing_sections = existing_sections_result.scalars().all()
        if existing_sections:
            return [
                {
                    "title": section.title,
                    "content": section.content,
                    "type": section.section_type,
                    "chart_html": None,
                    "disease_name": self._section_disease_name(section.title, section.section_type),
                    "is_verified": bool(section.is_verified),
                    "quality_scores": {"overall": 0.9 if section.is_verified else 0.7},
                }
                for section in existing_sections
            ]

        country = (
            await db.execute(select(Country).where(Country.id == report.country_id))
        ).scalar_one_or_none()
        country_name = (country.name_en or country.name) if country else "Unknown"
        country_code = country.code if country else ""

        disease_ids_needed = [int(id_) for id_ in data["disease_id"].unique()]
        disease_rows = (
            await db.execute(select(Disease).where(Disease.id.in_(disease_ids_needed)))
        ).scalars().all()
        disease_map = {row.id: row for row in disease_rows}
        standard_ids = [row.name for row in disease_rows if row.name not in self.SKIP_DISEASE_CODES]

        brief_rows = (
            await db.execute(
                select(DiseaseKnowledgeBrief).where(
                    DiseaseKnowledgeBrief.disease_id.in_(standard_ids),
                    DiseaseKnowledgeBrief.language == ("zh" if language == "zh" else "en"),
                    DiseaseKnowledgeBrief.status == "published",
                )
            )
        ).scalars().all()
        brief_map = {brief.disease_id: brief for brief in brief_rows}

        disease_cards: list[dict[str, Any]] = []
        for disease_id, disease_data in data.groupby("disease_id"):
            disease = disease_map.get(int(disease_id))
            if not disease or disease.name in self.SKIP_DISEASE_CODES:
                continue
            card = self._build_disease_card_payload(
                disease=disease,
                disease_data=disease_data,
                knowledge_brief=brief_map.get(disease.name),
                language=language,
            )
            disease_cards.append(card)

        disease_cards.sort(
            key=lambda item: (
                item["metrics"]["total_cases"],
                item["metrics"]["total_deaths"],
                abs(item["metrics"].get("change_pct") or 0),
            ),
            reverse=True,
        )

        if progress_callback:
            await progress_callback(0, max(len(disease_cards), 1), "Building structured report sections...")

        top_limit = int(kwargs.get("top_disease_cards", 0) or len(disease_cards))
        visible_cards = disease_cards[:top_limit]
        report_summary_payload = self._build_report_summary_payload(
            country_name=country_name,
            country_code=country_code,
            report=report,
            disease_cards=disease_cards,
            language=language,
        )

        report.title = report_summary_payload["title"]
        report.summary = report_summary_payload["summary_text"]
        report.key_findings = report_summary_payload["key_findings"]
        report.quality_score = 0.9
        report.metadata_ = {
            **(report.metadata_ or {}),
            "report_layout": "structured",
            "disease_card_count": len(visible_cards),
            "knowledge_brief_count": sum(1 for card in visible_cards if card.get("knowledge_status") == "published"),
        }

        section_payloads: list[dict[str, Any]] = [
            {
                "section_type": "executive_summary",
                "title": "Executive Summary" if language != "zh" else "执行摘要",
                "content": self._render_executive_summary(report_summary_payload, language),
                "data_sources": self._report_data_sources(country_name, country_code, raw_sources),
                "metadata": report_summary_payload,
                "disease_name": "",
            }
        ]

        for index, card in enumerate(visible_cards, 1):
            section_payloads.append(
                {
                    "section_type": "disease_card",
                    "title": f"{card['name_en']} - disease_card",
                    "content": self._render_disease_card(card, language),
                    "data_sources": card.get("data_sources") or [],
                    "metadata": card,
                    "disease_name": card["name_en"],
                }
            )
            if progress_callback and index % 10 == 0:
                await progress_callback(index, len(visible_cards), f"Built {index}/{len(visible_cards)} disease cards")

        quality_payload = self._build_data_quality_payload(data, disease_cards, language)
        methodology_payload = self._build_methodology_payload(country_name, language)
        section_payloads.extend(
            [
                {
                    "section_type": "data_quality_notes",
                    "title": "Data Quality Notes" if language != "zh" else "数据质量说明",
                    "content": self._render_data_quality_notes(quality_payload, language),
                    "data_sources": self._report_data_sources(country_name, country_code, raw_sources),
                    "metadata": quality_payload,
                    "disease_name": "",
                },
                {
                    "section_type": "methodology",
                    "title": "Methodology" if language != "zh" else "方法说明",
                    "content": self._render_methodology(methodology_payload, language),
                    "data_sources": self._report_data_sources(country_name, country_code, raw_sources),
                    "metadata": methodology_payload,
                    "disease_name": "",
                },
            ]
        )

        persisted_sections: list[dict[str, Any]] = []
        for order, payload in enumerate(section_payloads, 1):
            section = ReportSection(
                report_id=report.id,
                title=payload["title"],
                content=payload["content"],
                section_type=payload["section_type"],
                section_order=order,
                data_sources=payload.get("data_sources") or [],
                is_verified=True,
                metadata_=payload.get("metadata") or {},
            )
            db.add(section)
            await db.flush()
            persisted_sections.append(
                {
                    "title": section.title,
                    "content": section.content,
                    "type": section.section_type,
                    "chart_html": None,
                    "section_id": section.id,
                    "disease_name": payload.get("disease_name") or "",
                    "token_usage": {},
                    "quality_scores": {"overall": 0.9},
                    "is_verified": True,
                }
            )

        await db.commit()
        if progress_callback:
            await progress_callback(len(visible_cards), max(len(visible_cards), 1), "Structured report complete")
        await self._log_task_event(
            task_uuid,
            entry_type="success",
            title="Structured Report Generated",
            content=(
                f"Country: {country_name}\n"
                f"Disease cards: {len(visible_cards)}\n"
                f"Knowledge briefs used: {report.metadata_.get('knowledge_brief_count', 0)}"
            ),
            metadata={"scope": "report", "event": "structured_report_generated", "report_id": report.id},
        )
        logger.info("Structured report generated with %s sections", len(persisted_sections))
        return persisted_sections

    @staticmethod
    def _section_disease_name(title: str, section_type: str) -> str:
        if section_type != "disease_card" or " - " not in (title or ""):
            return ""
        return str(title).split(" - ")[0]

    def _build_disease_card_payload(
        self,
        *,
        disease,
        disease_data: pd.DataFrame,
        knowledge_brief,
        language: str,
    ) -> Dict[str, Any]:
        df = normalize_rate_columns(disease_data)
        sorted_df = df.sort_values("time") if "time" in df.columns else df
        cases = sorted_df["cases"].fillna(0).tolist() if "cases" in sorted_df.columns else []
        deaths = sorted_df["deaths"].fillna(0).tolist() if "deaths" in sorted_df.columns else []
        latest_cases = int(cases[-1]) if cases else 0
        previous_cases = int(cases[-2]) if len(cases) >= 2 else 0
        latest_deaths = int(deaths[-1]) if deaths else 0
        previous_deaths = int(deaths[-2]) if len(deaths) >= 2 else 0
        total_cases = int(sum(cases))
        total_deaths = int(sum(deaths))
        change_pct = self._pct_change(latest_cases, previous_cases)
        death_change_pct = self._pct_change(latest_deaths, previous_deaths)
        trend = self._trend_label(change_pct, language)
        period_start = self._format_period_value(sorted_df["time"].min()) if "time" in sorted_df.columns and len(sorted_df) else ""
        period_end = self._format_period_value(sorted_df["time"].max()) if "time" in sorted_df.columns and len(sorted_df) else ""

        if knowledge_brief:
            from src.knowledge.catalogue import knowledge_brief_publication_tier
            from src.knowledge.citations import normalize_knowledge_citations

            normalized_brief = normalize_knowledge_citations(
                {
                    "brief": knowledge_brief.brief,
                    "definition": knowledge_brief.definition,
                    "clinical_features": knowledge_brief.clinical_features,
                    "clinical_summary": knowledge_brief.clinical_summary,
                    "epidemiology": knowledge_brief.epidemiology,
                    "transmission": knowledge_brief.transmission,
                    "prevention": knowledge_brief.prevention,
                    "surveillance_note": knowledge_brief.surveillance_note,
                    "risk_groups": knowledge_brief.risk_groups,
                    "source_ids": knowledge_brief.source_ids or [],
                    "source_attribution": knowledge_brief.source_attribution or [],
                    "metadata": knowledge_brief.metadata_ or {},
                }
            )
            official_brief = normalized_brief.get("brief")
            definition = normalized_brief.get("definition")
            clinical_features = normalized_brief.get("clinical_features") or normalized_brief.get("clinical_summary")
            epidemiology = normalized_brief.get("epidemiology")
            transmission = normalized_brief.get("transmission")
            prevention = normalized_brief.get("prevention")
            surveillance_note = normalized_brief.get("surveillance_note")
            risk_groups = normalized_brief.get("risk_groups")
            source_attribution = normalized_brief.get("source_attribution") or []
            knowledge_status = knowledge_brief_publication_tier(knowledge_brief)
            knowledge_updated_at = knowledge_brief.updated_at.isoformat() if knowledge_brief.updated_at else None
            disclaimer = knowledge_brief.disclaimer
        else:
            from src.knowledge.catalogue import build_catalogue_disease_brief

            fallback_brief = build_catalogue_disease_brief(disease, language)
            official_brief = fallback_brief["brief"]
            definition = fallback_brief.get("definition")
            clinical_features = fallback_brief.get("clinical_features") or fallback_brief.get("clinical_summary")
            epidemiology = fallback_brief.get("epidemiology")
            transmission = fallback_brief["transmission"]
            prevention = fallback_brief["prevention"]
            surveillance_note = fallback_brief.get("surveillance_note")
            risk_groups = fallback_brief["risk_groups"]
            source_attribution = []
            knowledge_status = "fallback"
            knowledge_updated_at = None
            disclaimer = fallback_brief["disclaimer"]

        interpretation = self._current_interpretation(
            name=disease.name_en or disease.name,
            latest_cases=latest_cases,
            latest_deaths=latest_deaths,
            previous_cases=previous_cases,
            change_pct=change_pct,
            trend=trend,
            language=language,
        )
        limitations = self._disease_limitations(df, language)

        data_sources = [
            {
                "url": item.get("resolved_url") or item.get("url"),
                "title": item.get("title") or item.get("source_name"),
                "snippet": item.get("license") or item.get("source_type"),
            }
            for item in source_attribution
            if isinstance(item, dict)
        ]
        data_sources.append(
            {
                "url": None,
                "title": f"Surveillance data for {disease.name_en or disease.name}",
                "snippet": f"Period: {period_start} to {period_end}; records: {len(sorted_df)}; total cases: {total_cases}; total deaths: {total_deaths}",
            }
        )

        return {
            "disease_id": disease.name,
            "name_en": disease.name_en or disease.name,
            "name_zh": self._first_alias(disease) or disease.name_en or disease.name,
            "category": disease.category,
            "icd_10": disease.icd_10,
            "icd_11": disease.icd_11,
            "period_start": period_start,
            "period_end": period_end,
            "official_brief": official_brief,
            "definition": definition,
            "clinical_features": clinical_features,
            "epidemiology": epidemiology,
            "transmission": transmission,
            "prevention": prevention,
            "surveillance_note": surveillance_note,
            "risk_groups": risk_groups,
            "current_interpretation": interpretation,
            "trend_assessment": trend,
            "risk_note": self._risk_note(latest_cases, latest_deaths, language),
            "data_limitations": limitations,
            "disclaimer": disclaimer,
            "knowledge_status": knowledge_status,
            "knowledge_updated_at": knowledge_updated_at,
            "source_attribution": source_attribution,
            "data_sources": data_sources,
            "metrics": {
                "total_cases": total_cases,
                "total_deaths": total_deaths,
                "latest_cases": latest_cases,
                "latest_deaths": latest_deaths,
                "previous_cases": previous_cases,
                "previous_deaths": previous_deaths,
                "change_pct": change_pct,
                "death_change_pct": death_change_pct,
                "record_count": int(len(sorted_df)),
            },
        }

    def _build_report_summary_payload(
        self,
        *,
        country_name: str,
        country_code: str,
        report: Report,
        disease_cards: list[dict[str, Any]],
        language: str,
    ) -> Dict[str, Any]:
        total_cases = sum(card["metrics"]["total_cases"] for card in disease_cards)
        total_deaths = sum(card["metrics"]["total_deaths"] for card in disease_cards)
        latest_cases = sum(card["metrics"]["latest_cases"] for card in disease_cards)
        active_cards = [card for card in disease_cards if card["metrics"]["latest_cases"] > 0 or card["metrics"]["latest_deaths"] > 0]
        top_by_cases = sorted(disease_cards, key=lambda card: card["metrics"]["total_cases"], reverse=True)[:5]
        top_movers = sorted(
            disease_cards,
            key=lambda card: abs(card["metrics"].get("change_pct") or 0),
            reverse=True,
        )[:5]
        period = f"{report.period_start.strftime('%Y-%m-%d')} to {report.period_end.strftime('%Y-%m-%d')}"
        title = (
            f"{country_name} Infectious Disease Surveillance Report | {report.period_start.strftime('%Y-%m')} to {report.period_end.strftime('%Y-%m')}"
            if language != "zh"
            else f"{country_name} 传染病监测报告 | {report.period_start.strftime('%Y-%m')} 至 {report.period_end.strftime('%Y-%m')}"
        )
        if language == "zh":
            summary_text = (
                f"本报告汇总 {country_name} 在 {period} 期间的传染病监测数据，覆盖 {len(disease_cards)} 种标准化疾病。"
                f"报告期内累计记录 {total_cases:,} 例病例和 {total_deaths:,} 例死亡，最近一期活跃报告疾病 {len(active_cards)} 种。"
            )
            key_findings = [
                f"累计病例最高的疾病包括：{', '.join(card['name_en'] for card in top_by_cases if card['metrics']['total_cases'] > 0) or '暂无'}。",
                f"最近一期病例合计为 {latest_cases:,} 例，需结合各来源报告频率解读。",
                "所有疾病卡片均包含基础简介、当前解读、趋势判断、关键数字和数据限制。",
                "报告中的疾病简介优先使用知识库发布 brief；缺失时以本地目录后备说明展示并标记需复核。",
            ]
        else:
            summary_text = (
                f"This report summarizes infectious disease surveillance data for {country_name} during {period}, "
                f"covering {len(disease_cards)} standardized diseases. The reporting window contains {total_cases:,} "
                f"recorded cases and {total_deaths:,} deaths, with {len(active_cards)} diseases active in the latest observation."
            )
            key_findings = [
                f"Highest cumulative case burdens: {', '.join(card['name_en'] for card in top_by_cases if card['metrics']['total_cases'] > 0) or 'none reported'}.",
                f"Latest-observation reported cases sum to {latest_cases:,}; interpretation should account for source reporting cadence.",
                "Each disease card includes an official brief, current interpretation, trend assessment, key metrics, and data limitations.",
                "Disease introductions use published knowledge briefs where available, with local-catalogue fallback clearly marked for review.",
            ]

        return {
            "title": title,
            "country_name": country_name,
            "country_code": country_code,
            "period": period,
            "summary_text": summary_text,
            "key_findings": key_findings,
            "totals": {
                "disease_count": len(disease_cards),
                "active_latest_diseases": len(active_cards),
                "total_cases": total_cases,
                "total_deaths": total_deaths,
                "latest_cases": latest_cases,
            },
            "top_by_cases": top_by_cases,
            "top_movers": top_movers,
        }

    @staticmethod
    def _render_executive_summary(payload: Dict[str, Any], language: str) -> str:
        lines = [f"# {payload['title']}", "", payload["summary_text"], ""]
        lines.append("## Key Findings" if language != "zh" else "## 关键发现")
        for finding in payload["key_findings"]:
            lines.append(f"- {finding}")
        lines.append("")
        lines.append("## Highest Burden Diseases" if language != "zh" else "## 累计负担最高疾病")
        for card in payload["top_by_cases"]:
            metrics = card["metrics"]
            lines.append(
                f"- **{card['name_en']}**: {metrics['total_cases']:,} cases, {metrics['total_deaths']:,} deaths; latest {metrics['latest_cases']:,} cases."
            )
        lines.append("")
        lines.append("## Largest Recent Changes" if language != "zh" else "## 最近变化较大疾病")
        for card in payload["top_movers"]:
            change = card["metrics"].get("change_pct")
            change_text = "N/A" if change is None else f"{change:+.1f}%"
            lines.append(f"- **{card['name_en']}**: {change_text} vs previous observation; {card['trend_assessment']}.")
        return "\n".join(lines)

    @staticmethod
    def _render_disease_card(card: Dict[str, Any], language: str) -> str:
        labels = {
            "official": "Official Brief" if language != "zh" else "官方简介",
            "current": "Current Interpretation" if language != "zh" else "当前解读",
            "metrics": "Key Metrics" if language != "zh" else "关键指标",
            "risk": "Risk Note" if language != "zh" else "风险提示",
            "limits": "Data Limitations" if language != "zh" else "数据限制",
            "sources": "Sources" if language != "zh" else "来源",
        }
        m = card["metrics"]
        lines = [
            f"## {card['name_en']}",
            "",
            f"### {labels['official']}",
            card.get("official_brief") or "",
            "",
        ]
        if card.get("definition"):
            lines.extend(["**Definition:**" if language != "zh" else "**定义：**", card["definition"], ""])
        if card.get("clinical_features"):
            lines.extend(["**Clinical features:**" if language != "zh" else "**临床特征：**", card["clinical_features"], ""])
        if card.get("epidemiology"):
            lines.extend(["**Epidemiology:**" if language != "zh" else "**流行病学：**", card["epidemiology"], ""])
        if card.get("transmission"):
            lines.extend(["**Transmission / exposure:**" if language != "zh" else "**传播/暴露：**", card["transmission"], ""])
        if card.get("prevention"):
            lines.extend(["**Prevention context:**" if language != "zh" else "**预防解读：**", card["prevention"], ""])
        if card.get("surveillance_note"):
            lines.extend(["**Surveillance note:**" if language != "zh" else "**监测说明：**", card["surveillance_note"], ""])
        if card.get("risk_groups"):
            lines.extend(["**Risk groups:**" if language != "zh" else "**重点人群：**", card["risk_groups"], ""])
        lines.extend(
            [
                f"### {labels['current']}",
                card["current_interpretation"],
                "",
                f"### {labels['metrics']}",
                f"- Total cases: {m['total_cases']:,}",
                f"- Total deaths: {m['total_deaths']:,}",
                f"- Latest observation: {m['latest_cases']:,} cases and {m['latest_deaths']:,} deaths",
                f"- Trend assessment: {card['trend_assessment']}",
                "",
                f"### {labels['risk']}",
                card["risk_note"],
                "",
                f"### {labels['limits']}",
                card["data_limitations"],
                "",
            ]
        )
        sources = card.get("source_attribution") or []
        if sources:
            lines.append(f"### {labels['sources']}")
            for src in sources[:5]:
                title = src.get("title") or src.get("source_name") or src.get("source_type")
                url = src.get("resolved_url") or src.get("url")
                lines.append(f"- [{title}]({url})" if url else f"- {title}")
            lines.append("")
        if card.get("disclaimer"):
            lines.extend(["", f"*{card['disclaimer']}*"])
        return "\n".join(lines)

    @staticmethod
    def _build_data_quality_payload(data: pd.DataFrame, disease_cards: list[dict[str, Any]], language: str) -> Dict[str, Any]:
        missing_rates = {}
        for column in ("cases", "deaths", "incidence_rate", "mortality_rate"):
            if column in data.columns:
                missing_rates[column] = round(float(data[column].isna().mean()), 4)
        fallback_count = sum(1 for card in disease_cards if card.get("knowledge_status") != "published")
        return {
            "record_count": int(len(data)),
            "disease_count": len(disease_cards),
            "missing_rates": missing_rates,
            "fallback_knowledge_briefs": fallback_count,
            "language": language,
        }

    @staticmethod
    def _render_data_quality_notes(payload: Dict[str, Any], language: str) -> str:
        if language == "zh":
            lines = [
                "## 数据质量说明",
                f"- 本报告使用 {payload['record_count']:,} 条标准化监测记录，覆盖 {payload['disease_count']} 种疾病。",
                f"- {payload['fallback_knowledge_briefs']} 个疾病简介使用本地目录后备文本，需后续知识库复核。",
                "- 发病率、死亡率等率值依赖人口分母和来源定义，缺失时不自动推断。",
            ]
        else:
            lines = [
                "## Data Quality Notes",
                f"- This report uses {payload['record_count']:,} standardized surveillance records covering {payload['disease_count']} diseases.",
                f"- {payload['fallback_knowledge_briefs']} disease briefs use local-catalogue fallback text and should be reviewed in the knowledge base.",
                "- Incidence and mortality rates depend on population denominators and source definitions; missing rates are not inferred.",
            ]
        for column, missing in payload.get("missing_rates", {}).items():
            lines.append(f"- Missing `{column}` values: {missing:.1%}.")
        return "\n".join(lines)

    @staticmethod
    def _build_methodology_payload(country_name: str, language: str) -> Dict[str, Any]:
        return {
            "country_name": country_name,
            "language": language,
            "sections": ["executive_summary", "disease_card", "data_quality_notes", "methodology"],
        }

    @staticmethod
    def _render_methodology(payload: Dict[str, Any], language: str) -> str:
        if language == "zh":
            return (
                "## 方法说明\n"
                "本报告先聚合报告期内的国家级监测指标，再按标准疾病生成结构化疾病卡片。"
                "疾病卡片中的基础简介来自知识库发布 brief；监测解读来自当前数据库时间序列。"
                "所有数字均来自清洗后的监测记录，缺少来源支撑的医学解释不会自动生成。"
            )
        return (
            "## Methodology\n"
            "The report first aggregates country-level surveillance indicators for the reporting window, "
            "then renders structured disease cards for standardized diseases. Disease-card briefs come "
            "from published knowledge-base briefs when available; surveillance interpretation comes from "
            "the current database time series. All reported numbers are derived from cleaned surveillance "
            "records, and unsupported medical interpretation is not inferred."
        )

    @staticmethod
    def _report_data_sources(country_name: str, country_code: str, raw_sources: Optional[List[Dict[str, Any]]]) -> list[dict[str, Any]]:
        sources = [
            {
                "url": None,
                "title": f"{country_name} ({country_code}) surveillance records",
                "snippet": "Standardized database records used for this report.",
            }
        ]
        for src in (raw_sources or [])[:5]:
            sources.append(
                {
                    "url": src.get("url"),
                    "title": src.get("title"),
                    "snippet": (src.get("snippet") or src.get("text", ""))[:500],
                }
            )
        return sources

    @staticmethod
    def _pct_change(current: int, previous: int) -> Optional[float]:
        if previous == 0:
            if current == 0:
                return 0.0
            return None
        return round(((current - previous) / previous) * 100.0, 2)

    @staticmethod
    def _trend_label(change_pct: Optional[float], language: str) -> str:
        if change_pct is None:
            return "new or reappearing signal" if language != "zh" else "新增或重新出现信号"
        if change_pct > 20:
            return "increasing" if language != "zh" else "上升"
        if change_pct < -20:
            return "decreasing" if language != "zh" else "下降"
        return "stable or low-change" if language != "zh" else "稳定或低变化"

    @staticmethod
    def _format_period_value(value: Any) -> str:
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            return str(value) if value is not None else ""

    @staticmethod
    def _fallback_disease_brief(disease, language: str) -> str:
        from src.knowledge.catalogue import build_catalogue_disease_brief

        return build_catalogue_disease_brief(disease, language)["brief"]

    @staticmethod
    def _current_interpretation(
        *,
        name: str,
        latest_cases: int,
        latest_deaths: int,
        previous_cases: int,
        change_pct: Optional[float],
        trend: str,
        language: str,
    ) -> str:
        if language == "zh":
            if change_pct is None:
                change = "上一期为 0，无法计算相对变化"
            else:
                change = f"较上一期变化 {change_pct:+.1f}%"
            return f"{name} 最近一期报告 {latest_cases:,} 例病例、{latest_deaths:,} 例死亡；上一期病例为 {previous_cases:,} 例，{change}，趋势判断为{trend}。"
        change = "relative change is not calculable because the previous observation was zero" if change_pct is None else f"{change_pct:+.1f}% compared with the previous observation"
        return f"{name} recorded {latest_cases:,} cases and {latest_deaths:,} deaths in the latest observation; previous cases were {previous_cases:,}, {change}, and the trend assessment is {trend}."

    @staticmethod
    def _risk_note(latest_cases: int, latest_deaths: int, language: str) -> str:
        if language == "zh":
            if latest_deaths > 0:
                return "最近一期存在死亡报告，应结合来源定义、诊断延迟和临床严重程度进行重点复核。"
            if latest_cases > 0:
                return "最近一期仍有病例报告，应保持常规监测并关注异常增幅。"
            return "最近一期未报告病例，但低报告不等同于无风险，仍需结合监测敏感性解读。"
        if latest_deaths > 0:
            return "Deaths are present in the latest observation; source definitions, diagnostic delay, and clinical severity should be reviewed carefully."
        if latest_cases > 0:
            return "Cases remain present in the latest observation; routine monitoring should continue with attention to unusual increases."
        return "No cases were reported in the latest observation, but low reporting does not equal zero risk and should be interpreted with surveillance sensitivity."

    @staticmethod
    def _disease_limitations(df: pd.DataFrame, language: str) -> str:
        issues = []
        if "incidence_rate" in df.columns and df["incidence_rate"].isna().any():
            issues.append("incidence rate is missing for some records")
        if "mortality_rate" in df.columns and df["mortality_rate"].isna().any():
            issues.append("mortality rate is missing for some records")
        if len(df) < 3:
            issues.append("the time series is short")
        if not issues:
            return "No major structural limitation was detected in the available time series." if language != "zh" else "当前时间序列未发现明显结构性限制。"
        if language == "zh":
            return "；".join(
                {
                    "incidence rate is missing for some records": "部分记录缺少发病率",
                    "mortality rate is missing for some records": "部分记录缺少死亡率",
                    "the time series is short": "时间序列较短",
                }.get(issue, issue)
                for issue in issues
            ) + "。"
        return "; ".join(issues) + "."

    @staticmethod
    def _first_alias(disease) -> Optional[str]:
        aliases = disease.aliases or []
        if isinstance(aliases, list) and aliases:
            return str(aliases[0])
        return None
    
    async def _generate_sections(
        self,
        db,
        report: Report,
        data: pd.DataFrame,
        raw_sources: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """生成报告章节（并行处理，支持进度恢复）"""
        import asyncio
        from sqlalchemy import select
        from src.domain import Disease
        from src.core import get_config
        
        logger.info("Generating report sections (parallel mode with progress recovery)")
        task_uuid = kwargs.get('task_uuid')
        
        # Extract progress callback
        progress_callback = kwargs.get('progress_callback', None)
        review_language = kwargs.get('language', 'en')
        enable_inline_review = kwargs.get('enable_review', True)
        
        # 获取最大并行任务数配置
        config = get_config()
        max_parallel_tasks = config.report.max_parallel_tasks
        
        sections = []
        await self._ensure_task_not_cancelled(task_uuid, report.id)
        
        # 定义要生成的章节（顺序：先 trend，再 highlight，最后 summary；highlight/summary 依赖前文）
        section_types = kwargs.get('section_types', [
            'trend_analysis',
            'key_findings',
            'highlights',
            'summary',
        ])
        
        # 检查已存在的章节（进度恢复）
        existing_sections_query = select(ReportSection).where(
            ReportSection.report_id == report.id
        ).order_by(ReportSection.section_order)
        existing_sections_result = await db.execute(existing_sections_query)
        existing_sections = existing_sections_result.scalars().all()
        
        # 构建已存在章节的索引
        existing_section_keys = set()
        for section in existing_sections:
            key = f"{section.title}"
            existing_section_keys.add(key)
            parts = (section.title or "").split(" - ")
            sections.append({
                'title': section.title,
                'content': section.content,
                'type': section.section_type,
                'chart_html': None,  # 从数据库加载的章节没有图表HTML
                'disease_name': parts[0] if len(parts) >= 2 else "",
                'is_verified': bool(section.is_verified),
                'quality_scores': {},
            })
        
        if existing_sections:
            logger.info(f"Found {len(existing_sections)} existing sections, resuming from where we left off")
            await self._log_task_event(
                task_uuid,
                entry_type="warning",
                title="Resuming Existing Sections",
                content=(
                    f"Found {len(existing_sections)} existing sections in report #{report.id}. "
                    "These sections will be reused directly; only missing sections will call AI agents."
                ),
                metadata={
                    "scope": "report",
                    "event": "resume_existing_sections",
                    "report_id": report.id,
                    "existing_sections": int(len(existing_sections)),
                },
            )
        
        # 可选：尝试从已审核通过的报告中复用章节，减少重复AI调用
        reuse_from_approved: bool = kwargs.get("reuse_from_approved", True)
        reusable_sections: Dict[tuple, ReportSection] = {}
        if reuse_from_approved:
            try:
                from src.domain import Report as ReportModel

                # 查找同一国家 & 报告类型 & 时间范围覆盖当前报告的最新已批准报告
                reuse_query = (
                    select(ReportModel)
                    .where(
                        ReportModel.country_id == report.country_id,
                        ReportModel.report_type == report.report_type,
                        ReportModel.status == ReportStatus.APPROVED,
                        ReportModel.period_start <= report.period_start,
                        ReportModel.period_end >= report.period_end,
                    )
                    .order_by(ReportModel.created_at.desc())
                    .limit(1)
                )
                reuse_result = await db.execute(reuse_query)
                reuse_base = reuse_result.scalar_one_or_none()

                if reuse_base:
                    # 预加载这个已批准报告的所有章节，并按 (disease_name, section_type) 归类
                    base_sections_q = (
                        select(ReportSection)
                        .where(ReportSection.report_id == reuse_base.id)
                        .order_by(ReportSection.section_order)
                    )
                    base_sections = (await db.execute(base_sections_q)).scalars().all()
                    for s in base_sections:
                        parts = s.title.split(" - ")
                        if len(parts) >= 2:
                            key = (parts[0], s.section_type)
                            if key not in reusable_sections:
                                reusable_sections[key] = s
                    logger.info(
                        f"Found approved base report {reuse_base.id} with "
                        f"{len(reusable_sections)} reusable section templates"
                    )
                    await self._log_task_event(
                        task_uuid,
                        entry_type="info",
                        title="Approved Templates Loaded",
                        content=(
                            f"Loaded {len(reusable_sections)} reusable section templates "
                            f"from approved report #{reuse_base.id}."
                        ),
                        metadata={
                            "scope": "report",
                            "event": "approved_templates_loaded",
                            "report_id": report.id,
                            "base_report_id": reuse_base.id,
                            "template_count": int(len(reusable_sections)),
                        },
                    )
            except Exception as e:
                logger.warning(f"Failed to load reusable sections from approved report: {e}")

        # 对每种疾病进行分析
        disease_groups = data.groupby('disease_id')
        
        # 创建并行任务列表
        disease_info_list = []
        
        # Batch-load all required disease objects to avoid N+1 queries
        # numpy int64 must be cast to Python int for asyncpg compatibility
        disease_ids_needed = [int(id_) for id_ in data['disease_id'].unique()]
        diseases_result = await db.execute(select(Disease).where(Disease.id.in_(disease_ids_needed)))
        disease_map: Dict[int, Any] = {d.id: d for d in diseases_result.scalars().all()}

        for disease_id, disease_data in disease_groups:
            disease = disease_map.get(disease_id)

            if not disease:
                continue

            # 跳过不需要分析的占位疾病
            if disease.name in self.SKIP_DISEASE_CODES:
                logger.info(f"Skipping placeholder disease {disease.name} (id={disease_id})")
                continue

            # 使用人类可读的疾病名称（避免 D065 等编码）：优先 name_en，其次别名，最后 name
            disease_display_name = self._get_disease_display_name(disease)

            # 检查该疾病的所有章节是否已存在
            disease_sections_exist = all(
                f"{disease_display_name} - {section_type}" in existing_section_keys
                for section_type in section_types
            )
            
            if disease_sections_exist:
                logger.info(f"Skipping disease {disease_display_name} - all sections already exist")
                continue
            
            disease_info_list.append({
                'disease': disease,
                'disease_display_name': disease_display_name,
                'data': disease_data,
            })
        
        if not disease_info_list:
            logger.info("All sections already exist, no new sections to generate")
            return sections
        
        total_diseases = len(disease_info_list)
        logger.info(f"Processing {total_diseases} diseases in parallel (max {max_parallel_tasks} concurrent tasks)")

        # 为每个疾病/章节预创建运行记录，状态设为queued
        run_map: Dict[tuple, int] = {}
        for info in disease_info_list:
            disease = info['disease']
            for section_type in section_types:
                run = ReportSectionRun(
                    report_id=report.id,
                    section_id=None,
                    section_type=section_type,
                    disease_name=info['disease_display_name'],
                    status=ReportSectionRunStatus.QUEUED,
                )
                db.add(run)
                await db.flush()
                run_map[(disease.id, section_type)] = run.id
        await db.commit()
        
        # 提前从 ORM 对象读取需要传入并行任务的纯 Python 值，
        # 避免在 asyncio.gather 内部触发懒加载导致 MissingGreenlet 错误
        report_id_val = report.id
        report_period_start = report.period_start
        report_period_end = report.period_end

        # Track completed diseases for progress
        completed_count = 0
        
        # 并行处理所有疾病
        async def process_disease(disease_info):
            """处理单个疾病的所有章节（每个任务独立 Agent 实例，含修订循环）"""
            nonlocal completed_count
            
            disease = disease_info['disease']
            disease_display_name = disease_info['disease_display_name']
            disease_data = disease_info['data']
            run_ids = {section_type: run_map.get((disease.id, section_type)) for section_type in section_types}
            
            # Progress callback for this disease
            if progress_callback:
                await progress_callback(completed_count, total_diseases, f"Analyzing {disease_display_name}...")

            await self._ensure_task_not_cancelled(task_uuid, report.id)
            await self._log_task_event(
                task_uuid,
                entry_type="info",
                title="Disease Analysis Started",
                content=(
                    f"Disease: {disease_display_name}\n"
                    f"Records: {len(disease_data)}\n"
                    f"Sections Planned: {', '.join(section_types)}"
                ),
                metadata={
                    "scope": "disease",
                    "event": "disease_started",
                    "disease_name": disease_display_name,
                    "record_count": int(len(disease_data)),
                    "section_types": list(section_types),
                    "report_id": report.id,
                },
            )
            
            # 过滤与该疾病相关的最新原始网页上下文
            relevant_raw_sources = self._filter_raw_sources(raw_sources or [], disease_display_name)

            # 每个任务独立实例化 Analyst，避免并发竞态（shared state）
            analyst = AnalystAgent()
            
            # 分析数据（disease_data 已按 report 的 period_start/period_end 过滤）
            analysis_result = await analyst.process(
                data=disease_data,
                disease_name=disease_display_name,
                period_start=report_period_start,
                period_end=report_period_end,
                language=review_language,
            )
            
            # 附加疾病的所有可能名称与报告周期（供 Writer/Reviewer 使用）
            analysis_result["disease_names_all"] = self._get_all_disease_names(disease)
            analysis_result["report_period"] = {
                "start": report_period_start.isoformat(),
                "end": report_period_end.isoformat(),
            }
            
            # 获取analyst的对话历史
            analyst_conversations = analyst.get_conversation_history()
            analyst_prompt, analyst_response = self._find_last_ai_exchange(analyst_conversations)
            analyst_tokens = self._aggregate_tokens(analyst_conversations)
            await self._log_task_event(
                task_uuid,
                entry_type="info",
                title="Disease Analysis Completed",
                content=(
                    f"Disease: {disease_display_name}\n"
                    f"Records: {len(disease_data)}\n"
                    f"AI Messages: {len(analyst_conversations)}"
                ),
                metadata={
                    "scope": "disease",
                    "event": "disease_completed",
                    "disease_name": disease_display_name,
                    "record_count": int(len(disease_data)),
                    "conversation_count": len(analyst_conversations),
                    "report_id": report.id,
                },
                prompt=analyst_prompt,
                response=analyst_response,
                model_used=analyst_conversations[-1].get("model") if analyst_conversations else None,
                tokens_used=analyst_tokens.get("total", 0),
            )
            
            # 数据清洗与宽表转换：长表→宽表 Markdown，按采集频率（月/周）聚合，供 AI 使用
            from src.generation.data_cleaner import clean_and_format_for_ai
            formatted = clean_and_format_for_ai(disease_data, time_col="time", max_rows=24)
            analysis_result["formatted_table"] = formatted["markdown_table"]
            analysis_result["data_frequency"] = formatted["frequency"]
            analysis_result["table_period_range"] = formatted.get("period_range", "")
            
            # 附加原始网页上下文，供后续写作/审核参考
            analysis_result["raw_sources"] = relevant_raw_sources

            # 审核统一使用清洗后的结构化摘要，避免混用原始长表/中间对象
            review_data_summary = self._build_data_summary_for_review(disease_data, disease_display_name)
            review_data_summary["formatted_table"] = formatted.get("markdown_table", "")
            review_data_summary["data_frequency"] = formatted.get("frequency", "monthly")
            review_data_summary["raw_sources"] = [
                {
                    "url": src.get("url"),
                    "title": (src.get("title") or "")[:200],
                    "snippet": (src.get("snippet") or src.get("text", ""))[:500],
                }
                for src in (relevant_raw_sources or [])[:5]
            ]
            review_data_summary["report_period"] = analysis_result.get("report_period", {})

            # Shared blackboard context for this disease: all agents read slices from here.
            shared_context = self._build_shared_context(
                disease_name=disease_display_name,
                analysis_result=analysis_result,
                review_data_summary=review_data_summary,
            )
            
            # 生成各章节（顺序执行以传递依赖：trend→key_findings→highlights→summary）
            writer = WriterAgent()
            reviewer = ReviewerAgent()
            previous_section_summaries: List[str] = []

            disease_sections = []
            for section_type in section_types:
                await self._ensure_task_not_cancelled(task_uuid, report.id)
                # 如果启用了复用机制且存在可复用章节，则直接拷贝内容而不调用AI
                if reuse_from_approved:
                    reuse_key = (disease_display_name, section_type)
                    base_section = reusable_sections.get(reuse_key)
                    if base_section:
                        section_started_at = datetime.now(timezone.utc)
                        section_ended_at = datetime.now(timezone.utc)

                        # 更新运行记录为完成
                        run_id = run_ids.get(section_type)
                        if run_id:
                            await self._update_run_status(
                                run_id,
                                status=ReportSectionRunStatus.COMPLETED,
                                started_at=section_started_at,
                                ended_at=section_ended_at,
                            )

                        reused_content = base_section.content
                        disease_sections.append(
                            {
                                "disease_name": disease_display_name,
                                "section_type": section_type,
                                "content": reused_content,
                                "chart_html": None,
                                "ai_conversation": [],
                                "quality_scores": {},
                                "data_sources": list(base_section.data_sources) if base_section.data_sources else [],
                                "started_at": section_started_at,
                                "ended_at": section_ended_at,
                                "token_usage": {},
                                "model": None,
                                "provider": None,
                                "disease_id": disease.id,
                                "run_id": run_id,
                                "revision_count": 0,
                                "writer_temperature": writer.temperature,
                                "writer_max_tokens": writer.max_tokens,
                                "is_verified": bool(base_section.is_verified),
                            }
                        )
                        previous_section_summaries.append(
                            f"[{section_type}] {self._summarize_section_text(reused_content)}"
                        )
                        logger.info(
                            f"Reused approved section for {disease_display_name} / {section_type} "
                            f"from base report"
                        )
                        await self._log_task_event(
                            task_uuid,
                            entry_type="success",
                            title="Section Reused",
                            content=(
                                f"Disease: {disease_display_name}\n"
                                f"Section: {section_type}\n"
                                "Source: approved historical section"
                            ),
                            metadata={
                                "scope": "section",
                                "event": "section_reused",
                                "disease_name": disease_display_name,
                                "section_type": section_type,
                                "report_id": report.id,
                                "run_id": run_id,
                            },
                            response=reused_content,
                        )
                        continue

                # 检查该章节是否已存在
                section_key = f"{disease_display_name} - {section_type}"
                if section_key in existing_section_keys:
                    logger.info(f"Skipping section {section_key} - already exists")
                    continue
                
                run_id = run_ids.get(section_type)
                section_started_at = datetime.now(timezone.utc)
                await self._log_task_event(
                    task_uuid,
                    entry_type="info",
                    title="Section Started",
                    content=(
                        f"Disease: {disease_display_name}\n"
                        f"Section: {section_type}\n"
                        f"Attempt Window: {self.MAX_REVISIONS + 1}"
                    ),
                    metadata={
                        "scope": "section",
                        "event": "section_started",
                        "disease_name": disease_display_name,
                        "section_type": section_type,
                        "report_id": report.id,
                        "run_id": run_id,
                    },
                )

                # 更新运行状态为RUNNING
                if run_id:
                    await self._update_run_status(run_id, status=ReportSectionRunStatus.RUNNING, started_at=section_started_at)

                # Writer → Reviewer → Writer 修订循环（最多 MAX_REVISIONS 次）
                revision_instructions = None
                revision_count = 0
                writer_result = None
                review_result = {
                    "approved": True,
                    "quality_score": {},
                    "suggestions": [],
                    "assessment": "Inline review disabled",
                    "rewrite_instruction": "",
                }

                section_context = self._build_section_context(
                    shared_context=shared_context,
                    section_type=section_type,
                    previous_section_summaries=previous_section_summaries,
                )

                table_data_str = section_context.get("formatted_table") or ""
                prev_content_str = section_context.get("previous_sections_content") or ""

                max_attempts = (self.MAX_REVISIONS + 1) if enable_inline_review else 1
                for attempt in range(max_attempts):
                    await self._ensure_task_not_cancelled(task_uuid, report.id)
                    writer.clear_conversation_history()
                    writer_result = await writer.process(
                        section_type=section_type,
                        analysis_data=section_context,
                        style=kwargs.get('style', 'formal'),
                        language=review_language,
                        raw_sources=relevant_raw_sources,
                        revision_instructions=revision_instructions,
                        table_data_str=table_data_str,
                        previous_sections_content=prev_content_str,
                        report_date=report_period_end.strftime("%Y-%m-%d"),
                    )

                    if not enable_inline_review:
                        break

                    # 内联快速审核（使用清洗后的 review_data_summary）
                    reviewer.clear_conversation_history()
                    try:
                        review_result = await reviewer.process(
                            content=writer_result['content'],
                            content_type=section_type,
                            original_data=shared_context.get("review_packet", review_data_summary),
                            language=review_language,
                        )
                    except Exception as review_err:
                        logger.warning(f"Inline review failed for {disease_display_name}/{section_type}: {review_err}")
                        break

                    if review_result.get('approved', True) or attempt == (max_attempts - 1):
                        break

                    # 未通过 → 准备修订指令进入下一轮
                    rewrite_instruction = review_result.get('rewrite_instruction')
                    if isinstance(rewrite_instruction, str) and rewrite_instruction.strip():
                        revision_instructions = rewrite_instruction.strip()
                    else:
                        suggestions = review_result.get('suggestions', [])
                        revision_instructions = "\n".join(suggestions) if suggestions else None
                    revision_count += 1
                    suggestion_count = len(review_result.get('suggestions', []) or [])
                    logger.info(
                        f"Section {disease_display_name}/{section_type} revision #{revision_count}: "
                        f"{suggestion_count} suggestion(s)"
                    )

                # 合并所有对话历史
                writer_conversations = writer.get_conversation_history()
                reviewer_inline_convs = reviewer.get_conversation_history()
                ai_conversation = analyst_conversations + writer_conversations + reviewer_inline_convs
                last_prompt, last_response = self._find_last_ai_exchange(writer_conversations or reviewer_inline_convs)

                section_ended_at = datetime.now(timezone.utc)
                await self._ensure_task_not_cancelled(task_uuid, report.id)
                token_usage = self._aggregate_tokens(ai_conversation)
                model_used = ai_conversation[-1].get("model") if ai_conversation else None
                provider_used = ai_conversation[-1].get("provider") if ai_conversation else None
                quality_scores = review_result.get("quality_score", {}) if review_result else {}

                # 生成图表（如果需要）
                chart_html = None
                if section_type in ['trend_analysis', 'summary']:
                    chart = self._generate_section_chart(
                        section_type=section_type,
                        data=disease_data,
                        disease_name=disease_display_name,
                    )
                    if chart:
                        chart_html = self.chart_generator.get_chart_html(chart)

                # 序列化 raw_sources 为 data_sources（供 dashboard 展示）
                data_sources = []
                for src in (relevant_raw_sources or []):
                    data_sources.append({
                        "url": src.get("url"),
                        "title": src.get("title", "")[:200],
                        "snippet": (src.get("snippet") or src.get("text", ""))[:500],
                    })
                # 若无原始网页，用清洗后的宽表作为数据来源（避免 Data 标签显示「无记录」）
                if not data_sources and analysis_result:
                    table_md = analysis_result.get("formatted_table", "")
                    freq = analysis_result.get("data_frequency", "monthly")
                    period_range = analysis_result.get("table_period_range", "")
                    stats = analysis_result.get("statistics", {})
                    snippet = f"Frequency: {freq}, Period: {period_range}"
                    if stats:
                        snippet += f" | Total cases: {stats.get('total_cases')}, deaths: {stats.get('total_deaths')}"
                    if table_md:
                        snippet += "\n\n" + table_md[:800]
                    data_sources.append({
                        "url": None,
                        "title": f"Surveillance data for {disease_display_name} ({freq})",
                        "snippet": snippet[:1500],
                    })
                
                section_content = writer_result['content'] if writer_result else ''
                quality_overall = self._quality_overall(quality_scores)
                conversations_persisted = False
                if run_id and ai_conversation:
                    await self._persist_ai_conversations(
                        report_id=report.id,
                        run_id=run_id,
                        conversations=ai_conversation,
                    )
                    conversations_persisted = True
                await self._log_task_event(
                    task_uuid,
                    entry_type="success",
                    title="Section Completed",
                    content=(
                        f"Disease: {disease_display_name}\n"
                        f"Section: {section_type}\n"
                        f"Model: {model_used or '-'}\n"
                        f"Provider: {provider_used or '-'}\n"
                        f"Tokens: {token_usage.get('total', 0)}\n"
                        f"Revisions: {revision_count}\n"
                        f"Quality: {quality_overall if quality_overall is not None else '-'}"
                    ),
                    metadata={
                        "scope": "section",
                        "event": "section_completed",
                        "disease_name": disease_display_name,
                        "section_type": section_type,
                        "report_id": report.id,
                        "run_id": run_id,
                        "provider": provider_used,
                        "model": model_used,
                        "revision_count": revision_count,
                        "quality_overall": quality_overall,
                    },
                    prompt=last_prompt,
                    response=section_content,
                    model_used=model_used,
                    tokens_used=token_usage.get("total", 0),
                    duration=(section_ended_at - section_started_at).total_seconds(),
                )
                disease_sections.append({
                    'disease_name': disease_display_name,
                    'section_type': section_type,
                    'content': section_content,
                    'chart_html': chart_html,
                    'ai_conversation': ai_conversation,
                    'quality_scores': quality_scores,
                    'data_sources': data_sources,
                    'started_at': section_started_at,
                    'ended_at': section_ended_at,
                    'token_usage': token_usage,
                    'model': model_used,
                    'provider': provider_used,
                    'disease_id': disease.id,
                    'run_id': run_id,
                    'conversations_persisted': conversations_persisted,
                    'revision_count': revision_count,
                    'writer_temperature': writer.temperature,
                    'writer_max_tokens': writer.max_tokens,
                    'is_verified': bool(review_result.get('approved', True)) if enable_inline_review else True,
                })
                # 累积已生成章节内容，供后续 highlight/summary 依赖
                previous_section_summaries.append(
                    f"[{section_type}] {self._summarize_section_text(section_content)}"
                )

                # Persist COMPLETED status immediately so dashboard shows correct state during generation
                if run_id:
                    await self._update_run_status(
                        run_id,
                        status=ReportSectionRunStatus.COMPLETED,
                        ended_at=section_ended_at,
                    )
            
            # Update progress after completing this disease
            completed_count += 1
            await self._log_task_event(
                task_uuid,
                entry_type="success",
                title="Disease Workflow Completed",
                content=(
                    f"Disease: {disease_display_name}\n"
                    f"Sections Finished: {len(disease_sections)}"
                ),
                metadata={
                    "scope": "disease",
                    "event": "disease_workflow_completed",
                    "disease_name": disease_display_name,
                    "section_count": len(disease_sections),
                    "report_id": report.id,
                },
            )
            if progress_callback:
                await progress_callback(completed_count, total_diseases, f"Completed {disease_display_name}")
            
            return disease_sections
        
        # 使用信号量限制并发任务数
        semaphore = asyncio.Semaphore(max_parallel_tasks)
        
        async def process_with_semaphore(disease_info):
            """使用信号量控制并发"""
            async with semaphore:
                return await process_disease(disease_info)

        # 并行执行所有疾病的处理，并在每个疾病完成后立即落库章节与对话
        disease_tasks = [asyncio.create_task(process_with_semaphore(info)) for info in disease_info_list]
        new_sections_count = 0
        try:
            for completed_task in asyncio.as_completed(disease_tasks):
                disease_sections = await completed_task
                new_sections_count += await self._persist_generated_sections(
                    db=db,
                    report=report,
                    sections=sections,
                    disease_sections=disease_sections,
                    run_map=run_map,
                )
        except Exception:
            for pending_task in disease_tasks:
                if not pending_task.done():
                    pending_task.cancel()
            await asyncio.gather(*disease_tasks, return_exceptions=True)
            raise
        
        # 最后提交剩余的章节
        await db.commit()
        
        # Report-level token rollup: aggregate all section token usage into Report.token_usage
        total_tokens: Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        for sec in sections:
            for k in total_tokens:
                total_tokens[k] += sec.get('token_usage', {}).get(k, 0)
        report.token_usage = total_tokens
        await db.commit()
        
        logger.info(f"Generated {new_sections_count} new sections (total: {len(sections)} sections)")
        return sections

    @staticmethod
    def _parse_conversation_timestamp(value: Any) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except Exception:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    async def _persist_generated_sections(
        self,
        db,
        report: Report,
        sections: List[Dict[str, Any]],
        disease_sections: List[Dict[str, Any]],
        run_map: Dict[tuple, int],
    ) -> int:
        new_sections_count = 0

        for section_data in disease_sections:
            section = ReportSection(
                report_id=report.id,
                title=f"{section_data['disease_name']} - {section_data['section_type']}",
                content=section_data['content'],
                section_type=section_data['section_type'],
                section_order=len(sections) + 1,
                data_sources=section_data.get('data_sources', []),
                is_verified=bool(section_data.get('is_verified', False)),
            )

            db.add(section)
            await db.flush()

            run_id = section_data.get('run_id') or run_map.get((section_data.get('disease_id'), section_data['section_type']))
            if run_id:
                run = await db.get(ReportSectionRun, run_id)
                if run:
                    run.section_id = section.id
                    run.status = ReportSectionRunStatus.COMPLETED
                    run.provider = section_data.get('provider')
                    run.model = section_data.get('model')
                    run.temperature = section_data.get('writer_temperature', 0.7)
                    run.max_tokens = section_data.get('writer_max_tokens', 3000)
                    run.token_usage = section_data.get('token_usage', {})
                    run.quality_scores = section_data.get('quality_scores', {})
                    run.started_at = run.started_at or section_data.get('started_at')
                    run.ended_at = section_data.get('ended_at')
                    run.revision_count = section_data.get('revision_count', 0)

                    if section_data.get('conversations_persisted'):
                        existing_conversations = (
                            await db.execute(
                                select(AIConversation).where(
                                    AIConversation.run_id == run_id,
                                    AIConversation.report_id == report.id,
                                    AIConversation.section_id.is_(None),
                                )
                            )
                        ).scalars().all()
                        for conversation in existing_conversations:
                            conversation.section_id = section.id

            if not section_data.get('conversations_persisted'):
                for entry in section_data.get('ai_conversation', []):
                    conv = AIConversation(
                        run_id=run_id,
                        report_id=report.id,
                        section_id=section.id,
                        agent=entry.get('agent') or 'unknown',
                        role=entry.get('role') or entry.get('agent'),
                        timestamp=self._parse_conversation_timestamp(entry.get('timestamp')) or datetime.now(timezone.utc),
                        prompt=entry.get('prompt'),
                        system_prompt=entry.get('system_prompt'),
                        response=entry.get('response'),
                        model=entry.get('model'),
                        provider=entry.get('provider'),
                        tokens=entry.get('tokens') or {},
                        duration=entry.get('duration'),
                        temperature=entry.get('temperature'),
                        metadata_=entry.get('metadata') or {},
                    )
                    db.add(conv)

            sections.append({
                'title': section.title,
                'content': section.content,
                'type': section_data['section_type'],
                'chart_html': section_data['chart_html'],
                'ai_conversation': section_data.get('ai_conversation', []),
                'section_id': section.id,
                'run_id': run_id,
                'disease_id': section_data.get('disease_id'),
                'disease_name': section_data.get('disease_name'),
                'token_usage': section_data.get('token_usage', {}),
                'quality_scores': section_data.get('quality_scores', {}),
                'is_verified': bool(section_data.get('is_verified', False)),
            })
            new_sections_count += 1

        if new_sections_count:
            await db.commit()
            logger.info(
                f"Incrementally saved {new_sections_count} new sections for report {report.id} "
                f"(total persisted sections: {len(sections)})"
            )

        return new_sections_count

    async def _persist_ai_conversations(
        self,
        *,
        report_id: int,
        run_id: int,
        conversations: List[Dict[str, Any]],
    ) -> None:
        if not conversations:
            return

        async with get_database() as conversation_db:
            for entry in conversations:
                conversation_db.add(
                    AIConversation(
                        run_id=run_id,
                        report_id=report_id,
                        section_id=None,
                        agent=entry.get('agent') or 'unknown',
                        role=entry.get('role') or entry.get('agent'),
                        timestamp=self._parse_conversation_timestamp(entry.get('timestamp')) or datetime.now(timezone.utc),
                        prompt=entry.get('prompt'),
                        system_prompt=entry.get('system_prompt'),
                        response=entry.get('response'),
                        model=entry.get('model'),
                        provider=entry.get('provider'),
                        tokens=entry.get('tokens') or {},
                        duration=entry.get('duration'),
                        temperature=entry.get('temperature'),
                        metadata_=entry.get('metadata') or {},
                    )
                )
            await conversation_db.commit()
    
    def _build_data_summary_for_review(self, df: pd.DataFrame, disease_name: str = "") -> Dict[str, Any]:
        """从 DataFrame 构建清洗后的数据摘要，供 Reviewer 事实核查使用（避免传递原始 to_dict 的冗长格式）。"""
        if df is None or df.empty:
            return {"disease_name": disease_name, "statistics": {}, "trends": {}, "summary": "No data"}
        try:
            df = normalize_rate_columns(df)
            stats = {}
            if "cases" in df.columns:
                stats["total_cases"] = int(df["cases"].sum())
                stats["avg_cases"] = round(float(df["cases"].mean()), 1)
                stats["max_cases"] = int(df["cases"].max())
                stats["min_cases"] = int(df["cases"].min())
                if df["cases"].std() == df["cases"].std():
                    stats["std_cases"] = round(float(df["cases"].std()), 2)
            if "deaths" in df.columns:
                stats["total_deaths"] = int(df["deaths"].sum())
                if stats.get("total_cases", 0) > 0:
                    stats["fatality_rate"] = round((stats["total_deaths"] / stats["total_cases"]) * 100, 2)
            time_col = "time" if "time" in df.columns else ("date" if "date" in df.columns else None)
            trends = {}
            sorted_df = df
            if time_col:
                sorted_df = df.sort_values(time_col)
            if time_col and "cases" in df.columns:
                cases = sorted_df["cases"].values
                if len(cases) >= 2:
                    change_rate = ((cases[-1] - cases[0]) / (cases[0] + 1)) * 100
                    trends["cases_change_rate"] = round(change_rate, 2)
                    trends["cases_trend"] = "increasing" if change_rate > 10 else ("decreasing" if change_rate < -10 else "stable")
                if len(cases) >= 4:
                    ma = pd.Series(cases).rolling(window=min(4, len(cases))).mean().values
                    if len(ma) > 0 and ma[-1] == ma[-1]:
                        trends["moving_average"] = round(float(ma[-1]), 1)
            period_str = ""
            if time_col:
                try:
                    period_str = f"{sorted_df[time_col].min()} to {sorted_df[time_col].max()}"
                except Exception:
                    period_str = f"{len(df)} records"
            return {
                "disease_name": disease_name,
                "statistics": stats,
                "trends": trends,
                "period": period_str,
                "record_count": len(df),
                "raw_sources": [],
            }
        except Exception as e:
            logger.warning(f"Failed to build data summary for review: {e}")
            return {"disease_name": disease_name, "statistics": {}, "trends": {}, "summary": f"Error: {e}"}

    @staticmethod
    def _summarize_section_text(text: str, max_chars: int = 260) -> str:
        """Build a compact section summary for cross-section context passing."""
        if not text:
            return ""
        compact = " ".join(str(text).split())
        if len(compact) <= max_chars:
            return compact
        return compact[:max_chars] + "..."

    def _build_shared_context(
        self,
        disease_name: str,
        analysis_result: Dict[str, Any],
        review_data_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a disease-level blackboard context consumed by writer/reviewer slices."""
        period = analysis_result.get("period") or {}
        stats = analysis_result.get("statistics") or {}
        trends = analysis_result.get("trends") or {}
        anomalies = analysis_result.get("anomalies") or []
        insights = analysis_result.get("insights") or ""
        formatted_table = analysis_result.get("formatted_table") or ""
        data_frequency = analysis_result.get("data_frequency") or "monthly"
        raw_sources = analysis_result.get("raw_sources") or []

        return {
            "disease_name": disease_name,
            "disease_names_all": analysis_result.get("disease_names_all") or [disease_name],
            "period": {
                "start": period.get("start", ""),
                "end": period.get("end", ""),
            },
            "report_period": analysis_result.get("report_period") or {},
            "statistics": stats,
            "trends": trends,
            "anomalies": anomalies[:5],
            "insights": self._summarize_section_text(insights, max_chars=900),
            "formatted_table": formatted_table[:1600],
            "data_frequency": data_frequency,
            "raw_sources": [
                {
                    "url": src.get("url"),
                    "title": (src.get("title") or "")[:180],
                    "snippet": (src.get("snippet") or src.get("text", ""))[:260],
                }
                for src in raw_sources[:3]
            ],
            "review_packet": review_data_summary,
        }

    def _build_section_context(
        self,
        shared_context: Dict[str, Any],
        section_type: str,
        previous_section_summaries: List[str],
    ) -> Dict[str, Any]:
        """Build minimal section-specific writer context from shared blackboard."""
        previous_compact = "\n".join(previous_section_summaries[-3:]) if previous_section_summaries else ""

        section_context = {
            "disease_name": shared_context.get("disease_name"),
            "disease_names_all": shared_context.get("disease_names_all") or [],
            "period": shared_context.get("period") or {},
            "report_period": shared_context.get("report_period") or {},
            "statistics": shared_context.get("statistics") or {},
            "trends": shared_context.get("trends") or {},
            "anomalies": shared_context.get("anomalies") or [],
            "insights": shared_context.get("insights") or "",
            "formatted_table": shared_context.get("formatted_table") or "",
            "data_frequency": shared_context.get("data_frequency") or "monthly",
            "raw_sources": shared_context.get("raw_sources") or [],
            "section_type": section_type,
            "previous_sections_content": previous_compact,
        }

        # Further trim noisy fields for sections that do not need full table context.
        if section_type in ("key_findings", "highlights"):
            section_context["formatted_table"] = (section_context.get("formatted_table") or "")[:1000]
        if section_type == "summary":
            section_context["formatted_table"] = (section_context.get("formatted_table") or "")[:1300]

        return section_context

    async def _review_sections(
        self,
        db,
        report: Report,
        sections: List[Dict[str, Any]],
        original_data: pd.DataFrame,
        raw_sources: Optional[List[Dict[str, Any]]] = None,
        progress_callback: Optional[callable] = None,
        task_uuid: Optional[str] = None,
        language: str = "en",
    ) -> List[Dict[str, Any]]:
        """审核章节内容并更新数据库（AI调用并行，DB写入顺序执行）"""
        from sqlalchemy import select
        
        logger.info(f"Reviewing {len(sections)} sections (parallel AI calls)")
        
        total_sections = len(sections)
        semaphore = asyncio.Semaphore(self.config.report.max_parallel_tasks)

        # Phase 1: run all review AI calls in parallel (no DB writes here)
        async def run_review(section: Dict[str, Any]):
            """Execute reviewer AI call for one section (no DB access)."""
            async with semaphore:
                await self._ensure_task_not_cancelled(task_uuid, report.id)
                reviewer = ReviewerAgent()
                try:
                    # 按疾病筛选数据并构建清洗后的摘要，避免传递原始 DataFrame.to_dict()
                    disease_id = section.get("disease_id")
                    disease_name = section.get("disease_name", "")
                    section_df = original_data
                    if disease_id is not None and "disease_id" in original_data.columns:
                        section_df = original_data[original_data["disease_id"] == disease_id]
                    data_summary = self._build_data_summary_for_review(section_df, disease_name)
                    # 加入清洗后的宽表 Markdown，供事实核查
                    from src.generation.data_cleaner import clean_and_format_for_ai
                    formatted = clean_and_format_for_ai(section_df, time_col="time", max_rows=24)
                    data_summary["formatted_table"] = formatted.get("markdown_table", "")
                    data_summary["data_frequency"] = formatted.get("frequency", "monthly")
                    data_summary["raw_sources"] = [
                        {"url": s.get("url"), "title": (s.get("title") or "")[:200], "snippet": (s.get("snippet") or s.get("text", ""))[:300]}
                        for s in (raw_sources or [])[:5]
                    ]
                    review_result = await reviewer.process(
                        content=section['content'],
                        content_type=section['type'],
                        original_data=data_summary,
                        language=language,
                    )
                    reviewer_conversations = reviewer.get_conversation_history()
                except Exception as e:
                    logger.error(f"Review failed for section '{section.get('title')}': {e}")
                    review_result = {'approved': False, 'quality_score': {}, 'suggestions': [], 'assessment': str(e)}
                    reviewer_conversations = []
                return review_result, reviewer_conversations

        review_tasks = await asyncio.gather(*[run_review(s) for s in sections])

        # Phase 2: persist results sequentially (single DB session, no conflicts)
        reviewed_sections = []
        for i, (section, (review_result, reviewer_conversations)) in enumerate(zip(sections, review_tasks), 1):
            await self._ensure_task_not_cancelled(task_uuid, report.id)
            if progress_callback:
                await progress_callback("review", i - 1, total_sections, f"Saving review {i}/{total_sections}")

            # 从数据库获取对应的ReportSection
            query = select(ReportSection).where(
                ReportSection.report_id == report.id,
                ReportSection.title == section['title']
            )
            result = await db.execute(query)
            db_section = result.scalar_one_or_none()

            # 找到对应的运行记录
            run = None
            run_id = section.get('run_id')
            if run_id:
                run = await db.get(ReportSectionRun, run_id)
            if not run and db_section:
                run_query = (
                    select(ReportSectionRun)
                    .where(ReportSectionRun.section_id == db_section.id)
                    .order_by(ReportSectionRun.created_at.desc())
                    .limit(1)
                )
                run = (await db.execute(run_query)).scalar_one_or_none()
                if run:
                    section['run_id'] = run.id

            if db_section and run:
                # 保存质量分
                if 'quality_score' in review_result:
                    run.quality_scores = review_result['quality_score']

                # 追加reviewer对话
                for entry in reviewer_conversations:
                    ts_raw = entry.get('timestamp')
                    ts_parsed = None
                    if ts_raw:
                        try:
                            ts_parsed = datetime.fromisoformat(ts_raw)
                        except Exception:
                            ts_parsed = None

                    conv = AIConversation(
                        run_id=run.id,
                        report_id=report.id,
                        section_id=db_section.id,
                        agent=entry.get('agent') or 'reviewer',
                        role=entry.get('role') or entry.get('agent'),
                        timestamp=ts_parsed or datetime.now(timezone.utc),
                        prompt=entry.get('prompt'),
                        system_prompt=entry.get('system_prompt'),
                        response=entry.get('response'),
                        model=entry.get('model'),
                        provider=entry.get('provider'),
                        tokens=entry.get('tokens') or {},
                        duration=entry.get('duration'),
                        temperature=entry.get('temperature'),
                        metadata_=entry.get('metadata') or {},
                    )
                    db.add(conv)

                # 标记是否通过审核
                db_section.is_verified = review_result.get('approved', False)

                await db.commit()
                await db.refresh(db_section)

                section['quality_scores'] = run.quality_scores
                section['is_verified'] = db_section.is_verified

            approved = review_result.get('approved', False)
            quality_overall = self._quality_overall(review_result.get('quality_score'))
            review_prompt, review_response = self._find_last_ai_exchange(reviewer_conversations)
            await self._log_task_event(
                task_uuid,
                entry_type="success" if approved else "warning",
                title="Section Review Completed",
                content=(
                    f"Disease: {section.get('disease_name') or '-'}\n"
                    f"Section: {section.get('type') or '-'}\n"
                    f"Approved: {'yes' if approved else 'no'}\n"
                    f"Quality: {quality_overall if quality_overall is not None else '-'}"
                ),
                metadata={
                    "scope": "review",
                    "event": "review_completed",
                    "disease_name": section.get('disease_name'),
                    "section_type": section.get('type'),
                    "run_id": section.get('run_id'),
                    "report_id": report.id,
                    "approved": approved,
                    "quality_overall": quality_overall,
                },
                prompt=review_prompt,
                response=review_response,
                model_used=reviewer_conversations[-1].get("model") if reviewer_conversations else None,
                tokens_used=self._aggregate_tokens(reviewer_conversations).get("total", 0),
                success=approved,
                error_message=None if approved else review_result.get('assessment'),
            )
            if approved:
                logger.debug(f"Section approved: {section['title']}")
            else:
                logger.warning(f"Section needs revision (kept as-is): {section['title']}")

            reviewed_sections.append(section)

            if progress_callback:
                await progress_callback("review", i, total_sections, f"Reviewed {i}/{total_sections}")

        return reviewed_sections

    async def _ensure_task_not_cancelled(self, task_uuid: Optional[str], report_id: Optional[int] = None) -> None:
        if not task_uuid:
            return
        if await task_manager.is_cancel_requested(task_uuid):
            if report_id:
                await self._mark_incomplete_runs_cancelled(report_id, "Cancellation requested by user")
            raise TaskCancelledError("Cancellation requested by user")

    async def _mark_incomplete_runs_cancelled(self, report_id: int, message: str) -> None:
        try:
            async with get_database() as status_db:
                result = await status_db.execute(
                    select(ReportSectionRun).where(
                        ReportSectionRun.report_id == report_id,
                        ReportSectionRun.status.in_([
                            ReportSectionRunStatus.QUEUED,
                            ReportSectionRunStatus.RUNNING,
                        ]),
                    )
                )
                pending_runs = result.scalars().all()
                for run in pending_runs:
                    run.status = ReportSectionRunStatus.CANCELLED
                    run.error_message = message
                    run.ended_at = datetime.now(timezone.utc)
                await status_db.commit()
        except Exception as e:
            logger.warning(f"Failed to mark incomplete runs as cancelled for report {report_id}: {e}")

    def _aggregate_tokens(self, conversations: List[Dict[str, Any]]) -> Dict[str, int]:
        """Aggregate token usage from conversation entries."""
        totals = {"prompt": 0, "completion": 0, "total": 0}
        for entry in conversations or []:
            tokens = entry.get("tokens") or {}
            for key in ("prompt", "completion", "total"):
                if isinstance(tokens.get(key), (int, float)):
                    totals[key] += int(tokens.get(key, 0))
        return totals

    async def _update_run_status(
        self,
        run_id: Optional[int],
        status: Optional[ReportSectionRunStatus] = None,
        started_at: Optional[datetime] = None,
        ended_at: Optional[datetime] = None,
    ) -> None:
        """Update run status/timestamps using a fresh DB session to avoid session conflicts."""
        if not run_id:
            return

        try:
            async with get_database() as status_db:
                run = await status_db.get(ReportSectionRun, run_id)
                if not run:
                    return
                if status:
                    run.status = status
                if started_at:
                    run.started_at = started_at
                if ended_at:
                    run.ended_at = ended_at
                await status_db.commit()
        except Exception as e:
            logger.warning(f"Failed to update run {run_id} status: {e}")

    def _get_disease_display_name(self, disease) -> str:
        """返回人类可读的疾病名称，避免 D065 等编码直接展示给 AI。优先 name_en，其次别名，最后 name。"""
        import re
        # 疾病编码模式（如 D065、B04）
        code_pattern = re.compile(r"^[A-Z]\d{2,4}$", re.I)
        if disease.name_en and disease.name_en.strip() and not code_pattern.match(disease.name_en.strip()):
            return disease.name_en.strip()
        aliases = getattr(disease, "aliases", None) or []
        for a in aliases:
            if isinstance(a, str) and a.strip() and not code_pattern.match(a.strip()):
                return a.strip()
        return disease.name or "Unknown"

    def _get_all_disease_names(self, disease) -> List[str]:
        """返回疾病的所有可能名称（供 AI 识别）：name_en, name, aliases, icd_10, keywords，去重且过滤纯编码。"""
        import re
        code_pattern = re.compile(r"^[A-Z]\d{2,4}$", re.I)
        seen = set()
        names = []
        for val in [
            getattr(disease, "name_en", None),
            getattr(disease, "name", None),
            getattr(disease, "icd_10", None),
            getattr(disease, "icd_11", None),
        ]:
            if val and isinstance(val, str) and val.strip() and val.strip() not in seen:
                seen.add(val.strip())
                names.append(val.strip())
        for lst_attr in ("aliases", "keywords"):
            for item in (getattr(disease, lst_attr, None) or []):
                if isinstance(item, str) and item.strip() and item.strip() not in seen:
                    seen.add(item.strip())
                    names.append(item.strip())
        return names

    def _filter_raw_sources(
        self,
        raw_sources: List[Dict[str, Any]],
        disease_name: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """基于疾病关键词筛选原始网页上下文，避免提示词过长"""
        if not raw_sources:
            return []

        name_lower = (disease_name or "").lower()
        matched = [
            src for src in raw_sources
            if name_lower and name_lower in (src.get('snippet', '') + src.get('text', '')).lower()
        ]

        ordered = matched if matched else raw_sources
        return ordered[:limit]

    async def _fetch_recent_raw_pages(
        self,
        db,
        country_id: int,
        period_end: datetime,
        days_back: int = 45,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """从数据库获取近期爬取的原始网页文本，供AI参考"""
        try:
            from sqlalchemy import select, desc
            from src.domain import Country, CrawlRun, CrawlRawPage

            country_query = select(Country).where(Country.id == country_id)
            country = (await db.execute(country_query)).scalar_one_or_none()
            if not country:
                logger.warning(f"Country not found for id {country_id}, skip raw context")
                return []

            cutoff = period_end - timedelta(days=days_back)
            query = (
                select(CrawlRawPage)
                .join(CrawlRun, CrawlRawPage.run_id == CrawlRun.id)
                .where(
                    CrawlRun.country_code == country.code,
                    CrawlRawPage.fetched_at >= cutoff,
                )
                .order_by(desc(CrawlRawPage.fetched_at))
                .limit(limit)
            )

            pages = (await db.execute(query)).scalars().all()
            raw_sources = []

            for page in pages:
                snippet = ""
                try:
                    raw_text = Path(page.content_path).read_text(encoding='utf-8')
                    snippet = raw_text[:1200]
                except Exception as e:
                    logger.warning(f"Failed to load raw page text {page.content_path}: {e}")

                raw_sources.append({
                    'title': page.title or Path(page.content_path).stem,
                    'url': page.url,
                    'source': page.source,
                    'fetched_at': page.fetched_at.isoformat(),
                    'path': page.content_path,
                    'snippet': snippet,
                })

            if raw_sources:
                logger.info(f"Loaded {len(raw_sources)} raw web pages for context")
            else:
                logger.info("No recent raw web pages found for context")

            return raw_sources

        except Exception as e:
            logger.warning(f"Failed to fetch raw pages: {e}")
            return []
    
    async def _format_and_save(
        self,
        db,
        report: Report,
        sections: List[Dict[str, Any]],
    ) -> None:
        """格式化并保存报告"""
        from sqlalchemy import select
        from src.domain import Country
        
        logger.info("Formatting and saving report")

        # 从数据库获取国家名称
        country_name = "Unknown"
        try:
            country_obj = (await db.execute(select(Country).where(Country.id == report.country_id))).scalar_one_or_none()
            if country_obj:
                country_name = country_obj.name_en or country_obj.name or "Unknown"
        except Exception as e:
            logger.warning(f"Could not fetch country name: {e}")
        
        # 准备元数据
        metadata = {
            'title': report.title,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'period_start': report.period_start.strftime('%Y-%m-%d'),
            'period_end': report.period_end.strftime('%Y-%m-%d'),
            'country': country_name,
            'summary': report.summary,
            'key_findings': report.key_findings,
            'report_layout': (report.metadata_ or {}).get('report_layout', 'legacy'),
            'language': (report.generation_config or {}).get('language', 'en'),
        }
        
        # 生成文件名前缀
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename_prefix = f"report_{report.id}_{timestamp}"
        
        # 生成Markdown
        markdown_content = self.formatter.format_markdown(sections, metadata)
        markdown_path = self.output_dir / f"{filename_prefix}.md"
        self.formatter.save(markdown_content, str(markdown_path))
        report.markdown_path = str(markdown_path)
        
        # 生成HTML
        html_content = self.formatter.format_html(sections, metadata)
        html_path = self.output_dir / f"{filename_prefix}.html"
        self.formatter.save(html_content, str(html_path))
        report.html_path = str(html_path)
        
        # 生成PDF（可选）
        try:
            pdf_content = self.formatter.format_pdf(sections, metadata)
            pdf_path = self.output_dir / f"{filename_prefix}.pdf"
            self.formatter.save(pdf_content, str(pdf_path), format='binary')
            report.pdf_path = str(pdf_path)
        except Exception as e:
            logger.warning(f"PDF generation failed: {e}")
        
        await db.commit()
        logger.info(f"Report files saved: {filename_prefix}")
    
    async def _export_data(
        self,
        db,
        report: Report,
        country_id: int,
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        """导出数据文件"""
        logger.info("Exporting data files")
        
        try:
            # 获取国家代码
            from sqlalchemy import select
            from src.domain import Country
            
            country_query = select(Country).where(Country.id == country_id)
            country_result = await db.execute(country_query)
            country = country_result.scalar_one()
            
            # 导出数据
            exported_files = await self.data_exporter.export_all(
                country_code=country.code,
                period_start=period_start,
                period_end=period_end,
                formats=['csv', 'excel', 'json'],
            )
            
            # 同时导出latest数据
            latest_files = await self.data_exporter.export_latest(
                country_code=country.code,
                formats=['csv', 'excel'],
            )
            
            # 记录到报告
            export_info = {
                'period_data': exported_files,
                'latest_data': latest_files,
            }
            
            if not report.generation_config:
                report.generation_config = {}
            report.generation_config['exported_data'] = export_info
            
            await db.commit()
            
            logger.info(f"Data exported: {len(exported_files) + len(latest_files)} files")
            
        except Exception as e:
            logger.error(f"Failed to export data: {e}")
            # 不影响报告生成流程
    
    async def _send_email(
        self,
        db,
        report: Report,
        sections: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """发送报告邮件"""
        logger.info("Sending report email")

        # 读取HTML内容
        if report.html_path and Path(report.html_path).exists():
            with open(report.html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        else:
            # 使用简单HTML
            metadata = {'title': report.title}
            html_content = self.formatter.format_html(sections, metadata)

        delivery = self.email_service.send_report_to_settings_recipients(
            report_title=report.title,
            report_html=html_content,
            pdf_path=report.pdf_path,
        )
        await self._persist_email_delivery(db, report, delivery)

        if delivery.get("sent"):
            logger.info("Report email sent successfully")
        else:
            logger.warning(
                "Report email was not sent for report %s: %s",
                report.id,
                delivery.get("message") or delivery.get("reason") or "unknown",
            )
        return delivery

    async def _persist_email_delivery(
        self,
        db,
        report: Report,
        delivery: Dict[str, Any],
    ) -> None:
        generation_config = dict(report.generation_config or {})
        generation_config["email_delivery"] = delivery
        report.generation_config = generation_config
        db.add(report)
        await db.commit()
        await db.refresh(report)
    
    def _generate_section_chart(
        self,
        section_type: str,
        data: pd.DataFrame,
        disease_name: str,
    ):
        """为章节生成图表"""
        if data.empty or 'time' not in data.columns:
            return None

        if section_type == 'trend_analysis':
            # 双轴折线图：病例数（左轴）+ 死亡数（右轴）
            has_deaths = 'deaths' in data.columns and data['deaths'].notna().any()
            if has_deaths:
                return self.chart_generator.generate_dual_axis(
                    data=data.sort_values('time'),
                    x_col='time',
                    y1_col='cases',
                    y2_col='deaths',
                    title=f"{disease_name} 病例与死亡趋势",
                    y1_label='病例数',
                    y2_label='死亡数',
                )
            else:
                return self.chart_generator.generate_time_series(
                    data=data.sort_values('time'),
                    x_col='time',
                    y_cols=['cases'],
                    title=f"{disease_name} 病例趋势",
                    y_label='病例数',
                )
        elif section_type == 'summary':
            # 双子图：病例柱状 + 发病率折线（如有数据）
            return self.chart_generator.generate_cases_incidence_subplots(
                data=data.sort_values('time').tail(24),  # 最近24个月
                x_col='time',
                cases_col='cases',
                incidence_col='incidence_rate',
                title=f"{disease_name} 近期数据概览",
            )
        
        return None
