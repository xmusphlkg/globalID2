"""
GlobalID V2 Data Processors

Orchestrators that coordinate crawlers, parsers, and normalizers
to produce validated, database-ready DataFrames.
"""

from importlib import import_module

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
from .ca import CAOntarioMonthlyUpdater
from .fi import FIMonthlyUpdater
from .no import NOMonthlyUpdater
from .se import SEMonthlyUpdater

_is_processor = import_module(".is", __name__)
ISDataUpdater = _is_processor.ISDataUpdater
ISMonthlyUpdater = _is_processor.ISMonthlyUpdater
ISMultiFrequencyUpdater = _is_processor.ISMultiFrequencyUpdater

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
    "CAOntarioMonthlyUpdater",
    "FIMonthlyUpdater",
    "NOMonthlyUpdater",
    "SEMonthlyUpdater",
    "ISDataUpdater",
    "ISMonthlyUpdater",
    "ISMultiFrequencyUpdater",
]
