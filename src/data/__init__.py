"""
GlobalID V2 Data Crawlers

数据爬取模块
"""
from .crawlers.base import BaseCrawler, CrawlerResult
from .crawlers.cn_cdc import ChinaCDCCrawler

__all__ = [
    "BaseCrawler",
    "CrawlerResult",
    "ChinaCDCCrawler",
]
