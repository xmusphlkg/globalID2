"""Report v4 generation pipeline.

The v4 pipeline keeps the canonical report document locale-first and evidence
bound. Legacy report layouts are intentionally not exported from this package.
"""

from .models import METHOD_VERSION, SCHEMA_VERSION
from .pipeline import ReportV4Context, ReportV4Pipeline

__all__ = ["METHOD_VERSION", "SCHEMA_VERSION", "ReportV4Context", "ReportV4Pipeline"]
