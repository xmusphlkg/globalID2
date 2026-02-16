"""
GlobalID V2 Crawl Audit Models

爬取运行记录与原始页面索引
"""
from datetime import datetime
from typing import Optional, List

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class CrawlRun(BaseModel):
    """爬取运行记录"""

    __tablename__ = "crawl_runs"

    country_code: Mapped[str] = mapped_column(String(10), nullable=False, comment="国家代码")
    source: Mapped[str] = mapped_column(String(50), nullable=False, comment="数据源")
    status: Mapped[str] = mapped_column(String(20), nullable=False, comment="状态")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="开始时间")
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="结束时间")
    new_reports: Mapped[Optional[int]] = mapped_column(Integer, comment="新报告数")
    processed_reports: Mapped[Optional[int]] = mapped_column(Integer, comment="处理报告数")
    total_records: Mapped[Optional[int]] = mapped_column(Integer, comment="总记录数")
    raw_dir: Mapped[Optional[str]] = mapped_column(String(500), comment="原始文件目录")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")
    error_message: Mapped[Optional[str]] = mapped_column(Text, comment="错误信息")

    raw_pages: Mapped[List["CrawlRawPage"]] = relationship(
        "CrawlRawPage",
        back_populates="run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_crawl_run_country", "country_code"),
        Index("idx_crawl_run_status", "status"),
        Index("idx_crawl_run_started_at", "started_at"),
    )


class CrawlRawPage(BaseModel):
    """原始页面索引（文件路径为主）"""

    __tablename__ = "crawl_raw_pages"

    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("crawl_runs.id", ondelete="CASCADE"),
        nullable=False,
        comment="运行ID",
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False, comment="页面URL")
    title: Mapped[Optional[str]] = mapped_column(String(500), comment="页面标题")
    content_path: Mapped[str] = mapped_column(String(500), nullable=False, comment="文本文件路径")
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), comment="内容哈希")
    content_type: Mapped[Optional[str]] = mapped_column(String(50), comment="内容类型")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="抓取时间")
    source: Mapped[Optional[str]] = mapped_column(String(50), comment="数据源")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")

    run: Mapped["CrawlRun"] = relationship("CrawlRun", back_populates="raw_pages")

    __table_args__ = (
        UniqueConstraint("run_id", "url", name="uq_crawl_raw_pages_run_url"),
        Index("idx_crawl_raw_page_run", "run_id"),
        Index("idx_crawl_raw_page_url", "url"),
        Index("idx_crawl_raw_page_hash", "content_hash"),
    )