"""
GlobalID V2 Disease Mapping Model
"""
from typing import Optional

from sqlalchemy import JSON, Column, Index, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class DiseaseMapping(BaseModel):
    """
    疾病名称映射模型
    
    用于将不同来源、不同语言的疾病名称映射到系统内部的标准疾病ID或名称
    """
    __tablename__ = "disease_mappings"
    
    # 映射目标（指向 diseases 表的 name，或 standard_diseases 的 disease_id）
    # 目前主要映射到 diseases.name
    disease_id: Mapped[str] = mapped_column(String(200), nullable=False, comment="目标疾病ID/名称")
    
    # 映射源
    country_code: Mapped[str] = mapped_column(String(10), nullable=False, comment="国家代码 (例如: CN, US)")
    local_name: Mapped[str] = mapped_column(String(500), nullable=False, comment="本地名称/别名")
    
    # 映射属性
    is_primary: Mapped[bool] = mapped_column(default=False, comment="是否为主要名称")
    is_alias: Mapped[bool] = mapped_column(default=False, comment="是否为别名")
    priority: Mapped[int] = mapped_column(Integer, default=0, comment="优先级 (越高越优先)")
    usage_count: Mapped[int] = mapped_column(Integer, default=0, comment="使用次数")
    confidence_score: Mapped[float] = mapped_column(default=1.0, comment="置信度分数")
    
    # 分类和来源
    category: Mapped[Optional[str]] = mapped_column(String(100), comment="疾病类别 (用于分类映射)")
    source: Mapped[Optional[str]] = mapped_column(String(100), comment="来源 (例如: Manual, Auto, PubMed)")
    
    # 元数据
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")
    is_active: Mapped[bool] = mapped_column(default=True, comment="是否启用")
    
    # 索引
    __table_args__ = (
        Index("idx_mapping_lookup", "country_code", "local_name"),
        Index("idx_mapping_target", "disease_id"),
        Index("idx_mapping_active", "is_active"),
        # 复合唯一索引，确保同一个国家同一个本地名称只映射一次（或者需要允许一对多？通常是一对一映射到标准名）
        Index("idx_mapping_unique", "disease_id", "country_code", "local_name", unique=True),
    )
    
    def __repr__(self) -> str:
        return f"<DiseaseMapping(country='{self.country_code}', local='{self.local_name}' -> '{self.disease_id}')>"
