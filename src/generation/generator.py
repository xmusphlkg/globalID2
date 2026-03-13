"""
GlobalID V2 Report Generator

报告生成器：整合所有组件生成完整报告
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.core import get_config, get_database, get_logger
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
    3. 内容撰写
    4. 质量审核
    5. 格式化输出
    6. 邮件发送
    """
    
    def __init__(self):
        """初始化报告生成器"""
        self.config = get_config()
        
        # 初始化各组件
        self.analyst = AnalystAgent()
        self.writer = WriterAgent()
        self.reviewer = ReviewerAgent()
        self.chart_generator = ChartGenerator()
        self.data_exporter = DataExporter()
        self.formatter = ReportFormatter()
        self.email_service = EmailService()
        
        # 输出目录
        self.output_dir = Path(self.config.app.base_dir) / self.config.report.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("ReportGenerator initialized")
    
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
        
        # Helper function to call progress callback safely
        async def notify_progress(stage, current, total, message):
            if progress_callback:
                await progress_callback(stage, current, total, message)
        
        # 1. 创建报告记录
        report = await self._create_report_record(
            db,
            country_id=country_id,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            **kwargs
        )
        
        try:
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
            
            sections = await self._generate_sections(
                db,
                report=report,
                data=data,
                raw_sources=raw_sources,
                **sections_kwargs
            )
            
            await notify_progress("writing", 1, 1, f"Generated {len(sections)} sections")
            
            # 4. 审核内容
            if kwargs.get('enable_review', True):
                await notify_progress("review", 0, len(sections), "Starting content review...")
                sections = await self._review_sections(db, report, sections, data, raw_sources, progress_callback=notify_progress)
                await notify_progress("review", len(sections), len(sections), "Review complete")
            
            # 5. 格式化并保存
            await self._format_and_save(db, report, sections)
            
            # 6. 导出数据文件（如果配置）
            if kwargs.get('export_data', True):
                await self._export_data(db, report, country_id, period_start, period_end)
            
            # 7. 发送邮件（如果配置）
            if kwargs.get('send_email', False):
                await self._send_email(report, sections)
            
            # 8. 更新状态
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now(timezone.utc)
            await db.commit()
            
            logger.info(f"Report generation completed: {report.id}")
            return report
        
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            report.status = ReportStatus.FAILED
            report.error_message = str(e)
            await db.commit()
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
        report = Report(
            country_id=country_id,
            report_type=report_type,
            title=kwargs.get('title', f"{report_type.value}报告"),
            status=ReportStatus.GENERATING,
            period_start=period_start,
            period_end=period_end,
            generation_config=kwargs.get('config', {}),
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
            'recoveries': r.recoveries,
            'incidence_rate': r.incidence_rate,
            'mortality_rate': r.mortality_rate,
            'recovery_rate': r.recovery_rate,
        } for r in records])
        
        logger.info(f"Extracted {len(data)} records")
        return data
    
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
        
        # Extract progress callback
        progress_callback = kwargs.get('progress_callback', None)
        
        # 获取最大并行任务数配置
        config = get_config()
        max_parallel_tasks = config.report.max_parallel_tasks
        
        sections = []
        
        # 定义要生成的章节
        section_types = kwargs.get('section_types', [
            'summary',
            'trend_analysis',
            'key_findings',
            'recommendations',
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
            sections.append({
                'title': section.title,
                'content': section.content,
                'type': section.section_type,
                'chart_html': None,  # 从数据库加载的章节没有图表HTML
            })
        
        if existing_sections:
            logger.info(f"Found {len(existing_sections)} existing sections, resuming from where we left off")
        
        # 对每种疾病进行分析
        disease_groups = data.groupby('disease_id')
        
        # 创建并行任务列表
        disease_info_list = []
        
        skip_disease_names = {"D999"}

        for disease_id, disease_data in disease_groups:
            # 获取疾病信息
            disease_query = select(Disease).where(Disease.id == disease_id)
            disease_result = await db.execute(disease_query)
            disease = disease_result.scalar_one_or_none()
            
            if not disease:
                continue

            # 跳过不需要分析的占位疾病
            if disease.name in skip_disease_names:
                logger.info(f"Skipping placeholder disease {disease.name} (id={disease_id})")
                continue
            
            # 检查该疾病的所有章节是否已存在
            disease_sections_exist = all(
                f"{disease.name} - {section_type}" in existing_section_keys
                for section_type in section_types
            )
            
            if disease_sections_exist:
                logger.info(f"Skipping disease {disease.name} - all sections already exist")
                continue
            
            disease_info_list.append({
                'disease': disease,
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
                    disease_name=disease.name,
                    status=ReportSectionRunStatus.QUEUED,
                )
                db.add(run)
                await db.flush()
                run_map[(disease.id, section_type)] = run.id
        await db.commit()
        
        # Track completed diseases for progress
        completed_count = 0
        
        # 并行处理所有疾病
        async def process_disease(disease_info):
            """处理单个疾病的所有章节"""
            nonlocal completed_count
            
            disease = disease_info['disease']
            disease_data = disease_info['data']
            run_ids = {section_type: run_map.get((disease.id, section_type)) for section_type in section_types}
            
            # Progress callback for this disease
            if progress_callback:
                await progress_callback(completed_count, total_diseases, f"Analyzing {disease.name}...")
            
            # 过滤与该疾病相关的最新原始网页上下文
            relevant_raw_sources = self._filter_raw_sources(raw_sources or [], disease.name)

            # 清除之前的对话历史（每个疾病/section重新开始）
            self.analyst.clear_conversation_history()
            
            # 分析数据
            analysis_result = await self.analyst.process(
                data=disease_data,
                disease_name=disease.name,
                period_start=report.period_start,
                period_end=report.period_end,
            )
            
            # 获取analyst的对话历史
            analyst_conversations = self.analyst.get_conversation_history()
            
            # 附加原始网页上下文，供后续写作/审核参考
            analysis_result["raw_sources"] = relevant_raw_sources
            
            # 生成各章节
            disease_sections = []
            for section_type in section_types:
                # 检查该章节是否已存在
                section_key = f"{disease.name} - {section_type}"
                if section_key in existing_section_keys:
                    logger.info(f"Skipping section {section_key} - already exists")
                    continue
                
                section_started_at = datetime.now(timezone.utc)

                # 更新运行状态为RUNNING
                run_id = run_ids.get(section_type)
                if run_id:
                    await self._update_run_status(run_id, status=ReportSectionRunStatus.RUNNING, started_at=section_started_at)

                # 清除writer的对话历史（每个section重新开始）
                self.writer.clear_conversation_history()
                
                writer_result = await self.writer.process(
                    section_type=section_type,
                    analysis_data=analysis_result,
                    style=kwargs.get('style', 'formal'),
                    language=kwargs.get('language', 'zh'),
                    raw_sources=relevant_raw_sources,
                )
                
                # 获取writer的对话历史
                writer_conversations = self.writer.get_conversation_history()
                
                # 合并analyst和writer的对话历史
                ai_conversation = analyst_conversations + writer_conversations

                section_ended_at = datetime.now(timezone.utc)
                token_usage = self._aggregate_tokens(ai_conversation)
                model_used = ai_conversation[-1].get("model") if ai_conversation else None
                provider_used = ai_conversation[-1].get("provider") if ai_conversation else None
                
                # 生成图表（如果需要）
                chart_html = None
                if section_type in ['trend_analysis', 'summary']:
                    chart = self._generate_section_chart(
                        section_type=section_type,
                        data=disease_data,
                        disease_name=disease.name,
                    )
                    if chart:
                        chart_html = self.chart_generator.get_chart_html(chart)
                
                disease_sections.append({
                    'disease_name': disease.name,
                    'section_type': section_type,
                    'content': writer_result['content'],
                    'chart_html': chart_html,
                    'ai_conversation': ai_conversation,
                    'started_at': section_started_at,
                    'ended_at': section_ended_at,
                    'token_usage': token_usage,
                    'model': model_used,
                    'provider': provider_used,
                    'disease_id': disease.id,
                    'run_id': run_id,
                })
            
            # Update progress after completing this disease
            completed_count += 1
            if progress_callback:
                await progress_callback(completed_count, total_diseases, f"Completed {disease.name}")
            
            return disease_sections
        
        # 使用信号量限制并发任务数
        semaphore = asyncio.Semaphore(max_parallel_tasks)
        
        async def process_with_semaphore(disease_info):
            """使用信号量控制并发"""
            async with semaphore:
                return await process_disease(disease_info)
        
        # 并行执行所有疾病的处理（受信号量限制）
        results = await asyncio.gather(*[process_with_semaphore(info) for info in disease_info_list])
        
        # 收集所有章节并保存到数据库（增量保存）
        new_sections_count = 0
        for disease_sections in results:
            for section_data in disease_sections:
                # 创建章节记录
                section = ReportSection(
                    report_id=report.id,
                    title=f"{section_data['disease_name']} - {section_data['section_type']}",
                    content=section_data['content'],
                    section_type=section_data['section_type'],
                    section_order=len(sections) + 1,
                )

                db.add(section)
                await db.flush()  # 获取section.id供运行记录使用

                # 更新运行记录为完成
                run_id = section_data.get('run_id') or run_map.get((section_data.get('disease_id'), section_data['section_type']))
                if run_id:
                    run = await db.get(ReportSectionRun, run_id)
                    if run:
                        run.section_id = section.id
                        run.status = ReportSectionRunStatus.COMPLETED
                        run.provider = section_data.get('provider')
                        run.model = section_data.get('model')
                        run.temperature = self.writer.temperature
                        run.max_tokens = self.writer.max_tokens
                        run.token_usage = section_data.get('token_usage', {})
                        run.started_at = run.started_at or section_data.get('started_at')
                        run.ended_at = section_data.get('ended_at')

                # 保存AI对话记录
                for entry in section_data.get('ai_conversation', []):
                    ts_raw = entry.get('timestamp')
                    ts_parsed = None
                    if ts_raw:
                        try:
                            ts_parsed = datetime.fromisoformat(ts_raw)
                        except Exception:
                            ts_parsed = None

                    conv = AIConversation(
                        run_id=run_id,
                        report_id=report.id,
                        section_id=section.id,
                        agent=entry.get('agent') or 'unknown',
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

                new_sections_count += 1
                
                sections.append({
                    'title': section.title,
                    'content': section.content,
                    'type': section_data['section_type'],
                    'chart_html': section_data['chart_html'],
                    'ai_conversation': section_data.get('ai_conversation', []),
                    'section_id': section.id,
                    'run_id': run_id,
                    'token_usage': section_data.get('token_usage', {}),
                })
                
                # 每生成一个章节就提交一次，确保进度保存
                if new_sections_count % 5 == 0:  # 每5个章节提交一次
                    await db.commit()
                    logger.info(f"Progress saved: {new_sections_count} new sections generated")
        
        # 最后提交剩余的章节
        await db.commit()
        
        logger.info(f"Generated {new_sections_count} new sections (total: {len(sections)} sections)")
        return sections
    
    async def _review_sections(
        self,
        db,
        report: Report,
        sections: List[Dict[str, Any]],
        original_data: pd.DataFrame,
        raw_sources: Optional[List[Dict[str, Any]]] = None,
        progress_callback: Optional[callable] = None,
    ) -> List[Dict[str, Any]]:
        """审核章节内容并更新数据库"""
        from sqlalchemy import select
        
        logger.info("Reviewing sections")
        
        reviewed_sections = []
        total_sections = len(sections)
        
        for i, section in enumerate(sections, 1):
            # Progress callback
            if progress_callback:
                await progress_callback("review", i-1, total_sections, f"Reviewing section {i}/{total_sections}")
            
            # 清除reviewer的对话历史
            self.reviewer.clear_conversation_history()
            
            review_result = await self.reviewer.process(
                content=section['content'],
                content_type=section['type'],
                original_data={
                    'structured_data': original_data.to_dict(),
                    'raw_sources': raw_sources or [],
                },
            )
            
            # 获取reviewer的对话历史
            reviewer_conversations = self.reviewer.get_conversation_history()
            
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
                    .order_by(desc(ReportSectionRun.created_at))
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

                # 提交更新
                await db.commit()
                await db.refresh(db_section)

                # 更新section字典
                section['quality_scores'] = run.quality_scores
                section['is_verified'] = db_section.is_verified
            
            if review_result['approved']:
                reviewed_sections.append(section)
                logger.debug(f"Section approved: {section['title']}")
            else:
                logger.warning(f"Section needs revision: {section['title']}")
                # 可以选择重新生成或保留原内容
                reviewed_sections.append(section)
            
            # Update progress after each review
            if progress_callback:
                await progress_callback("review", i, total_sections, f"Reviewed {i}/{total_sections}")
        
        return reviewed_sections

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
        logger.info("Formatting and saving report")
        
        # 准备元数据
        metadata = {
            'title': report.title,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'period_start': report.period_start.strftime('%Y-%m-%d'),
            'period_end': report.period_end.strftime('%Y-%m-%d'),
            'country': 'China',  # TODO: 从数据库获取
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
        report: Report,
        sections: List[Dict[str, Any]],
    ) -> None:
        """发送报告邮件"""
        logger.info("Sending report email")
        
        # 获取收件人列表
        recipients = self.config.email.default_recipients
        
        if not recipients:
            logger.warning("No email recipients configured")
            return
        
        # 读取HTML内容
        if report.html_path:
            with open(report.html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        else:
            # 使用简单HTML
            metadata = {'title': report.title}
            html_content = self.formatter.format_html(sections, metadata)
        
        # 发送邮件
        success = self.email_service.send_report(
            to_addrs=recipients,
            report_title=report.title,
            report_html=html_content,
            pdf_path=report.pdf_path,
        )
        
        if success:
            logger.info("Report email sent successfully")
        else:
            logger.error("Failed to send report email")
    
    def _generate_section_chart(
        self,
        section_type: str,
        data: pd.DataFrame,
        disease_name: str,
    ):
        """为章节生成图表"""
        if section_type == 'trend_analysis':
            return self.chart_generator.generate_time_series(
                data=data,
                x_col='time',
                y_cols=['cases'],
                title=f"{disease_name} 病例趋势",
                y_label='病例数',
            )
        elif section_type == 'summary':
            return self.chart_generator.generate_bar_chart(
                data=data.tail(10),  # 最近10个数据点
                x_col='time',
                y_col='cases',
                title=f"{disease_name} 近期数据",
            )
        
        return None
