"""
GlobalID V2 Data Parsers

解析爬取的数据，提取结构化信息
"""

from .base import BaseParser, ParseResult
from .html_parser import HTMLTableParser

__all__ = [
    "BaseParser",
    "ParseResult",
    "HTMLTableParser",
]
