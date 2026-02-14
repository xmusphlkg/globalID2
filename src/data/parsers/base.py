"""
GlobalID V2 Base Parser

基础解析器类，定义通用的解析接口
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
    """解析结果数据类"""
    
    # 基本信息
    source_url: str
    source_title: str
    parse_date: datetime = field(default_factory=datetime.now)
    
    # 解析出的数据
    data: Optional[pd.DataFrame] = None
    raw_content: Optional[str] = None
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # 解析状态
    success: bool = True
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
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
        """是否包含有效数据"""
        return self.data is not None and not self.data.empty


class BaseParser(ABC):
    """
    基础解析器类
    
    定义解析器的通用接口和功能
    """
    
    def __init__(self):
        """初始化解析器"""
        self.logger = get_logger(self.__class__.__name__)
    
    @abstractmethod
    def parse(self, content: str, **kwargs) -> ParseResult:
        """
        解析内容
        
        Args:
            content: 待解析的内容（HTML、PDF等）
            **kwargs: 额外参数
            
        Returns:
            ParseResult: 解析结果
        """
        pass
    
    @abstractmethod
    def validate(self, data: pd.DataFrame) -> bool:
        """
        验证解析结果
        
        Args:
            data: 解析得到的数据
            
        Returns:
            bool: 是否有效
        """
        pass
    
    def _is_column_meaningful(self, column: pd.Series, threshold: float = 0.1) -> bool:
        """
        检查列是否包含有意义的数据
        
        Args:
            column: pandas Series
            threshold: 非空行比例阈值
            
        Returns:
            bool: 是否有意义
        """
        if len(column) == 0:
            return False
        
        # 计算非空非空字符串的比例
        non_empty = column.replace("", pd.NA).notna().sum()
        ratio = non_empty / len(column)
        
        return ratio > threshold
    
    def _clean_text(self, text: str) -> str:
        """
        清理文本
        
        Args:
            text: 原始文本
            
        Returns:
            str: 清理后的文本
        """
        if not isinstance(text, str):
            return str(text) if text is not None else ""
        
        # 去除多余空白
        text = " ".join(text.split())
        
        return text.strip()
