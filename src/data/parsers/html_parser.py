"""
GlobalID V2 HTML Table Parser

解析HTML页面中的表格数据，支持中英文格式
"""
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.core import get_logger
from .base import BaseParser, ParseResult

logger = get_logger(__name__)


class HTMLTableParser(BaseParser):
    """
    HTML表格解析器
    
    用于解析中国CDC和政府网站的传染病数据表格
    支持两种格式：
    1. 英文格式：Diseases, Cases, Deaths
    2. 中文格式：疾病名称, 病例数, 死亡数
    """
    
    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        })
    
    def parse(self, content: str, **kwargs) -> ParseResult:
        """
        解析HTML内容，提取表格数据
        
        Args:
            content: HTML内容或URL
            **kwargs: 额外参数
                - url: 源URL
                - title: 页面标题
                - date: 报告日期
                - year_month: 年月字符串
                - source: 数据源
                - language: 语言（'en' 或 'zh'）
                
        Returns:
            ParseResult: 解析结果
        """
        url = kwargs.get("url", "")
        title = kwargs.get("title", "")
        language = kwargs.get("language", "en")
        
        try:
            # 如果content是URL，则获取内容
            if content.startswith("http"):
                self.logger.debug(f"Fetching from URL: {content}")
                response = self.session.get(content, timeout=30)
                response.raise_for_status()
                html_content = response.text
                url = content
            else:
                html_content = content
            
            # 解析HTML
            soup = BeautifulSoup(html_content, "html.parser")
            
            # 提取表格
            tables = soup.find_all("table")
            if not tables:
                return ParseResult(
                    source_url=url,
                    source_title=title,
                    success=False,
                    error_message="未找到表格",
                    metadata=kwargs,
                )
            
            # 提取第一个表格（通常是数据表格）
            table = tables[0]
            self.logger.debug(f"Found {len(tables)} table(s), using first one")
            df = self._extract_table_data(table)
            self.logger.debug(f"Extracted DataFrame shape: {df.shape}")
            
            if df.empty:
                return ParseResult(
                    source_url=url,
                    source_title=title,
                    success=False,
                    error_message="表格为空",
                    metadata=kwargs,
                )
            
            # 清洗数据
            if language == "zh":
                cleaned_df = self._clean_chinese_data(df, kwargs)
            else:
                cleaned_df = self._clean_english_data(df, kwargs)
            
            # 验证数据
            if not self.validate(cleaned_df):
                return ParseResult(
                    source_url=url,
                    source_title=title,
                    data=cleaned_df,
                    success=False,
                    error_message="数据验证失败",
                    metadata=kwargs,
                )
            
            return ParseResult(
                source_url=url,
                source_title=title,
                data=cleaned_df,
                raw_content=html_content,
                success=True,
                metadata=kwargs,
            )
            
        except Exception as e:
            self.logger.error(f"解析失败: {e}")
            return ParseResult(
                source_url=url,
                source_title=title,
                success=False,
                error_message=str(e),
                metadata=kwargs,
            )
    
    def _extract_table_data(self, table) -> pd.DataFrame:
        """
        从BeautifulSoup table对象中提取数据
        
        Args:
            table: BeautifulSoup table对象
            
        Returns:
            pd.DataFrame: 表格数据
        """
        data = []
        
        # 只提取表体数据，忽略表头和表尾
        tbody = table.find("tbody")
        if tbody:
            rows = tbody.find_all("tr")
            for tr in rows:
                cells = tr.find_all("td")
                if cells:
                    row = []
                    for td in cells:
                        # 移除上标标签（通常是注释标记）
                        for sup in td.find_all("sup"):
                            sup.decompose()
                        text = td.get_text(strip=True)
                        row.append(text)
                    # 只保留有3列的行（跳过注释行）
                    if len(row) == 3:
                        data.append(row)
        
        if not data:
            return pd.DataFrame()
        
        # 创建DataFrame
        df = pd.DataFrame(data)
        self.logger.debug(f"Created DataFrame shape: {df.shape}")
        
        # 确保只有3列，如果不是则截取前3列
        if len(df.columns) > 3:
            self.logger.debug(f"Truncating to 3 columns from {len(df.columns)}")
            df = df.iloc[:, :3]
        elif len(df.columns) < 3:
            self.logger.warning(f"Insufficient columns: {len(df.columns)}")
            return pd.DataFrame()
        
        # 设置列名
        df.columns = ["Diseases", "Cases", "Deaths"]
        self.logger.debug(f"Final DataFrame shape: {df.shape}")
        
        return df
    
    def _clean_english_data(self, df: pd.DataFrame, metadata: Dict) -> pd.DataFrame:
        """
        清洗英文格式的数据
        
        Args:
            df: 原始数据
            metadata: 元数据（包含date, url等）
            
        Returns:
            pd.DataFrame: 清洗后的数据
        """
        self.logger.debug(f"_clean_english_data input DataFrame shape: {df.shape}")
        
        # 复制数据，避免修改原始数据
        data = df.iloc[1:].copy()  # 跳过表头行
        self.logger.debug(f"After skipping header: {data.shape}")
        
        # 设置列名
        if len(data.columns) >= 3:
            data.columns = ["Diseases", "Cases", "Deaths"]
        else:
            self.logger.warning(f"Insufficient columns: {len(data.columns)}")
            return pd.DataFrame()
        
        # 清洗疾病名称（移除特殊字符）
        data["Diseases"] = data["Diseases"].str.replace(r"[^\w\s]", "", regex=True)
        data["Diseases"] = data["Diseases"].str.strip()
        
        # 添加额外的列
        date_value = metadata.get("date")
        
        try:
            # 处理DOI字段，如果是列表则转换为字符串
            doi_value = metadata.get("doi", "missing")
            if isinstance(doi_value, list):
                doi_value = "; ".join(doi_value)
            data["DOI"] = doi_value
            data["URL"] = metadata.get("url", "")
            data["Date"] = date_value
            data["YearMonthDay"] = date_value.strftime("%Y/%m/%d") if date_value else ""
            data["YearMonth"] = metadata.get("year_month", "")
            data["Source"] = metadata.get("source", "")
            data["Province"] = "China"
            data["ProvinceCN"] = "全国"
            data["ADCode"] = "100000"
            data["Incidence"] = -10  # 待计算
            data["Mortality"] = -10  # 待计算
            data["DiseasesCN"] = ""  # 需要通过映射获取
        except Exception as e:
            self.logger.error(f"Error adding columns: {e}")
            raise
        
        # 重新排序列
        column_order = [
            "Date", "YearMonthDay", "YearMonth",
            "Diseases", "DiseasesCN",
            "Cases", "Deaths",
            "Incidence", "Mortality",
            "ProvinceCN", "Province", "ADCode",
            "DOI", "URL", "Source"
        ]
        
        # 确保所有列都存在
        for col in column_order:
            if col not in data.columns:
                data[col] = ""
        
        self.logger.debug(f"Final columns: {list(data.columns)}")
        
        return data[column_order]
    
    def _clean_chinese_data(self, df: pd.DataFrame, metadata: Dict) -> pd.DataFrame:
        """
        清洗中文格式的数据
        
        Args:
            df: 原始数据
            metadata: 元数据
            
        Returns:
            pd.DataFrame: 清洗后的数据
        """
        # 复制数据
        data = df.iloc[1:].copy()  # 跳过表头行
        
        # 设置列名
        if len(data.columns) >= 3:
            data.columns = ["DiseasesCN", "Cases", "Deaths"]
        else:
            self.logger.warning(f"列数不足: {len(data.columns)}")
            return pd.DataFrame()
        
        # 移除"合计"行
        data = data[~data["DiseasesCN"].str.contains("合计", na=False)]
        
        # 清洗疾病名称 - 只保留中文字符、字母、数字、空格
        # 使用Unicode范围匹配中文: \u4e00-\u9fff
        data["DiseasesCN"] = data["DiseasesCN"].apply(
            lambda x: ''.join(c for c in str(x) if c.isalnum() or c.isspace() or '\u4e00' <= c <= '\u9fff')
        )
        data["DiseasesCN"] = data["DiseasesCN"].str.replace(
            "甲乙丙类总计", "合计", regex=False
        )
        data["DiseasesCN"] = data["DiseasesCN"].str.strip()
        
        # 添加额外的列
        data["DOI"] = metadata.get("doi", "missing")
        data["URL"] = metadata.get("url", "")
        data["Date"] = metadata.get("date")
        data["YearMonthDay"] = metadata.get("date").strftime("%Y/%m/%d") if metadata.get("date") else ""
        data["YearMonth"] = metadata.get("year_month", "")
        data["Source"] = metadata.get("source", "")
        data["Province"] = "China"
        data["ProvinceCN"] = "全国"
        data["ADCode"] = "100000"
        data["Incidence"] = -10
        data["Mortality"] = -10
        
        # Diseases 列需要通过映射获取（在后续步骤处理）
        data["Diseases"] = ""
        
        # 重新排序列
        column_order = [
            "Date", "YearMonthDay", "YearMonth",
            "Diseases", "DiseasesCN",
            "Cases", "Deaths",
            "Incidence", "Mortality",
            "ProvinceCN", "Province", "ADCode",
            "DOI", "URL", "Source"
        ]
        
        # 确保所有列都存在
        for col in column_order:
            if col not in data.columns:
                data[col] = ""
        
        return data[column_order]
    
    def _is_column_meaningful(self, series: pd.Series) -> bool:
        """
        检查列是否有意义（不全是空值或无用数据）
        
        Args:
            series: pandas Series
            
        Returns:
            bool: 是否有意义
        """
        # 检查是否所有值都是空或只包含空白字符
        non_empty = series.dropna()
        if len(non_empty) == 0:
            return False
        
        # 检查是否所有非空值都是相同的无意义内容
        unique_values = non_empty.unique()
        meaningless_patterns = ["", " ", "-", "—", "N/A", "n/a", "NA", "null", "NULL"]
        
        for val in unique_values:
            if str(val).strip() not in meaningless_patterns:
                return True
        
        return False
    
    def validate(self, data: pd.DataFrame) -> bool:
        """
        验证解析结果
        
        Args:
            data: 解析得到的数据
            
        Returns:
            bool: 是否有效
        """
        if data.empty:
            self.logger.warning("数据为空")
            return False
        
        # 检查必需的列
        required_columns = ["Diseases", "DiseasesCN", "Cases", "Deaths"]
        for col in required_columns:
            if col not in data.columns:
                self.logger.warning(f"缺少列: {col}")
                return False
        
        # 检查至少有一列有数据
        if data["Diseases"].notna().sum() == 0 and data["DiseasesCN"].notna().sum() == 0:
            self.logger.warning("疾病名称列均为空")
            return False
        
        # 检查Cases和Deaths列的类型（应该是数字或可转换为数字）
        for col in ["Cases", "Deaths"]:
            if col in data.columns:
                try:
                    # 尝试转换为数值类型
                    pd.to_numeric(data[col], errors="coerce")
                except Exception as e:
                    self.logger.warning(f"列 {col} 无法转换为数值: {e}")
                    return False
        
        return True
    
    def parse_from_url(self, url: str, **kwargs) -> ParseResult:
        """
        从URL解析数据（便捷方法）
        
        Args:
            url: 页面URL
            **kwargs: 额外参数
            
        Returns:
            ParseResult: 解析结果
        """
        kwargs["url"] = url
        return self.parse(url, **kwargs)
