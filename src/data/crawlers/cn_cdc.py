"""
GlobalID V2 China Infectious Disease Data Crawlers

从中国疾病预防控制中心(CDC)、卫健委等官方来源爬取传染病数据
"""
import re
from datetime import datetime
from typing import List, Optional
from urllib.parse import urljoin

import xmltodict
from bs4 import BeautifulSoup

from src.core import get_logger
from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)


class ChinaCDCCrawler(BaseCrawler):
    """
    中国CDC传染病数据爬虫
    
    支持多个数据源：
    1. China CDC Weekly（英文）
    2. 国家卫健委（中文）
    3. PubMed RSS（英文）
    """
    
    # 数据源配置
    CDC_WEEKLY_URL = "https://weekly.chinacdc.cn"
    GOV_API_URL = "https://www.ndcpa.gov.cn/queryList"
    PUBMED_RSS_URL = "https://pubmed.ncbi.nlm.nih.gov/rss/search/1tQjT4yH2iuqFpDL7Y1nShJmC4kDC5_BJYgw4R1O0BCs-_Nemt/?limit=100&utm_campaign=pubmed-2&fc=20230905093742"
    
    def __init__(self):
        super().__init__(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            timeout=30,
            max_retries=3,
            delay=1.0,
        )
    
    @staticmethod
    def extract_date_en(text: str) -> Optional[str]:
        """
        从英文文本中提取日期
        
        Args:
            text: 包含日期的文本，如 "Weekly Report - January 2024"
            
        Returns:
            格式化的日期字符串 "2024 January"，如果未找到则返回None
        """
        # 移除HTML标签和特殊字符
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"[^a-zA-Z0-9\s]", "", text)
        
        # 匹配 "Month YYYY" 或 "YYYY Month" 格式
        match = re.search(r"\b([A-Za-z]+)\s+(\d{4})\b", text)
        if match:
            month, year = match.groups()
            return f"{year} {month.capitalize()}"
        
        match = re.search(r"\b(\d{4})\s+([A-Za-z]+)\b", text)
        if match:
            year, month = match.groups()
            return f"{year} {month.capitalize()}"
        
        return None
    
    @staticmethod
    def extract_date_cn(text: str) -> Optional[str]:
        """
        从中文文本中提取日期
        
        Args:
            text: 包含日期的文本，如 "2024年1月"
            
        Returns:
            格式化的日期字符串"2024 January"，如果未找到则返回None
        """
        # 移除HTML标签
        text = re.sub(r"<[^>]+>", "", text)
        
        # 匹配中文日期格式 "YYYY年MM月"
        match = re.search(r"(\d{4})年(\d{1,2})月", text)
        if match:
            year, month = match.groups()
            date_obj = datetime(int(year), int(month), 1)
            return date_obj.strftime("%Y %B")
        
        return None
    
    async def crawl(self, source: str = "all", **kwargs) -> List[CrawlerResult]:
        """
        爬取中国传染病数据
        
        Args:
            source: 数据源 ("cdc_weekly", "nhc", "pubmed", "all")
            **kwargs: 额外参数
            
        Returns:
            爬取结果列表
        """
        results = []
        
        if source in ("cdc_weekly", "all"):
            try:
                cdc_results = self.crawl_cdc_weekly()
                results.extend(cdc_results)
                logger.info(f"CDC Weekly: 爬取到 {len(cdc_results)} 条记录")
            except Exception as e:
                logger.error(f"CDC Weekly 爬取失败: {e}")
        
        if source in ("nhc", "gov", "all"):
            try:
                gov_results = self.crawl_gov()
                results.extend(gov_results)
                logger.info(f"官方通报(GOV): 爬取到 {len(gov_results)} 条记录")
            except Exception as e:
                logger.error(f"官方通报(GOV)爬取失败: {e}")
        
        if source in ("pubmed", "all"):
            try:
                pubmed_results = self.crawl_pubmed_rss()
                results.extend(pubmed_results)
                logger.info(f"PubMed RSS: 爬取到 {len(pubmed_results)} 条记录")
            except Exception as e:
                logger.error(f"PubMed RSS 爬取失败: {e}")
        
        # 按日期排序
        results.sort(key=lambda x: x.date if x.date else datetime.min, reverse=True)
        
        logger.info(f"总计爬取到 {len(results)} 条记录")
        return results
    
    def crawl_cdc_weekly(self) -> List[CrawlerResult]:
        """爬取 China CDC Weekly 数据"""
        response = self.get(self.CDC_WEEKLY_URL)
        return self.parse_cdc_weekly(response)
    
    def crawl_gov(self) -> List[CrawlerResult]:
        """爬取国家疾控局(GOV)数据"""
        # 国家疾控局使用POST请求
        form_data = {
            'current': '1', 
            'pageSize': '10',
            'webSiteCode[]': 'jbkzzx',
            'channelCode[]': 'c100016'
        }
        response = self.post(self.GOV_API_URL, data=form_data)
        return self.parse_gov(response)
    
    def crawl_pubmed_rss(self) -> List[CrawlerResult]:
        """爬取 PubMed RSS 数据"""
        if not hasattr(self, 'PUBMED_RSS_URL') or not self.PUBMED_RSS_URL:
            logger.warning("PubMed RSS URL 未配置")
            return []
        
        response = self.get(self.PUBMED_RSS_URL)
        return self.parse_pubmed_rss(response)
    
    def parse(self, response) -> List[CrawlerResult]:
        """通用解析方法（根据来源分发）"""
        # 这个方法在子类中被具体的 parse_* 方法替代
        return []
    
    def parse_cdc_weekly(self, response) -> List[CrawlerResult]:
        """解析 CDC Weekly 页面"""
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        
        # 查找所有包含"National Notifiable Infectious Diseases"的链接
        for a_tag in soup.find_all("a", href=True):
            text = a_tag.text.strip()
            if "National Notifiable Infectious Diseases" in text:
                # 提取日期
                year_month = self.extract_date_en(text)
                if not year_month:
                    continue
                
                # 提取DOI
                link = a_tag.get("href")
                doi = None
                if "doi" in link:
                    doi = link.split("doi/")[1] if "doi/" in link else link
                
                # 解析日期对象
                try:
                    date_obj = datetime.strptime(year_month, "%Y %B")
                except ValueError:
                    logger.warning(f"无法解析日期: {year_month}")
                    continue
                
                # 构造完整URL
                full_url = urljoin(self.CDC_WEEKLY_URL, link)
                
                result = CrawlerResult(
                    title=text,
                    url=full_url,
                    date=date_obj,
                    year_month=year_month,
                    metadata={
                        "source": "China CDC Weekly",
                        "origin": "CN",
                        "doi": doi,
                        "language": "en",
                    },
                    raw_data={
                        "original_link": link,
                        "original_text": text,
                    },
                )
                results.append(result)
        
        return results

    def parse_gov(self, response) -> List[CrawlerResult]:
        """解析国家疾控局API响应"""
        try:
            data = response.json()
            items = data.get("data", {}).get("results", [])
        except Exception as e:
            logger.error(f"解析GOV数据失败: {e}")
            return []
        
        results = []
        for item in items[:10]:  # 只取前10条
            try:
                source = item.get("source", {})
                title = source.get("title", "")
                urls = source.get("urls", "")
                
                # 提取日期
                year_month = self.extract_date_cn(title)
                if not year_month:
                    continue
                
                # 解析日期对象
                date_obj = datetime.strptime(year_month, "%Y %B")
                
                # 解析URL
                import json
                url = json.loads(urls).get("common", "") if urls else ""
                full_url = urljoin(self.GOV_API_URL, url) if url else None
                
                result = CrawlerResult(
                    title=title,
                    url=full_url,
                    date=date_obj,
                    year_month=year_month,
                    metadata={
                        "source": "National Disease Control and Prevention Administration",
                        "origin": "CN",
                        "language": "zh",
                    },
                    raw_data=item,
                )
                results.append(result)
            except Exception as e:
                logger.warning(f"解析单条记录失败: {e}")
                continue
        
        return results
    
    def parse_pubmed_rss(self, response) -> List[CrawlerResult]:
        """解析 PubMed RSS Feed"""
        try:
            rss_data = xmltodict.parse(response.content)
            items = rss_data.get("rss", {}).get("channel", {}).get("item", [])
        except Exception as e:
            logger.error(f"解析PubMed RSS失败: {e}")
            return []
        
        results = []
        for item in items:
            try:
                title = item.get("title", "")
                
                # 提取日期
                year_month = self.extract_date_en(title)
                if not year_month:
                    continue
                
                # 解析日期对象
                date_obj = datetime.strptime(year_month, "%Y %B")
                
                # 获取原始PubMed URL
                pubmed_url = item.get("link")
                
                # 从 dc:identifier 中提取 PMCID
                pmc_url = None
                identifiers = item.get("dc:identifier", [])
                if not isinstance(identifiers, list):
                    identifiers = [identifiers]
                
                pmcid = None
                for identifier in identifiers:
                    if isinstance(identifier, str) and identifier.startswith("pmc:PMC"):
                        pmcid = identifier.replace("pmc:PMC", "")
                        pmc_url = f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{pmcid}/"
                        break
                
                result = CrawlerResult(
                    title=title,
                    url=pmc_url or pubmed_url,  # 优先使用PMC URL
                    date=date_obj,
                    year_month=year_month,
                    metadata={
                        "source": "PubMed",
                        "origin": "CN",
                        "doi": item.get("dc:identifier"),
                        "pub_date": item.get("pubDate"),
                        "language": "en",
                        "pubmed_url": pubmed_url,  # 保存原始PubMed URL
                        "pmcid": pmcid,  # 保存PMCID用于调试
                    },
                    raw_data=item,
                )
                results.append(result)
            except Exception as e:
                logger.warning(f"解析单条RSS记录失败: {e}")
                continue
        
        return results
