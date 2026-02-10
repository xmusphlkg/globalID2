"""
GlobalID V2 Base Crawler

基础爬虫类，定义通用的爬取接口和功能
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
    """爬取结果数据类"""
    
    title: str
    url: Optional[str] = None
    content: Optional[str] = None
    date: Optional[datetime] = None
    year_month: Optional[str] = None  # "2024 January" 格式
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
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
    基础爬虫类
    
    提供通用的HTTP请求、重试机制、错误处理等功能
    """
    
    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3,
        delay: float = 1.0,
    ):
        """
        初始化爬虫
        
        Args:
            user_agent: User-Agent字符串
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
            delay: 请求延迟（秒）
        """
        self.config = get_config()
        self.timeout = timeout
        self.delay = delay
        
        # 配置Session
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or "Mozilla/5.0 (compatible; GlobalID/2.0)",
        })
        
        # 配置重试策略
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        logger.info(f"{self.__class__.__name__} initialized")
    
    def get(self, url: str, **kwargs) -> requests.Response:
        """
        发送GET请求
        
        Args:
            url: 请求URL
            **kwargs: 额外的请求参数
            
        Returns:
            Response对象
        """
        time.sleep(self.delay)
        
        try:
            response = self.session.get(url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            logger.debug(f"GET {url} - Status: {response.status_code}")
            return response
        except requests.RequestException as e:
            logger.error(f"GET request failed for {url}: {e}")
            raise
    
    def post(self, url: str, **kwargs) -> requests.Response:
        """
        发送POST请求
        
        Args:
            url: 请求URL
            **kwargs: 额外的请求参数
            
        Returns:
            Response对象
        """
        time.sleep(self.delay)
        
        try:
            response = self.session.post(url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            logger.debug(f"POST {url} - Status: {response.status_code}")
            return response
        except requests.RequestException as e:
            logger.error(f"POST request failed for {url}: {e}")
            raise
    
    @abstractmethod
    async def crawl(self, **kwargs) -> List[CrawlerResult]:
        """
        执行爬取任务（需要子类实现）
        
        Args:
            **kwargs: 爬取参数
            
        Returns:
            爬取结果列表
        """
        pass
    
    @abstractmethod
    def parse(self, response: requests.Response) -> List[CrawlerResult]:
        """
        解析响应内容（需要子类实现）
        
        Args:
            response: HTTP响应对象
            
        Returns:
            解析后的结果列表
        """
        pass
    
    def __enter__(self):
        """上下文管理器进入"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        self.session.close()
