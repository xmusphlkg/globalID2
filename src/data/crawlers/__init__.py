"""
GlobalID V2 Data Crawlers

数据爬取器导出
"""
from .base import BaseCrawler, CrawlerResult
from .cn_cdc import ChinaCDCCrawler
from .jp_idwr import JapanIDWRCrawler
from .au_nindss import AustraliaNINDSSCrawler

__all__ = [
    "BaseCrawler",
    "CrawlerResult",
    "ChinaCDCCrawler",
    "JapanIDWRCrawler",
    "AustraliaNINDSSCrawler",
]
