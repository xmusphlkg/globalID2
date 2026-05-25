"""
GlobalID V2 Data Processors

Orchestrators that coordinate crawlers, parsers, and normalizers
to produce validated, database-ready DataFrames.
"""

from .cn import DataProcessor
from .jp import JPWeeklyUpdater
from .us import USWeeklyUpdater
from .au import AUMonthlyUpdater, AUWeeklyUpdater
from .nz import NZMonthlyUpdater
from .tw import TWMonthlyUpdater
from .br import BRMonthlyUpdater
from .kr import KRMonthlyUpdater
from .hk import HKMonthlyUpdater
from .ch import CHMonthlyUpdater

__all__ = [
    "DataProcessor",
    "JPWeeklyUpdater",
    "USWeeklyUpdater",
    "AUMonthlyUpdater",
    "AUWeeklyUpdater",
    "NZMonthlyUpdater",
    "TWMonthlyUpdater",
    "BRMonthlyUpdater",
    "KRMonthlyUpdater",
    "HKMonthlyUpdater",
    "CHMonthlyUpdater",
]
