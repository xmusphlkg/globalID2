"""
GlobalID V2 Report Models

报告模型：存储生成的报告及其章节
"""
from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class ReportStatus(str, PyEnum):
    """报告状态枚举"""
    PENDING = "pending"  # 待生成
    GENERATING = "generating"  # 生成中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    REVIEWING = "reviewing"  # 审核中
    APPROVED = "approved"  # 审核通过（可复用）
    PUBLISHED = "published"  # 已发布


class ReportType(str, PyEnum):
    """报告类型枚举"""
    DAILY = "daily"  # 日报
    WEEKLY = "weekly"  # 周报
    MONTHLY = "monthly"  # 月报
    SPECIAL = "special"  # 专题报告


class Report(BaseModel):
    """
    报告模型
    
    存储生成的报告主体信息
    """
    __tablename__ = "reports"
    
    # 唯一标识
    report_uuid: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        default=lambda: str(uuid4()),
        nullable=False,
        unique=True,
        comment="报告唯一标识（UUID）",
    )
    
    # 基本信息
    title: Mapped[str] = mapped_column(String(500), nullable=False, comment="报告标题")
    report_type: Mapped[str] = mapped_column(
        Enum(ReportType),
        nullable=False,
        default=ReportType.MONTHLY,
        comment="报告类型",
    )
    status: Mapped[str] = mapped_column(
        Enum(ReportStatus),
        nullable=False,
        default=ReportStatus.PENDING,
        comment="报告状态",
    )
    
    # 关联国家
    country_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("countries.id", ondelete="CASCADE"),
        nullable=False,
        comment="国家ID",
    )
    
    # 时间范围
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="报告起始时间")
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="报告结束时间")
    
    # 报告内容
    summary: Mapped[Optional[str]] = mapped_column(Text, comment="摘要") 
    key_findings = Column(JSON, nullable=False, default=list, comment="关键发现列表")
    recommendations = Column(JSON, nullable=False, default=list, comment="建议列表")
    
    # 生成信息
    generation_config = Column(JSON, nullable=False, default=dict, comment="生成配置")
    ai_model_used: Mapped[Optional[str]] = mapped_column(String(100), comment="使用的AI模型")
    generation_time: Mapped[Optional[float]] = mapped_column(comment="生成耗时（秒）")
    token_usage = Column(JSON, comment="Token使用统计")
    
    # 质量评估
    quality_score: Mapped[Optional[float]] = mapped_column(comment="质量分数（0-1）")
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), comment="审核人")
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), comment="审核时间")
    
    # 发布信息
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), comment="发布时间")
    published_url: Mapped[Optional[str]] = mapped_column(String(500), comment="发布URL")
    
    # 文件路径
    html_path: Mapped[Optional[str]] = mapped_column(String(500), comment="HTML文件路径")
    pdf_path: Mapped[Optional[str]] = mapped_column(String(500), comment="PDF文件路径")
    markdown_path: Mapped[Optional[str]] = mapped_column(String(500), comment="Markdown文件路径")
    
    # 元数据
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")
    error_message: Mapped[Optional[str]] = mapped_column(Text, comment="错误信息（如果失败）")
    
    # 关系
    country: Mapped["Country"] = relationship("Country", back_populates="reports")
    sections: Mapped[List["ReportSection"]] = relationship(
        "ReportSection",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportSection.section_order",
    )
    section_runs: Mapped[List["ReportSectionRun"]] = relationship(
        "ReportSectionRun",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportSectionRun.created_at",
    )
    
    # 索引
    __table_args__ = (
        Index("idx_report_country", "country_id"),
        Index("idx_report_status", "status"),
        Index("idx_report_type", "report_type"),
        Index("idx_report_period", "period_start", "period_end"),
        Index("idx_report_country_period", "country_id", "period_start", "period_end"),
    )
    
    def __repr__(self) -> str:
        return f"<Report(id={self.id}, title='{self.title}', status='{self.status}')>"


class ReportSection(BaseModel):
    """
    报告章节模型
    
    存储报告的各个章节内容
    """
    __tablename__ = "report_sections"
    
    # 关联报告
    report_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        comment="报告ID",
    )
    
    # 章节信息
    section_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="章节类型")
    section_order: Mapped[int] = mapped_column(Integer, nullable=False, comment="章节顺序")
    title: Mapped[str] = mapped_column(String(500), nullable=False, comment="章节标题")
    
    # 内容
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="章节内容（Markdown）")
    content_html: Mapped[Optional[str]] = mapped_column(Text, comment="章节内容（HTML）")
    
    # AI生成信息
    prompt_used: Mapped[Optional[str]] = mapped_column(Text, comment="使用的提示词")
    ai_model: Mapped[Optional[str]] = mapped_column(String(100), comment="使用的AI模型")
    generation_time: Mapped[Optional[float]] = mapped_column(comment="生成耗时（秒）")
    token_count: Mapped[Optional[int]] = mapped_column(Integer, comment="Token数量")
    
    # 数据依赖
    data_sources = Column(JSON, nullable=False, default=list, comment="数据来源列表")
    charts = Column(JSON, nullable=False, default=list, comment="图表配置")
    tables = Column(JSON, nullable=False, default=list, comment="表格数据")
    
    # 质量
    is_verified: Mapped[bool] = mapped_column(default=False, comment="是否已验证")
    verification_notes: Mapped[Optional[str]] = mapped_column(Text, comment="验证备注")
    
    # 元数据
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")
    
    # 关系
    report: Mapped["Report"] = relationship("Report", back_populates="sections")
    runs: Mapped[List["ReportSectionRun"]] = relationship(
        "ReportSectionRun",
        back_populates="section",
        cascade="all, delete-orphan",
    )
    
    # 索引
    __table_args__ = (
        Index("idx_section_report", "report_id"),
        Index("idx_section_order", "report_id", "section_order"),
        Index("idx_section_type", "section_type"),
    )
    
    def __repr__(self) -> str:
        return f"<ReportSection(id={self.id}, title='{self.title}', order={self.section_order})>"


class ReportSectionRunStatus(str, PyEnum):
    """报告章节运行状态"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReportSectionRun(BaseModel):
    """报告章节的运行记录（用于保存对话与质量分）"""
    __tablename__ = "report_section_runs"

    run_uuid: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        default=lambda: str(uuid4()),
        nullable=False,
        unique=True,
        comment="运行唯一标识"
    )
    report_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        comment="报告ID",
    )
    section_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("report_sections.id", ondelete="CASCADE"),
        nullable=True,
        comment="章节ID",
    )
    section_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="章节类型")
    disease_name: Mapped[Optional[str]] = mapped_column(String(200), comment="疾病名称")
    status: Mapped[str] = mapped_column(
        Enum(ReportSectionRunStatus),
        nullable=False,
        default=ReportSectionRunStatus.QUEUED,
        comment="运行状态",
    )
    provider: Mapped[Optional[str]] = mapped_column(String(100), comment="AI提供商")
    model: Mapped[Optional[str]] = mapped_column(String(100), comment="使用模型")
    temperature: Mapped[Optional[float]] = mapped_column(Float, comment="温度")
    max_tokens: Mapped[Optional[int]] = mapped_column(Integer, comment="最大tokens")
    token_usage = Column(JSON, nullable=False, default=dict, comment="token使用情况")
    quality_scores = Column(JSON, nullable=False, default=dict, comment="质量评分")
    revision_count: Mapped[int] = mapped_column(Integer, default=0, comment="Number of writer revisions triggered by reviewer")
    error_message: Mapped[Optional[str]] = mapped_column(Text, comment="错误信息")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), comment="开始时间")
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), comment="结束时间")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")

    report: Mapped["Report"] = relationship("Report", back_populates="section_runs")
    section: Mapped["ReportSection"] = relationship("ReportSection", back_populates="runs")
    conversations: Mapped[List["AIConversation"]] = relationship(
        "AIConversation",
        back_populates="run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_section_run_report", "report_id"),
        Index("idx_section_run_section", "section_id"),
        Index("idx_section_run_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<ReportSectionRun(id={self.id}, section_id={self.section_id}, status='{self.status}')>"


class AIConversation(BaseModel):
    """AI对话记录"""
    __tablename__ = "ai_conversations"

    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("report_section_runs.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属运行ID",
    )
    report_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        comment="报告ID",
    )
    section_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("report_sections.id", ondelete="CASCADE"),
        nullable=True,
        comment="章节ID",
    )
    agent: Mapped[str] = mapped_column(String(50), nullable=False, comment="代理类型")
    role: Mapped[Optional[str]] = mapped_column(String(50), comment="角色/阶段")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    prompt: Mapped[Optional[str]] = mapped_column(Text, comment="用户提示")
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, comment="系统提示")
    response: Mapped[Optional[str]] = mapped_column(Text, comment="模型回复")
    model: Mapped[Optional[str]] = mapped_column(String(100), comment="模型")
    provider: Mapped[Optional[str]] = mapped_column(String(100), comment="提供商")
    tokens = Column(JSON, nullable=False, default=dict, comment="token统计")
    duration: Mapped[Optional[float]] = mapped_column(Float, comment="耗时秒")
    temperature: Mapped[Optional[float]] = mapped_column(Float, comment="温度")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")

    run: Mapped["ReportSectionRun"] = relationship("ReportSectionRun", back_populates="conversations")
    section: Mapped["ReportSection"] = relationship("ReportSection")
    report: Mapped["Report"] = relationship("Report")

    __table_args__ = (
        Index("idx_ai_conv_run", "run_id"),
        Index("idx_ai_conv_section", "section_id"),
    )

    def __repr__(self) -> str:
        return f"<AIConversation(id={self.id}, run_id={self.run_id}, agent='{self.agent}')>"
