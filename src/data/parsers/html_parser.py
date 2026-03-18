"""
GlobalID V2 HTML Table Parser

Parses HTML pages containing infectious disease data tables. Supports both
English (Diseases / Cases / Deaths) and Chinese (疾病名称 / 病例数 / 死亡数) formats.

Design principles:
  - parse(html_content)  accepts HTML strings only (single responsibility).
  - fetch_and_parse(url) is the explicit method that owns network I/O.
"""
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.core import get_logger
from src.core.missing_values import normalize_rate_columns
from .base import BaseParser, ParseResult

logger = get_logger(__name__)

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class HTMLTableParser(BaseParser):
    """
    HTML table parser for infectious disease reports.

    Parses 3-column tables (Disease / Cases / Deaths) from China CDC and
    government websites. Supports both English and Chinese source formats.

    ``parse()`` accepts HTML strings only.
    Use ``fetch_and_parse(url)`` when you need to download and parse in one call.
    """

    def __init__(self):
        super().__init__()
        # Session is created lazily in fetch_and_parse only
        self._session: Optional[requests.Session] = None

    @property
    def _http_session(self) -> requests.Session:
        """Lazy-init HTTP session for fetch_and_parse."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(_FETCH_HEADERS)
        return self._session

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def parse(self, content: str, **kwargs) -> ParseResult:
        """
        Parse an HTML string and extract table data.

        Args:
            content: Pre-fetched HTML string (URLs are not accepted here).
            **kwargs:
                - url:        Record the data source URL.
                - title:      Page title.
                - date:       Report date.
                - year_month: Year-month string (e.g. "2025 January").
                - source:     Data source name.
                - language:   Language (``'en'`` or ``'zh'``).

        Returns:
            :class:`ParseResult`
        """
        url = kwargs.get("url", "")
        title = kwargs.get("title", "")
        language = kwargs.get("language", "en")

        html_content = content
        try:
            
            # 解析HTML
            soup = BeautifulSoup(html_content, "html.parser")
            
            # 提取表格
            tables = soup.find_all("table")
            if not tables:
                return ParseResult(
                    source_url=url,
                    source_title=title,
                    success=False,
                    error_message="No table found in HTML",
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
                    error_message="Table is empty",
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
                    error_message="Data validation failed",
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
            self.logger.error(f"[HTMLTableParser] Parse failed | error={e}")
            return ParseResult(
                source_url=url,
                source_title=title,
                success=False,
                error_message=str(e),
                metadata=kwargs,
            )
    
    def _extract_table_data(self, table) -> pd.DataFrame:
        """
        Extract row data from a BeautifulSoup table element.

        Args:
            table: BeautifulSoup ``<table>`` element.

        Returns:
            DataFrame with columns ``["Diseases", "Cases", "Deaths"]``.
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
                        # Remove superscript tags (usually footnote markers)
                        for sup in td.find_all("sup"):
                            sup.decompose()
                        text = td.get_text(strip=True)
                        row.append(text)
                    # Keep only 3-column rows (skip footnote rows)
                    if len(row) == 3:
                        data.append(row)
        
        if not data:
            return pd.DataFrame()
        
        # 创建DataFrame
        df = pd.DataFrame(data)
        self.logger.debug(f"Created DataFrame shape: {df.shape}")
        
        # Ensure exactly 3 columns; truncate if more
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
        Clean an English-format table DataFrame.

        Args:
            df:       Raw DataFrame from ``_extract_table_data``.
            metadata: Crawl metadata (date, url, source, …).

        Returns:
            Cleaned DataFrame with standardised columns.
        """
        self.logger.debug(f"_clean_english_data input DataFrame shape: {df.shape}")
        
        # 复制数据，避免修改原始数据
        data = df.iloc[1:].copy()  # skip header row
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
            data["Incidence"] = None
            data["Mortality"] = None
            data["DiseasesCN"] = ""  # populated via mapping in a later step
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
        
        return normalize_rate_columns(data[column_order], copy=False)
    
    def _clean_chinese_data(self, df: pd.DataFrame, metadata: Dict) -> pd.DataFrame:
        """
        Clean a Chinese-format table DataFrame.

        Args:
            df:       Raw DataFrame from ``_extract_table_data``.
            metadata: Crawl metadata (date, url, source, …).

        Returns:
            Cleaned DataFrame with standardised columns.
        """
        data = df.iloc[1:].copy()  # skip header row
        
        # 设置列名
        if len(data.columns) >= 3:
            data.columns = ["DiseasesCN", "Cases", "Deaths"]
        else:
            self.logger.warning(f"Insufficient columns: {len(data.columns)}")
            return pd.DataFrame()
        
        # Remove summary rows ("合计" = total)
        data = data[~data["DiseasesCN"].str.contains("合计", na=False)]

        # Keep only CJK characters, ASCII alphanumerics, and spaces
        # Unicode CJK range: \u4e00-\u9fff
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
        data["Incidence"] = None
        data["Mortality"] = None
        
        # "Diseases" column is populated via mapping in a later step
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
        
        return normalize_rate_columns(data[column_order], copy=False)
    
    def _is_column_meaningful(self, series: pd.Series) -> bool:
        """
        Check whether a column has at least one meaningful non-empty value.

        Args:
            series: pandas Series to inspect.

        Returns:
            True if at least one value is non-null and non-trivial.
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
        Validate a parsed DataFrame.

        Args:
            data: Parsed DataFrame to validate.

        Returns:
            True if the data passes all quality checks.
        """
        if data.empty:
            self.logger.warning("[HTMLTableParser] Validation failed: empty DataFrame")
            return False
        
        # 检查必需的列
        required_columns = ["Diseases", "DiseasesCN", "Cases", "Deaths"]
        for col in required_columns:
            if col not in data.columns:
                self.logger.warning(f"[HTMLTableParser] Missing required column: {col}")
                return False

        # At least one disease name column must have data
        if data["Diseases"].notna().sum() == 0 and data["DiseasesCN"].notna().sum() == 0:
            self.logger.warning("[HTMLTableParser] Both disease name columns are empty")
            return False
        
        # 检查Cases和Deaths列的类型（应该是数字或可转换为数字）
        for col in ["Cases", "Deaths"]:
            if col in data.columns:
                try:
                    pd.to_numeric(data[col], errors="coerce")
                except Exception as e:
                    self.logger.warning(f"[HTMLTableParser] Column {col} cannot be converted to numeric: {e}")
                    return False
        
        return True
    
    def fetch_and_parse(self, url: str, **kwargs) -> ParseResult:
        """
        Fetch HTML from a URL and parse it.

        This method explicitly owns the network I/O, replacing the old
        pattern of passing a URL as the ``content`` argument to ``parse()``.

        Args:
            url:      Page URL to fetch.
            **kwargs: Same optional arguments as ``parse()``.

        Returns:
            :class:`ParseResult`
        """
        kwargs.setdefault("url", url)
        try:
            self.logger.debug(f"Fetching from URL: {url}")
            response = self._http_session.get(url, timeout=30)
            response.raise_for_status()
            return self.parse(response.text, **kwargs)
        except requests.RequestException as exc:
            return ParseResult(
                source_url=url,
                source_title=kwargs.get("title", ""),
                success=False,
                error_message=f"HTTP request failed: {exc}",
                metadata=kwargs,
            )

    def parse_from_url(self, url: str, **kwargs) -> ParseResult:
        """Backward-compatible alias for :meth:`fetch_and_parse`."""
        return self.fetch_and_parse(url, **kwargs)
