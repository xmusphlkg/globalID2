"""
GlobalID V2 Country Model

国家/地区模型：存储国家配置信息
"""
from typing import Optional

from sqlalchemy import Boolean, Column, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class Country(BaseModel):
    """
    国家/地区模型
    
    存储国家基本信息、数据源配置和爬取规则
    """
    __tablename__ = "countries"
    
    # 基本信息
    code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True, comment="国家代码（ISO 3166）")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="国家名称")
    name_en: Mapped[str] = mapped_column(String(100), nullable=False, comment="英文名称")
    name_local: Mapped[Optional[str]] = mapped_column(String(100), comment="当地语言名称")
    
    # 语言和时区
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en", comment="主要语言代码")
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC", comment="时区")
    
    # 数据源配置
    data_source_url: Mapped[Optional[str]] = mapped_column(String(500), comment="数据源URL")
    data_source_type: Mapped[Optional[str]] = mapped_column(String(50), comment="数据源类型（api/web/ftp）")
    api_key: Mapped[Optional[str]] = mapped_column(Text, comment="API密钥（加密存储）")
    
    # 爬取配置
    crawler_config = Column(JSON, nullable=False, default=dict, comment="爬虫配置")
    parser_config = Column(JSON, nullable=False, default=dict, comment="解析器配置")
    
    # 疾病映射规则
    disease_mapping_rules = Column(JSON, nullable=False, default=dict, comment="疾病名称映射规则")
    
    # 报告配置
    report_config = Column(JSON, nullable=False, default=dict, comment="报告生成配置")
    
    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    last_crawl_time: Mapped[Optional[str]] = mapped_column(String(50), comment="最后爬取时间")
    
    # 元数据
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")
    notes: Mapped[Optional[str]] = mapped_column(Text, comment="备注")
    
    # 关系
    records: Mapped[list] = relationship(
        "DiseaseRecord",
        back_populates="country",
        cascade="all, delete-orphan",
    )
    reports: Mapped[list] = relationship(
        "Report",
        back_populates="country",
        cascade="all, delete-orphan",
    )
    
    # 索引
    __table_args__ = (
        Index("idx_country_code", "code"),
        Index("idx_country_active", "is_active"),
    )
    
    def __repr__(self) -> str:
        return f"<Country(id={self.id}, code='{self.code}', name='{self.name}')>"
