"""
GlobalID V2 Disease Model

疾病模型：存储疾病信息和语义向量
"""
from typing import List, Optional

from sqlalchemy import JSON, Column, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
try:
    from pgvector.sqlalchemy import Vector  # optional, may not be available in DB image
except Exception:
    Vector = None

from .base import BaseModel


class Disease(BaseModel):
    """
    疾病模型
    
    存储疾病的基本信息、别名、类别以及用于语义搜索的向量表示
    """
    __tablename__ = "diseases"
    
    # 基本信息
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, comment="疾病名称")
    name_en: Mapped[Optional[str]] = mapped_column(String(200), comment="英文名称")
    category: Mapped[str] = mapped_column(String(100), nullable=False, comment="疾病类别")
    
    # 疾病编码
    icd_10: Mapped[Optional[str]] = mapped_column(String(10), comment="ICD-10编码")
    icd_11: Mapped[Optional[str]] = mapped_column(String(20), comment="ICD-11编码")
    
    # 别名和关键词
    aliases = Column(JSON, nullable=False, default=list, comment="疾病别名列表")
    keywords = Column(JSON, nullable=False, default=list, comment="关键词列表")
    
    # 描述信息
    description: Mapped[Optional[str]] = mapped_column(Text, comment="疾病描述")
    symptoms: Mapped[Optional[str]] = mapped_column(Text, comment="主要症状")
    transmission: Mapped[Optional[str]] = mapped_column(Text, comment="传播途径")
    
    # 语义向量（用于AI匹配）
    # 临时回退为 JSON 存储 embedding 向量列表，以避免在数据库镜像未安装 pgvector 时导致 schema 创建失败。
    embedding = Column(JSON, nullable=True, comment="OpenAI embedding（列表/JSON）")
    
    # 元数据
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")
    is_active: Mapped[bool] = mapped_column(default=True, comment="是否启用")
    
    # 关系
    records: Mapped[List["DiseaseRecord"]] = relationship(
        "DiseaseRecord",
        back_populates="disease",
        cascade="all, delete-orphan",
    )
    
    # 索引
    __table_args__ = (
        Index("idx_disease_name", "name"),
        Index("idx_disease_category", "category"),
        Index("idx_disease_icd10", "icd_10"),
        Index("idx_disease_active", "is_active"),
    )
    
    def __repr__(self) -> str:
        return f"<Disease(id={self.id}, name='{self.name}', category='{self.category}')>"
