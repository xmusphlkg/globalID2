"""
GlobalID V2 Data Storage Layer

Persistence layer: writes normalised DataFrames to the database.
Storage logic is separated from DataProcessor (Single Responsibility Principle).
"""

from .record_store import RecordStore

__all__ = ["RecordStore"]
