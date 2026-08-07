"""
GlobalID V2 Data Storage Layer

Persistence layer: writes normalised DataFrames to the database.
Storage logic is separated from DataProcessor (Single Responsibility Principle).
"""

from .record_store import RecordStore
from .series_observation_store import (
    RegistryRowSelection,
    SeriesObservationQualityError,
    SeriesObservationQualityIssue,
    SeriesObservationQualityPolicy,
    SeriesObservationQualityReport,
    SeriesObservationQuarantinedError,
    SeriesObservationSaveResult,
    SeriesObservationStore,
)

__all__ = [
    "RecordStore",
    "RegistryRowSelection",
    "SeriesObservationQualityError",
    "SeriesObservationQualityIssue",
    "SeriesObservationQualityPolicy",
    "SeriesObservationQualityReport",
    "SeriesObservationQuarantinedError",
    "SeriesObservationSaveResult",
    "SeriesObservationStore",
]
