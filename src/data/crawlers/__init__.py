"""
GlobalID V2 Data Crawlers

Public exports for the crawlers sub-package.
"""
from .base import BaseCrawler, CrawlerResult
from .cn import ChinaCDCCrawler
from .jp import JapanIDWRCrawler
from .au import AustraliaNINDSSCrawler
from .us import USNNDSSCrawler
from .nz import NewZealandPHFCrawler
from .tw import TaiwanNIDSSCrawler
from .br import BrazilSINANCrawler
from .kr import KoreaKDCAOpenAPICrawler
from .hk import HongKongCHPCrawler

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
    "NewZealandPHFCrawler",
    "TaiwanNIDSSCrawler",
    "BrazilSINANCrawler",
    "KoreaKDCAOpenAPICrawler",
    "HongKongCHPCrawler",
]
