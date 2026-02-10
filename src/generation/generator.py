"""
GlobalID V2 Report Generator

报告生成器：整合所有组件生成完整报告
"""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.core import get_config, get_database, get_logger
from src.domain import Report, ReportSection, ReportStatus, ReportType
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
        self.db = get_database()
        
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
            **kwargs: 额外参数
            
        Returns:
            生成的报告对象
        """
        logger.info(f"Starting report generation: {report_type} for country {country_id}")
        
        # 1. 创建报告记录
        report = await self._create_report_record(
            country_id=country_id,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            **kwargs
        )
        
        try:
            # 2. 提取数据
            data = await self._extract_data(
                country_id=country_id,
                period_start=period_start,
                period_end=period_end,
                diseases=diseases,
            )
            
            if data.empty:
                logger.warning("No data found for report")
                report.status = ReportStatus.FAILED
                report.error_message = "No data available"
                await self.db.commit()
                return report
            
            # 3. 生成章节
            sections = await self._generate_sections(
                report=report,
                data=data,
                **kwargs
            )
            
            # 4. 审核内容
            if kwargs.get('enable_review', True):
                sections = await self._review_sections(sections, data)
            
            # 5. 格式化并保存
            await self._format_and_save(report, sections)
            
            # 6. 导出数据文件（如果配置）
            if kwargs.get('export_data', True):
                await self._export_data(report, country_id, period_start, period_end)
            
            # 7. 发送邮件（如果配置）
            if kwargs.get('send_email', False):
                await self._send_email(report, sections)
            
            # 7. 更新状态
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.utcnow()
            await self.db.commit()
            
            logger.info(f"Report generation completed: {report.id}")
            return report
        
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            report.status = ReportStatus.FAILED
            report.error_message = str(e)
            await self.db.commit()
            raise
    
    async def _create_report_record(
        self,
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
        
        self.db.add(report)
        await self.db.commit()
        await self.db.refresh(report)
        
        logger.info(f"Created report record: {report.id}")
        return report
    
    async def _extract_data(
        self,
        country_id: int,
        period_start: datetime,
        period_end: datetime,
        diseases: Optional[List[int]] = None,
    ) -> pd.DataFrame:
        """提取数据"""
        from sqlalchemy import select
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
        result = await self.db.execute(query)
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
            'fatality_rate': r.fatality_rate,
        } for r in records])
        
        logger.info(f"Extracted {len(data)} records")
        return data
    
    async def _generate_sections(
        self,
        report: Report,
        data: pd.DataFrame,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """生成报告章节"""
        logger.info("Generating report sections")
        
        sections = []
        
        # 定义要生成的章节
        section_types = kwargs.get('section_types', [
            'summary',
            'trend_analysis',
            'key_findings',
            'recommendations',
        ])
        
        # 对每种疾病进行分析
        disease_groups = data.groupby('disease_id')
        
        for disease_id, disease_data in disease_groups:
            # 获取疾病信息
            from sqlalchemy import select
            from src.domain import Disease
            
            disease_query = select(Disease).where(Disease.id == disease_id)
            disease_result = await self.db.execute(disease_query)
            disease = disease_result.scalar_one_or_none()
            
            if not disease:
                continue
            
            # 分析数据
            analysis_result = await self.analyst.process(
                data=disease_data,
                disease_name=disease.name,
                period_start=report.period_start,
                period_end=report.period_end,
            )
            
            # 生成各章节
            for section_type in section_types:
                writer_result = await self.writer.process(
                    section_type=section_type,
                    analysis_data=analysis_result,
                    style=kwargs.get('style', 'formal'),
                    language=kwargs.get('language', 'zh'),
                )
                
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
                
                # 创建章节记录
                section = ReportSection(
                    report_id=report.id,
                    title=f"{disease.name} - {writer_result['section_type']}",
                    content=writer_result['content'],
                    section_type=section_type,
                    order=len(sections) + 1,
                )
                
                self.db.add(section)
                
                sections.append({
                    'title': section.title,
                    'content': section.content,
                    'type': section_type,
                    'chart_html': chart_html,
                })
        
        await self.db.commit()
        
        logger.info(f"Generated {len(sections)} sections")
        return sections
    
    async def _review_sections(
        self,
        sections: List[Dict[str, Any]],
        original_data: pd.DataFrame,
    ) -> List[Dict[str, Any]]:
        """审核章节内容"""
        logger.info("Reviewing sections")
        
        reviewed_sections = []
        
        for section in sections:
            review_result = await self.reviewer.process(
                content=section['content'],
                content_type=section['type'],
                original_data=original_data.to_dict(),
            )
            
            if review_result['approved']:
                reviewed_sections.append(section)
                logger.debug(f"Section approved: {section['title']}")
            else:
                logger.warning(f"Section needs revision: {section['title']}")
                # 可以选择重新生成或保留原内容
                reviewed_sections.append(section)
        
        return reviewed_sections
    
    async def _format_and_save(
        self,
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
        
        await self.db.commit()
        logger.info(f"Report files saved: {filename_prefix}")
    
    async def _export_data(
        self,
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
            country_result = await self.db.execute(country_query)
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
            
            await self.db.commit()
            
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
