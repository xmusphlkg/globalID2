"""
GlobalID V2 Data Crawlers

Public exports for the crawlers sub-package.
"""
from importlib import import_module

from .base import BaseCrawler, CrawlerResult
from .cn import ChinaCDCCrawler
from .jp import JapanIDWRCrawler
from .au import AustraliaNINDSSCrawler
from .us import USNHSSHIVCrawler, USNNDSSCrawler
from .nz import NewZealandPHFCrawler
from .tw import TaiwanNIDSSCrawler
from .br import BrazilSINANCrawler
from .kr import KoreaKDCAOpenAPICrawler
from .hk import HongKongCHPCrawler
from .ch import SwitzerlandIDDCrawler
from .ca import CanadaOntarioPHOCrawler
from .fi import FinlandTHLCrawler
from .ie import IrelandHPSCWeeklyCrawler
from .ie_annual import IrelandHPSCAnnualCrawler
from .ie_weekly_archive import IrelandHPSCWeeklyArchiveCrawler
from .no import NorwayMSISCrawler
from .se import SwedenSmiNetCrawler
from .at import AustriaAGESRadarCrawler
from .de import GermanySurvStatCrawler

# ``is`` is Iceland's ISO code and a Python keyword, so this one module must be
# loaded dynamically rather than through a ``from .is import ...`` statement.
IcelandDOHCrawler = import_module(".is", __name__).IcelandDOHCrawler

# Backward-compatible aliases
JPIDWRCrawler = JapanIDWRCrawler

__all__ = [
    "BaseCrawler",
    "CrawlerResult",
    "ChinaCDCCrawler",
    "JapanIDWRCrawler",
    "JPIDWRCrawler",
    "AustraliaNINDSSCrawler",
    "USNNDSSCrawler",
    "USNHSSHIVCrawler",
    "NewZealandPHFCrawler",
    "TaiwanNIDSSCrawler",
    "BrazilSINANCrawler",
    "KoreaKDCAOpenAPICrawler",
    "HongKongCHPCrawler",
    "SwitzerlandIDDCrawler",
    "CanadaOntarioPHOCrawler",
    "FinlandTHLCrawler",
    "IrelandHPSCWeeklyCrawler",
    "IrelandHPSCAnnualCrawler",
    "IrelandHPSCWeeklyArchiveCrawler",
    "NorwayMSISCrawler",
    "SwedenSmiNetCrawler",
    "AustriaAGESRadarCrawler",
    "GermanySurvStatCrawler",
    "IcelandDOHCrawler",
]
