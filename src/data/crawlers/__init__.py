"""
GlobalID V2 Data Crawlers

数据爬取器导出
"""
from .base import BaseCrawler, CrawlerResult
from .cn_cdc import ChinaCDCCrawler

__all__ = [
    "BaseCrawler",
    "CrawlerResult",
    "ChinaCDCCrawler",
]
