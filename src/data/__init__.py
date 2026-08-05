"""
GlobalID V2 Data Pipeline

Crawl → Parse → Normalise → Store

Sub-packages:
  crawlers/    - HTTP crawlers (BaseCrawler, ChinaCDCCrawler, JapanIDWRCrawler, …)
  parsers/     - Content parsers (HTMLTableParser, …)
  normalizers/ - Disease name normalisation (DiseaseMapper, DiseaseMapperDB, …)
  processors/  - End-to-end processors (DataProcessor, …)
  storage/     - Persistence layer (RecordStore)
"""
from .crawlers.base import BaseCrawler, CrawlerResult
from .crawlers.cn import ChinaCDCCrawler
from .crawlers.jp import JapanIDWRCrawler
from .crawlers.au import AustraliaNINDSSCrawler
from .crawlers.us import USNHSSHIVCrawler, USNNDSSCrawler
from .crawlers.br import BrazilSINANCrawler
from .crawlers.kr import KoreaKDCAOpenAPICrawler
from .parsers.html_parser import HTMLTableParser
from .normalizers.disease_mapper import DiseaseMapper
from .normalizers.disease_mapper_db import DiseaseMapperDB, DiseaseMapperDBSync
from .processors.cn import DataProcessor
from .storage.record_store import RecordStore

# Backward-compatible alias
JPIDWRCrawler = JapanIDWRCrawler

__all__ = [
    # Crawlers
    "BaseCrawler",
    "CrawlerResult",
    "ChinaCDCCrawler",
    "JapanIDWRCrawler",
    "JPIDWRCrawler",
    "AustraliaNINDSSCrawler",
    "USNNDSSCrawler",
    "USNHSSHIVCrawler",
    "BrazilSINANCrawler",
    "KoreaKDCAOpenAPICrawler",
    # Parsers
    "HTMLTableParser",
    # Normalizers
    "DiseaseMapper",
    "DiseaseMapperDB",
    "DiseaseMapperDBSync",
    # Processors
    "DataProcessor",
    # Storage
    "RecordStore",
]
