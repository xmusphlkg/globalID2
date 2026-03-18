"""
GlobalID V2 Base Crawler

Abstract base class that defines the common crawl interface and shared HTTP functionality.
"""
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.core import get_config, get_logger

logger = get_logger(__name__)


@dataclass
class CrawlerResult:
    """Crawl result dataclass: holds all metadata and content for one fetched report."""
    
    title: str
    url: Optional[str] = None
    content: Optional[str] = None
    date: Optional[datetime] = None
    year_month: Optional[str] = None  # canonical format: "2024 January"
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "date": self.date.isoformat() if self.date else None,
            "year_month": self.year_month,
            "metadata": self.metadata,
            "raw_data": self.raw_data,
        }


class BaseCrawler(ABC):
    """
    Abstract base crawler.

    Provides shared HTTP session, retry strategy, rate-limiting delay,
    and error handling. Subclasses must implement `crawl()` and `parse()`.
    """
    
    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3,
        delay: float = 1.0,
    ):
        """
        Initialise the crawler.

        Args:
            user_agent: Custom User-Agent string.
            timeout:    Request timeout in seconds.
            max_retries: Maximum number of retries per request.
            delay:      Fixed delay between requests (seconds).
        """
        self.config = get_config()
        self.timeout = timeout
        self.delay = delay

        # Configure HTTP session
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or "Mozilla/5.0 (compatible; GlobalID/2.0)",
        })

        # Configure retry strategy
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        logger.debug(f"[{self.__class__.__name__}] Crawler ready | timeout={timeout}s retries={max_retries} delay={delay}s")
    
    def get(self, url: str, **kwargs) -> requests.Response:
        """
        Send a GET request with rate-limiting delay and automatic retries.

        Args:
            url:      Target URL.
            **kwargs: Extra arguments forwarded to ``requests.Session.get``.

        Returns:
            ``requests.Response`` object.
        """
        time.sleep(self.delay)
        
        try:
            response = self.session.get(url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            logger.debug(f"GET {url} - Status: {response.status_code}")
            return response
        except requests.RequestException as e:
            logger.error(f"[{self.__class__.__name__}] GET failed | url={url} error={e}")
            raise

    def post(self, url: str, **kwargs) -> requests.Response:
        """
        Send a POST request with rate-limiting delay and automatic retries.

        Args:
            url:      Target URL.
            **kwargs: Extra arguments forwarded to ``requests.Session.post``.

        Returns:
            ``requests.Response`` object.
        """
        time.sleep(self.delay)
        
        try:
            response = self.session.post(url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            logger.debug(f"POST {url} - Status: {response.status_code}")
            return response
        except requests.RequestException as e:
            logger.error(f"[{self.__class__.__name__}] POST failed | url={url} error={e}")
            raise

    @abstractmethod
    async def crawl(self, **kwargs) -> List[CrawlerResult]:
        """
        Execute the full crawl pipeline (must be implemented by subclasses).

        Args:
            **kwargs: Crawl parameters (source, force, fill_missing, …).

        Returns:
            List of :class:`CrawlerResult` objects.
        """
        pass

    @abstractmethod
    def parse(self, response: requests.Response) -> List[CrawlerResult]:
        """
        Parse an HTTP response into structured results (must be implemented by subclasses).

        Args:
            response: ``requests.Response`` object.

        Returns:
            List of :class:`CrawlerResult` objects.
        """
        pass
    
    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit: close the HTTP session."""
        self.session.close()
