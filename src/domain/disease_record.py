"""
GlobalID V2 Disease Record Model

疾病记录模型：存储时间序列的疾病数据（TimescaleDB hypertable）
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class DiseaseRecord(Base):
    """
    疾病记录模型（时间序列数据）
    
使用 TimescaleDB hypertable 存储疾病监测数据
    """
    __tablename__ = "disease_records"
    
    # 主键（时间+疾病+国家的组合）
    time: Mapped[datetime] = mapped_column(DateTime, primary_key=True, comment="记录时间")
    disease_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("diseases.id", ondelete="CASCADE"),
        primary_key=True,
        comment="疾病ID",
    )
    country_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("countries.id", ondelete="CASCADE"),
        primary_key=True,
        comment="国家ID",
    )
    
    # 统计数据
    cases: Mapped[Optional[int]] = mapped_column(Integer, comment="病例数")
    deaths: Mapped[Optional[int]] = mapped_column(Integer, comment="死亡数")
    recoveries: Mapped[Optional[int]] = mapped_column(Integer, comment="康复数")
    active_cases: Mapped[Optional[int]] = mapped_column(Integer, comment="现存病例数")
    
    # 增量数据
    new_cases: Mapped[Optional[int]] = mapped_column(Integer, comment="新增病例")
    new_deaths: Mapped[Optional[int]] = mapped_column(Integer, comment="新增死亡")
    new_recoveries: Mapped[Optional[int]] = mapped_column(Integer, comment="新增康复")
    
    # 率数据
    incidence_rate: Mapped[Optional[float]] = mapped_column(Float, comment="发病率（每10万人）")
    mortality_rate: Mapped[Optional[float]] = mapped_column(Float, comment="死亡率（%）")
    recovery_rate: Mapped[Optional[float]] = mapped_column(Float, comment="康复率（%）")
    
    # 地理信息
    region: Mapped[Optional[str]] = mapped_column(String(100), comment="地区/省份")
    city: Mapped[Optional[str]] = mapped_column(String(100), comment="城市")
    
    # 数据质量
    data_source: Mapped[Optional[str]] = mapped_column(String(200), comment="数据来源")
    data_quality: Mapped[Optional[str]] = mapped_column(String(20), comment="数据质量（high/medium/low）")
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, comment="置信度分数（0-1）")
    
    # 元数据
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")
    raw_data = Column(JSON, comment="原始数据（用于追溯）")
    
    # 关系
    disease: Mapped["Disease"] = relationship("Disease", back_populates="records")
    country: Mapped["Country"] = relationship("Country", back_populates="records")
    
    # 索引（TimescaleDB 会自动在 time 列创造分区索引）
    __table_args__ = (
        Index("idx_record_time", "time"),
        Index("idx_record_disease", "disease_id"),
        Index("idx_record_country", "country_id"),
        Index("idx_record_region", "region"),
        Index("idx_record_time_disease_country", "time", "disease_id", "country_id"),
    )
    
    def __repr__(self) -> str:
        return (
            f"<DiseaseRecord(time={self.time}, disease_id={self.disease_id}, "
            f"country_id={self.country_id}, cases={self.cases})>"
        )
