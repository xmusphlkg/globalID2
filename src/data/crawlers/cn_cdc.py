"""  
GlobalID V2 China Infectious Disease Data Crawlers

从中国疾病预防控制中心(CDC)、卫健委等官方来源爬取传染病数据

设计理念（参考1.0版本）：
1. 先获取列表（轻量级）- fetch_list()
2. 提取年月信息并与数据库对比 - check_new_data()
3. 只爬取新数据的详细内容（重量级）- crawl_details()
"""
import re
from datetime import datetime
from typing import List, Optional, Set, Dict
from urllib.parse import urljoin

import xmltodict
from bs4 import BeautifulSoup
from sqlalchemy import select, func

from src.core import get_logger
from src.core.database import get_db
from src.domain import DiseaseRecord
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
    
    async def fetch_list(self, source: str = "all", **kwargs) -> List[CrawlerResult]:
        """
        第一阶段：获取数据列表（轻量级操作）
        只获取标题、URL、日期等元信息，不爬取详细内容
        
        Args:
            source: 数据源 ("cdc_weekly", "nhc", "pubmed", "all")
            **kwargs: 额外参数
            
        Returns:
            元信息列表（不含详细内容）
        """
        results = []
        
        if source in ("cdc_weekly", "all"):
            try:
                cdc_results = self.crawl_cdc_weekly()
                results.extend(cdc_results)
                logger.info(f"CDC Weekly: 发现 {len(cdc_results)} 个报告")
            except Exception as e:
                logger.error(f"CDC Weekly 列表获取失败: {e}")
        
        if source in ("nhc", "gov", "all"):
            try:
                gov_results = self.crawl_gov()
                results.extend(gov_results)
                logger.info(f"国家疾控局: 发现 {len(gov_results)} 个报告")
            except Exception as e:
                logger.error(f"国家疾控局列表获取失败: {e}")
        
        if source in ("pubmed", "all"):
            try:
                pubmed_results = self.crawl_pubmed_rss()
                results.extend(pubmed_results)
                logger.info(f"PubMed RSS: 发现 {len(pubmed_results)} 个报告")
            except Exception as e:
                logger.error(f"PubMed RSS 列表获取失败: {e}")
        
        # 按日期排序
        results.sort(key=lambda x: x.date if x.date else datetime.min, reverse=True)
        
        logger.info(f"总计发现 {len(results)} 个报告")
        return results
    
    async def check_new_data(self, list_results: List[CrawlerResult]) -> Dict[str, List[CrawlerResult]]:
        """
        第二阶段：检查哪些数据是新的（与数据库对比）
        
        逻辑（参考1.0版本）：
        1. 查询数据库中最新的数据时间
        2. 只爬取时间晚于数据库最新时间的报告
        3. 这样实现真正的增量更新
        
        Args:
            list_results: 从fetch_list获取的列表
            
        Returns:
            字典，包含 'new' 和 'existing' 两个键
        """
        from datetime import date
        today = date.today()
        
        # 获取数据库中最新的数据时间（排除未来日期）
        async with get_db() as session:
            result = await session.execute(
                select(func.max(DiseaseRecord.time)).select_from(DiseaseRecord).where(
                    DiseaseRecord.time <= today
                )
            )
            max_time = result.scalar()
        
        if max_time:
            max_date = max_time.date()
            logger.info(f"数据库中最新数据时间: {max_date} (排除未来日期)")
        else:
            max_date = None
            logger.info("数据库为空，将爬取所有数据")
        
        # 筛选出时间晚于数据库最新时间的报告
        new_results = []
        existing_results = []
        
        for result in list_results:
            if result.date is None:
                logger.warning(f"报告缺少日期信息，跳过: {result.title}")
                continue
            
            result_date = result.date.date() if hasattr(result.date, 'date') else result.date
            
            # 如果数据库为空，或报告时间晚于数据库最新时间，则需要爬取
            if max_date is None or result_date > max_date:
                new_results.append(result)
            else:
                existing_results.append(result)
        
        logger.info(f"发现 {len(new_results)} 个新报告需要爬取（时间 > {max_date}）")
        if new_results:
            # 按月份汇总新数据
            new_months = sorted(set(r.year_month for r in new_results if r.year_month))
            logger.info(f"新数据月份: {new_months}")
        
        return {
            'new': new_results,
            'existing': existing_results
        }
    
    async def crawl(self, source: str = "all", force: bool = False, **kwargs) -> List[CrawlerResult]:
        """
        智能爬取流程（参考1.0版本设计）：
        1. 先获取列表（轻量级）
        2. 与数据库对比
        3. 只爬取新数据的详情（重量级）
        
        Args:
            source: 数据源 ("cdc_weekly", "nhc", "pubmed", "all")
            force: 是否强制爬取所有数据（忽略数据库检查）
            **kwargs: 额外参数
            
        Returns:
            爬取的新数据列表
        """
        # 第一阶段：获取列表
        logger.info("[阶段1/3] 获取数据列表...")
        list_results = await self.fetch_list(source=source, **kwargs)
        
        if not list_results:
            logger.warning("未发现任何数据")
            return []
        
        # 第二阶段：检查新数据
        if force:
            logger.info("[阶段2/3] 强制模式：将爬取所有数据")
            new_results = list_results
        else:
            logger.info("[阶段3/3] 检查新数据...")
            check_result = await self.check_new_data(list_results)
            new_results = check_result['new']
        
        if not new_results:
            logger.info("✓ 无新数据需要爬取")
            return []
        
        # 第三阶段：爬取详情（这部分留给后续的processor处理）
        logger.info(f"[阶段3/3] 将处理 {len(new_results)} 个新报告")
        
        return new_results
    
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
