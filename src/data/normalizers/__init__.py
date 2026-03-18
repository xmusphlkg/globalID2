"""
GlobalID V2 Data Normalizers

Disease name mapping and data normalisation.
"""

from .disease_mapper import DiseaseMapper
from .disease_mapper_db import DiseaseMapperDB, DiseaseMapperDBSync

__all__ = [
    "DiseaseMapper",       # CSV-backed mapper (backward-compat)
    "DiseaseMapperDB",     # async database mapper
    "DiseaseMapperDBSync", # sync wrapper (recommended)
]
