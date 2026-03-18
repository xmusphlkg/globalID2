"""
GlobalID V2 Base Parser

Abstract base class defining the common parser interface.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from src.core import get_logger

logger = get_logger(__name__)


@dataclass
class ParseResult:
    """Parse result dataclass: holds the extracted DataFrame and parse metadata."""

    # Basic identifiers
    source_url: str
    source_title: str
    parse_date: datetime = field(default_factory=datetime.now)

    # Extracted data
    data: Optional[pd.DataFrame] = None
    raw_content: Optional[str] = None

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Parse status
    success: bool = True
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "source_url": self.source_url,
            "source_title": self.source_title,
            "parse_date": self.parse_date.isoformat(),
            "data": self.data.to_dict() if self.data is not None else None,
            "metadata": self.metadata,
            "success": self.success,
            "error_message": self.error_message,
        }
    
    @property
    def has_data(self) -> bool:
        """True if the result contains a non-empty DataFrame."""
        return self.data is not None and not self.data.empty


class BaseParser(ABC):
    """
    Abstract base parser.

    Defines the common interface for all parsers.
    """
    
    def __init__(self):
        """Initialise parser with a module-scoped logger."""
        self.logger = get_logger(self.__class__.__name__)
    
    @abstractmethod
    def parse(self, content: str, **kwargs) -> ParseResult:
        """
        Parse raw content into a :class:`ParseResult`.

        Args:
            content:  Raw content string (HTML, CSV, etc.).
            **kwargs: Extra parameters (url, title, date, year_month, …).

        Returns:
            :class:`ParseResult`
        """
        pass

    @abstractmethod
    def validate(self, data: pd.DataFrame) -> bool:
        """
        Validate a parsed DataFrame.

        Args:
            data: Parsed DataFrame to validate.

        Returns:
            True if the data meets minimum quality requirements.
        """
        pass
    
    def _is_column_meaningful(self, column: pd.Series, threshold: float = 0.1) -> bool:
        """
        Check whether a column contains meaningful (non-trivial) data.

        Args:
            column:    pandas Series to inspect.
            threshold: Minimum fraction of non-empty rows required.

        Returns:
            True if the column passes the threshold.
        """
        if len(column) == 0:
            return False

        # Fraction of non-null, non-empty-string values
        non_empty = column.replace("", pd.NA).notna().sum()
        ratio = non_empty / len(column)

        return ratio > threshold
    
    def _clean_text(self, text: str) -> str:
        """
        Clean a text string by collapsing internal whitespace.

        Args:
            text: Raw text.

        Returns:
            Cleaned text with leading/trailing whitespace removed.
        """
        if not isinstance(text, str):
            return str(text) if text is not None else ""

        # Collapse whitespace
        text = " ".join(text.split())

        return text.strip()
