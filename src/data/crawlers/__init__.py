"""
GlobalID V2 Data Crawlers

Public exports for the crawlers sub-package.
"""
from .base import BaseCrawler, CrawlerResult
from .cn import ChinaCDCCrawler
from .jp import JapanIDWRCrawler
from .au import AustraliaNINDSSCrawler
from .us import USNNDSSCrawler

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
]
