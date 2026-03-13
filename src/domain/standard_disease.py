"""
GlobalID V2 Standard Disease Model
"""
from typing import Optional

from sqlalchemy import JSON, Column, Index, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class StandardDisease(BaseModel):
    """
    标准疾病库模型
    
    存储标准化的疾病定义，作为系统内部的参考标准
    """
    __tablename__ = "standard_diseases"
    
    # 唯一标识符
    disease_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, comment="标准疾病ID (例如: INFLUENZA)")
    
    # 名称
    standard_name_en: Mapped[str] = mapped_column(String(200), nullable=False, comment="标准英文名称")
    standard_name_zh: Mapped[Optional[str]] = mapped_column(String(200), comment="标准中文名称")
    
    # 分类和编码
    category: Mapped[Optional[str]] = mapped_column(String(100), comment="疾病类别")
    icd_10: Mapped[Optional[str]] = mapped_column(String(20), comment="ICD-10编码")
    icd_11: Mapped[Optional[str]] = mapped_column(String(20), comment="ICD-11编码")
    
    # 描述
    description: Mapped[Optional[str]] = mapped_column(Text, comment="疾病描述")
    
    # 元数据
    source: Mapped[str] = mapped_column(String(100), default="Manual", comment="来源")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")
    is_active: Mapped[bool] = mapped_column(default=True, comment="是否启用")
    
    # 索引
    __table_args__ = (
        Index("idx_std_disease_id", "disease_id"),
        Index("idx_std_disease_name_en", "standard_name_en"),
        Index("idx_std_disease_category", "category"),
    )
    
    def __repr__(self) -> str:
        return f"<StandardDisease(id={self.disease_id}, name_en='{self.standard_name_en}')>"
