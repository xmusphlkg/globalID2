"""
GlobalID V2 Data Normalizers

数据标准化模块，负责疾病名称映射、地理位置标准化等
"""

from .disease_mapper import DiseaseMapper
from .disease_mapper_db import DiseaseMapperDB, DiseaseMapperDBSync

__all__ = [
    "DiseaseMapper",  # 保留旧版CSV映射器用于向后兼容
    "DiseaseMapperDB",  # 异步数据库映射器
    "DiseaseMapperDBSync",  # 同步数据库映射器（推荐）
]
