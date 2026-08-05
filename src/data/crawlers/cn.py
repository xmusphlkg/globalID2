"""
GlobalID V2 China Infectious Disease Data Crawlers

Crawls infectious disease data from China CDC, NHC, and PubMed.

Three-phase design:
1. Lightweight index fetch               - fetch_list()
2. Compare against DB, identify new data - check_new_data()
3. Crawl full content for new entries    - (handled by DataProcessor)
"""
import json
import re
from datetime import datetime
from typing import List, Optional, Set, Dict
from urllib.parse import urljoin

import xmltodict
from bs4 import BeautifulSoup
from sqlalchemy import select, func, text

from src.core import get_logger
from src.core.database import get_db
from src.domain import DiseaseRecord
from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)


class ChinaCDCCrawler(BaseCrawler):
    """
    China CDC infectious disease data crawler.

    Supports three data sources:
    1. China CDC Weekly (English)
    2. National Disease Control and Prevention Administration / NHC (Chinese)
    3. PubMed RSS (English)
    """
    
    # Source config
    CDC_WEEKLY_URL = "https://weekly.chinacdc.cn"
    CDC_WEEKLY_VOLUME_API = "/data/article/volumeArticles"
    GOV_API_URL = "https://www.ndcpa.gov.cn/queryList"
    PUBMED_RSS_URL = "https://pubmed.ncbi.nlm.nih.gov/rss/search/1tQjT4yH2iuqFpDL7Y1nShJmC4kDC5_BJYgw4R1O0BCs-_Nemt/?limit=100&utm_campaign=pubmed-2&fc=20230905093742"
    _RETRIEVAL_CHANNEL_PRIORITY = {
        "China CDC Weekly": 30,
        "Gov Data": 20,
        "PubMed": 10,
    }
    
    def __init__(self):
        super().__init__(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            timeout=30,
            max_retries=3,
            delay=1.0,
        )
        self.last_crawl_stats: Dict[str, object] = {}
    
    @staticmethod
    def extract_date_en(text: str) -> Optional[str]:
        """
        Extract a report date from English text.

        Args:
            text: Text containing a date, e.g. "Weekly Report - January 2024".

        Returns:
            Canonical date string "2024 January", or None if not found.
        """
        # Strip HTML tags and special characters
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"[^a-zA-Z0-9\s]", "", text)

        # Match "Month YYYY" or "YYYY Month" formats
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
        Extract a report date from Chinese text.

        Args:
            text: Text containing a Chinese date, e.g. "2024年1月".

        Returns:
            Canonical date string "2024 January", or None if not found.
        """
        # Strip HTML tags
        text = re.sub(r"<[^>]+>", "", text)

        # Match Chinese date format "YYYY年MM月"
        match = re.search(r"(\d{4})年(\d{1,2})月", text)
        if match:
            year, month = match.groups()
            date_obj = datetime(int(year), int(month), 1)
            return date_obj.strftime("%Y %B")
        
        return None
    
    async def fetch_list(self, source: str = "all", **kwargs) -> List[CrawlerResult]:
        """
        Phase 1: Lightweight index fetch.
        Retrieves only titles, URLs, and dates — no full content.

        Args:
            source: Data source ("cdc_weekly", "nhc", "pubmed", or "all").
            **kwargs: Extra parameters passed to individual fetch helpers.

        Returns:
            List of :class:`CrawlerResult` objects with metadata only.
        """
        results = []
        
        if source in ("cdc_weekly", "all"):
            try:
                cdc_results = self.crawl_cdc_weekly()
                results.extend(cdc_results)
                logger.info(f"[CN-CDC] CDC Weekly index | found={len(cdc_results)}")
            except Exception as e:
                logger.error(f"[CN-CDC] CDC Weekly fetch failed | error={e}")

        if source in ("nhc", "gov", "all"):
            try:
                gov_results = self.crawl_gov()
                results.extend(gov_results)
                logger.info(f"[CN-CDC] NHC Gov index | found={len(gov_results)}")
            except Exception as e:
                logger.error(f"[CN-CDC] NHC Gov fetch failed | error={e}")

        if source in ("pubmed", "all"):
            try:
                pubmed_results = self.crawl_pubmed_rss()
                results.extend(pubmed_results)
                logger.info(f"[CN-CDC] PubMed RSS index | found={len(pubmed_results)}")
            except Exception as e:
                logger.error(f"[CN-CDC] PubMed RSS fetch failed | error={e}")

        # These retrieval channels can surface the same national monthly
        # bulletin. Select one deterministically before concurrent processing
        # so conflict handling is never a last-finisher-wins policy.
        results = self._select_preferred_period_results(results)

        # Sort descending by date
        results.sort(key=lambda x: x.date if x.date else datetime.min, reverse=True)

        logger.info(f"[CN-CDC] Phase 1/3 Done | total_candidates={len(results)}")
        return results

    @classmethod
    def _select_preferred_period_results(
        cls, results: List[CrawlerResult]
    ) -> List[CrawlerResult]:
        """Keep one deterministic retrieval artifact per national period."""
        selected: Dict[str, CrawlerResult] = {}
        for position, result in enumerate(results):
            if result.year_month:
                period_key = result.year_month
            elif result.date:
                period_key = result.date.strftime("%Y %B")
            else:
                # Unknown-period artifacts must not collapse into one another.
                period_key = f"unknown:{position}:{result.url or result.title}"

            current = selected.get(period_key)
            if current is None or cls._retrieval_preference(result) > (
                cls._retrieval_preference(current)
            ):
                selected[period_key] = result

        removed = len(results) - len(selected)
        if removed:
            logger.info(
                "[CN-CDC] Duplicate retrieval artifacts removed"
                f" | removed={removed} periods={len(selected)}"
            )
        return list(selected.values())

    @classmethod
    def _retrieval_preference(cls, result: CrawlerResult) -> tuple[int, str, str]:
        source = str(result.metadata.get("source") or "")
        return (
            cls._RETRIEVAL_CHANNEL_PRIORITY.get(source, 0),
            str(result.url or ""),
            str(result.title or ""),
        )
    
    async def check_new_data(
        self,
        list_results: List[CrawlerResult],
        *,
        fill_missing: bool = False,
    ) -> Dict[str, object]:
        """
        Phase 2: Identify which reports are new (compare against the database).

        Logic:
        1. Query the database for the latest record time (CN, excluding future dates).
        2. Return only reports dated after that time (true incremental update).
        3. Optionally back-fill months that are absent from the DB.

        Args:
            list_results: Candidate list from :meth:`fetch_list`.
            fill_missing:  If True, also flag months missing from DB for re-fetch.

        Returns:
            Dict with keys ``new``, ``existing``, and ``stats``.
        """
        from datetime import date
        today = date.today()
        
        # Query the DB for the latest record time (CN only, exclude future dates)
        async with get_db() as session:
            country_result = await session.execute(
                text("SELECT id FROM countries WHERE code = :code"),
                {"code": "CN"},
            )
            country_row = country_result.fetchone()
            country_id = country_row[0] if country_row else None

            max_time = None
            if country_id is not None:
                result = await session.execute(
                    select(func.max(DiseaseRecord.time)).select_from(DiseaseRecord).where(
                        DiseaseRecord.country_id == country_id,
                        DiseaseRecord.time <= today,
                    )
                )
                max_time = result.scalar()

            existing_year_months: Set[str] = set()
            if fill_missing and country_id is not None:
                # Collect existing months to enable "gap backfill"
                months_result = await session.execute(
                    select(func.date_trunc("month", DiseaseRecord.time))
                    .distinct()
                    .where(
                        DiseaseRecord.country_id == country_id,
                        DiseaseRecord.time <= today,
                    )
                )
                for (month_dt,) in months_result.fetchall():
                    if month_dt is None:
                        continue
                    # Normalize to the same format used by crawler results: "YYYY Month"
                    existing_year_months.add(month_dt.strftime("%Y %B"))
        
        if max_time:
            max_date = max_time.date()
            logger.info(f"[CN-CDC] DB latest record | max_date={max_date} (future dates excluded)")
        else:
            max_date = None
            logger.info("[CN-CDC] DB is empty — will crawl all available data")
        
        # Filter: keep reports dated after the DB latest, or missing months if backfill requested
        new_results = []
        existing_results = []
        
        missing_months: Set[str] = set()
        for result in list_results:
            if result.date is None:
                logger.warning(f"[CN-CDC] Report missing date, skipping | title={result.title!r}")
                continue
            
            result_date = result.date.date() if hasattr(result.date, 'date') else result.date
            
            # If backfill is enabled, also fetch reports whose month is missing in DB.
            is_missing_month = fill_missing and (result.year_month is not None) and (result.year_month not in existing_year_months)
            if is_missing_month and result.year_month:
                missing_months.add(result.year_month)

            # Crawl if DB is empty, or report date is newer than the DB latest
            if max_date is None or result_date > max_date or is_missing_month:
                new_results.append(result)
            else:
                existing_results.append(result)
        
        logger.info(
            f"[CN-CDC] Phase 2/3 Done | new={len(new_results)} existing={len(existing_results)}"
            f" max_date={max_date} fill_missing={fill_missing}"
        )
        if new_results:
            new_months = sorted(set(r.year_month for r in new_results if r.year_month))
            logger.info(f"[CN-CDC] New data months | months={new_months}")
        
        return {
            'new': new_results,
            'existing': existing_results,
            'stats': {
                'max_date': max_date.isoformat() if max_date else None,
                'total_candidates': len(list_results),
                'new_count': len(new_results),
                'existing_count': len(existing_results),
                'fill_missing': fill_missing,
                'missing_months_count': len(missing_months),
                'missing_months': sorted(missing_months),
            },
        }
    
    async def crawl(
        self,
        source: str = "all",
        force: bool = False,
        fill_missing: bool = False,
        **kwargs,
    ) -> List[CrawlerResult]:
        """
        Full three-phase crawl pipeline.

        1. Fetch lightweight index (fetch_list).
        2. Compare against DB to find new/missing entries (check_new_data).
        3. Return new entries so the caller (DataProcessor) can fetch and process them.

        Args:
            source:       Data source ("cdc_weekly", "nhc", "pubmed", or "all").
            force:        If True, skip DB comparison and return all candidates.
            fill_missing: If True, also include months absent from the DB.
            **kwargs:     Extra parameters forwarded to fetch_list.

        Returns:
            List of new :class:`CrawlerResult` objects to process.
        """
        # Phase 1: fetch index
        logger.info("[CN-CDC] Phase 1/3 — Fetching index")
        list_results = await self.fetch_list(source=source, **kwargs)
        self.last_crawl_stats = {
            "source": source,
            "force": force,
            "fill_missing": fill_missing,
            "total_candidates": len(list_results),
        }

        if not list_results:
            logger.warning("[CN-CDC] No candidates found")
            return []

        # Phase 2: compare against DB
        if force:
            logger.info("[CN-CDC] Phase 2/3 — Force mode: processing all candidates")
            new_results = list_results
            self.last_crawl_stats.update({
                "mode": "force",
                "new_count": len(new_results),
                "existing_count": 0,
                "max_date": None,
                "missing_months_count": 0,
                "missing_months": [],
            })
        else:
            logger.info("[CN-CDC] Phase 2/3 — Checking new data")
            check_result = await self.check_new_data(list_results, fill_missing=fill_missing)
            new_results = check_result["new"]
            self.last_crawl_stats.update(check_result.get("stats", {}))

        if not new_results:
            logger.info("[CN-CDC] No new data to process")
            return []

        # Phase 3: full detail fetch is delegated to DataProcessor
        logger.info(f"[CN-CDC] Phase 3/3 — Ready to process | reports={len(new_results)}")
        
        return new_results
    
    def crawl_cdc_weekly(self) -> List[CrawlerResult]:
        """Fetch China CDC Weekly index."""
        publication_year = datetime.now().year
        results: List[CrawlerResult] = []
        api_errors: List[Exception] = []

        # The journal homepage only contains the current issue. Its official
        # volume endpoint contains every article in the year, including the
        # monthly notifiable-disease reports needed by incremental updates.
        for year in (publication_year, publication_year - 1):
            volume = year - 2018
            try:
                response = self.post(
                    urljoin(self.CDC_WEEKLY_URL, self.CDC_WEEKLY_VOLUME_API),
                    data={"year": str(year), "volume": str(volume)},
                )
                results.extend(self.parse_cdc_weekly_volume(response.json()))
            except Exception as exc:
                api_errors.append(exc)
                logger.warning(
                    f"[CN-CDC] Volume index failed | year={year} volume={volume} error={exc}"
                )

        if results:
            deduplicated = {result.url: result for result in results if result.url}
            return sorted(
                deduplicated.values(),
                key=lambda result: result.date or datetime.min,
                reverse=True,
            )

        # Compatibility fallback for older deployments of the journal site.
        response = self.get(self.CDC_WEEKLY_URL)
        fallback = self.parse_cdc_weekly(response)
        if not fallback and api_errors:
            raise RuntimeError(
                "China CDC Weekly volume index and homepage fallback returned no reports"
            ) from api_errors[-1]
        return fallback

    def parse_cdc_weekly_volume(self, payload: object) -> List[CrawlerResult]:
        """Parse the journal's official volume JSON into report candidates."""
        articles: List[dict] = []

        def collect(value: object) -> None:
            if isinstance(value, dict):
                title = str(value.get("titleEn") or value.get("title") or "").strip()
                if "National Notifiable Infectious Diseases" in title:
                    articles.append(value)
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(payload)
        results: List[CrawlerResult] = []
        for article in articles:
            title = str(article.get("titleEn") or article.get("title") or "").strip()
            year_month = self.extract_date_en(title)
            doi = str(article.get("doi") or "").strip()
            if not year_month or not doi:
                continue
            try:
                date_obj = datetime.strptime(year_month, "%Y %B")
            except ValueError:
                continue
            url = urljoin(self.CDC_WEEKLY_URL, f"/en/article/doi/{doi}")
            results.append(
                CrawlerResult(
                    title=title,
                    url=url,
                    date=date_obj,
                    year_month=year_month,
                    metadata={
                        "source": "China CDC Weekly",
                        "ontology_source_id": "SRC_CN_CDC",
                        "origin": "CN",
                        "doi": doi,
                        "language": "en",
                    },
                    raw_data={
                        "article_id": article.get("id"),
                        "article_no": article.get("articleNo"),
                        "issue": article.get("issue"),
                    },
                )
            )
        return results

    def crawl_gov(self) -> List[CrawlerResult]:
        """Fetch NHC Gov API index."""
        # NHC Gov API uses POST requests
        form_data = {
            'current': '1', 
            'pageSize': '10',
            'webSiteCode[]': 'jbkzzx',
            'channelCode[]': 'c100016'
        }
        response = self.post(self.GOV_API_URL, data=form_data)
        return self.parse_gov(response)
    
    def crawl_pubmed_rss(self) -> List[CrawlerResult]:
        """Fetch PubMed RSS feed."""
        if not hasattr(self, 'PUBMED_RSS_URL') or not self.PUBMED_RSS_URL:
            logger.warning("[CN-CDC] PubMed RSS URL not configured")
            return []
        
        response = self.get(self.PUBMED_RSS_URL)
        return self.parse_pubmed_rss(response)
    
    def parse(self, response) -> List[CrawlerResult]:
        """Generic parse dispatcher — delegated to specific parse_* methods."""
        return []
    
    def parse_cdc_weekly(self, response) -> List[CrawlerResult]:
        """Parse China CDC Weekly HTML index page."""
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        
        # Find all links referencing "National Notifiable Infectious Diseases"
        for a_tag in soup.find_all("a", href=True):
            text = a_tag.text.strip()
            if "National Notifiable Infectious Diseases" in text:
                # Extract date
                year_month = self.extract_date_en(text)
                if not year_month:
                    continue
                
                # Extract DOI
                link = a_tag.get("href")
                doi = None
                if "doi" in link:
                    doi = link.split("doi/")[1] if "doi/" in link else link
                
                # Parse into a date object
                try:
                    date_obj = datetime.strptime(year_month, "%Y %B")
                except ValueError:
                    logger.warning(f"[CN-CDC] Cannot parse date | year_month={year_month!r}")
                    continue
                
                # Build absolute URL
                full_url = urljoin(self.CDC_WEEKLY_URL, link)
                
                result = CrawlerResult(
                    title=text,
                    url=full_url,
                    date=date_obj,
                    year_month=year_month,
                    metadata={
                        "source": "China CDC Weekly",
                        "ontology_source_id": "SRC_CN_CDC",
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
        """Parse NHC Gov API JSON response."""
        try:
            data = response.json()
            items = data.get("data", {}).get("results", [])
        except Exception as e:
            logger.error(f"[CN-CDC] Gov API parse failed | error={e}")
            return []
        
        results = []
        for item in items[:10]:  # limit to first 10 items
            try:
                source = item.get("source", {})
                title = source.get("title", "")
                urls = source.get("urls", "")

                # Extract date
                year_month = self.extract_date_cn(title)
                if not year_month:
                    continue

                # Parse into a date object
                date_obj = datetime.strptime(year_month, "%Y %B")

                # Resolve URL
                url = json.loads(urls).get("common", "") if urls else ""
                full_url = urljoin(self.GOV_API_URL, url) if url else None
                
                result = CrawlerResult(
                    title=title,
                    url=full_url,
                    date=date_obj,
                    year_month=year_month,
                    metadata={
                        "source": "Gov Data",
                        "ontology_source_id": "SRC_CN_CDC",
                        "origin": "CN",
                        "language": "zh",
                    },
                    raw_data=item,
                )
                results.append(result)
            except Exception as e:
                logger.warning(f"[CN-CDC] Gov record parse failed | error={e}")
                continue

        return results

    def parse_pubmed_rss(self, response) -> List[CrawlerResult]:
        """Parse PubMed RSS feed XML."""
        try:
            rss_data = xmltodict.parse(response.content)
            items = rss_data.get("rss", {}).get("channel", {}).get("item", [])
        except Exception as e:
            logger.error(f"[CN-CDC] PubMed RSS parse failed | error={e}")
            return []
        
        results = []
        for item in items:
            try:
                title = item.get("title", "")
                
                # Extract date
                year_month = self.extract_date_en(title)
                if not year_month:
                    continue

                # Parse into a date object
                date_obj = datetime.strptime(year_month, "%Y %B")

                # Original PubMed URL
                pubmed_url = item.get("link")
                
                # Extract PMCID from dc:identifier
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
                    url=pmc_url or pubmed_url,  # prefer PMC URL when available
                    date=date_obj,
                    year_month=year_month,
                    metadata={
                        "source": "PubMed",
                        "ontology_source_id": "SRC_CN_CDC",
                        "origin": "CN",
                        "doi": item.get("dc:identifier"),
                        "pub_date": item.get("pubDate"),
                        "language": "en",
                        "pubmed_url": pubmed_url,  # fallback URL
                        "pmcid": pmcid,  # for debugging
                    },
                    raw_data=item,
                )
                results.append(result)
            except Exception as e:
                logger.warning(f"[CN-CDC] PubMed RSS item parse failed | error={e}")
                continue

        return results
