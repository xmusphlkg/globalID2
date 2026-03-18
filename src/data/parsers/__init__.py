"""
GlobalID V2 Data Parsers

Parse crawled data and extract structured information.
"""

from .base import BaseParser, ParseResult
from .html_parser import HTMLTableParser

__all__ = [
    "BaseParser",
    "ParseResult",
    "HTMLTableParser",
]
