"""
GlobalID V2 Data Processor

Orchestrates the full data pipeline: crawl results → parse → normalise → validate → store.
Storage is delegated to ``src.data.storage.RecordStore``.
"""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import hashlib
import re
import os

import pandas as pd
from bs4 import BeautifulSoup

from src.core import get_logger
from src.core.database import get_db
from src.core.missing_values import normalize_rate_columns
from src.domain import CrawlRawPage
from src.data.crawlers.base import CrawlerResult
from src.data.parsers.html_parser import HTMLTableParser
from src.data.normalizers.english_mapper import create_disease_mapper
from src.data.storage.record_store import RecordStore

logger = get_logger(__name__)


class DataProcessor:
    """
    Data pipeline orchestrator.

    Responsibilities:
    1. Parse HTML tables via :class:`HTMLTableParser`.
    2. Normalise disease names via :class:`DiseaseMapperDB`.
    3. Clean and validate the resulting DataFrame.
    4. Persist records via :class:`RecordStore`.
    5. Archive raw page content.

    Does NOT perform HTTP fetching (handled by crawlers) or bulk storage
    optimisation (handled by RecordStore).
    """
    
    def __init__(
        self,
        output_dir: Optional[Path] = None,
        country_code: str = "CN",
    ):
        """
        Initialise the processor.

        Args:
            output_dir:   Directory for CSV output files.
            country_code: ISO country code (uppercase, e.g. "CN").
        """
        self.output_dir = output_dir or Path("data/processed")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.country_code = country_code.upper()  # ensure uppercase

        # Parser is stateless; mapper is created on demand inside async methods
        self.parser = HTMLTableParser()
        self._record_store = RecordStore()

        # Limit concurrency to avoid event-loop contention
        self.max_concurrent = int(os.getenv('MAX_CRAWLER_CONCURRENT', '2'))
        
        logger.debug(f"Data processor initialized (country: {country_code}, max_concurrent: {self.max_concurrent})")
    
    async def process_crawler_results(
        self,
        results: List[CrawlerResult],
        save_to_file: bool = True,
        save_raw: bool = False,
        crawl_run_id: Optional[int] = None,
        raw_dir: Optional[Path] = None,
        progress_callback: Optional[callable] = None,
    ) -> List[pd.DataFrame]:
        """
        Process a list of crawler results (supports concurrent execution).

        Args:
            results:            List of :class:`CrawlerResult` objects from a crawler.
            save_to_file:       Whether to write each processed DataFrame to CSV.
            save_raw:           Whether to archive raw HTML content.
            crawl_run_id:       Run ID for raw page archival.
            raw_dir:            Directory for raw page archive files.
            progress_callback:  Optional async callback ``callback(current, total, message)``.

        Returns:
            List of processed DataFrames (one per successfully processed result).
        """
        logger.info(f"[DataProcessor][{self.country_code}] Processing | reports={len(results)} concurrency={self.max_concurrent}")
        
        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        # Process all results concurrently
        tasks = [
            self._process_single_result(
                i, result, len(results), save_to_file, save_raw, 
                crawl_run_id, raw_dir, semaphore, progress_callback
            )
            for i, result in enumerate(results, 1)
        ]
        
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out None and exceptions
        processed_data = [r for r in results_list if r is not None and not isinstance(r, Exception)]
        
        # Count errors
        errors = [r for r in results_list if isinstance(r, Exception)]
        if errors:
            logger.warning(f"[DataProcessor][{self.country_code}] Processing finished with errors | errors={len(errors)}")

        logger.info(f"[DataProcessor][{self.country_code}] Done | processed={len(processed_data)} total={len(results)}")
        return processed_data
    
    async def _process_single_result(
        self,
        index: int,
        result: CrawlerResult,
        total: int,
        save_to_file: bool,
        save_raw: bool,
        crawl_run_id: Optional[int],
        raw_dir: Optional[Path],
        semaphore: asyncio.Semaphore,
        progress_callback: Optional[callable] = None,
    ) -> Optional[pd.DataFrame]:
        """Process a single crawler result within the concurrency semaphore."""
        async with semaphore:
            try:
                # Log progress
                if progress_callback:
                    await progress_callback(index, total, f"Processing: {result.year_month}")
                else:
                    logger.debug(f"[DataProcessor][{self.country_code}] [{index}/{total}] Parse OK | period={result.year_month!r}")
                
                # Parse HTML table: use pre-fetched content if available, otherwise fetch from URL
                _parse_kwargs = dict(
                    title=result.title,
                    date=result.date,
                    year_month=result.year_month,
                    source=result.metadata.get("source"),
                    language=result.metadata.get("language", "en"),
                    doi=result.metadata.get("doi"),
                )
                if result.content:
                    parse_result = self.parser.parse(result.content, url=result.url, **_parse_kwargs)
                elif result.url:
                    parse_result = self.parser.fetch_and_parse(result.url, **_parse_kwargs)
                else:
                    logger.warning(f"[DataProcessor][{self.country_code}] [{index}/{total}] No content or URL | title={result.title!r}")
                    return None
                
                if not parse_result.success or not parse_result.has_data:
                    logger.debug(f"[DataProcessor][{self.country_code}] Parse failed | [{index}/{total}] error={parse_result.error_message!r}")
                    return None

                if save_raw and crawl_run_id and raw_dir and parse_result.raw_content:
                    await self._save_raw_content(
                        run_id=crawl_run_id,
                        raw_dir=raw_dir,
                        result=result,
                        raw_html=parse_result.raw_content,
                        fetched_at=parse_result.parse_date,
                    )
                
                # Create the appropriate disease mapper for this source
                data_source = result.metadata.get("source", "")
                language = result.metadata.get("language", "en")

                # New multi-language architecture: country code and language are separate
                disease_mapper = await create_disease_mapper(
                    country_code=self.country_code or "CN",  # use the processor's country code
                    language=language,
                    data_source=data_source
                )

                # Normalise disease names
                df = await self._normalize_disease_names(
                    parse_result.data,
                    language=language,
                    disease_mapper=disease_mapper,
                )

                # Calculate incidence/mortality rates (if population data is available)
                df = self._calculate_rates(df)
                
                # Validate data
                if not self._validate_data(df):
                    logger.debug(f"[DataProcessor][{self.country_code}] [{index}/{total}] Validation failed | period={result.year_month!r}")
                    return None
                
                # Save to file
                if save_to_file and result.year_month:
                    self._save_to_file(df, result.year_month)
                
                # Save to database via RecordStore (batch upsert, no N+1)
                await self._record_store.save_dataframe(df, self.country_code)
                
                return df
                
            except Exception as e:
                logger.error(f"[DataProcessor][{self.country_code}] [{index}/{total}] Failed | error={e}")
                return None

    async def save_raw_pages(
        self,
        results: List[CrawlerResult],
        crawl_run_id: int,
        raw_dir: Path,
    ) -> int:
        """Archive raw page text only (no normalisation or DB writes)."""
        saved = 0
        for i, result in enumerate(results, 1):
            try:
                _raw_kwargs = dict(
                    title=result.title,
                    date=result.date,
                    year_month=result.year_month,
                    source=result.metadata.get("source"),
                    language=result.metadata.get("language", "en"),
                    doi=result.metadata.get("doi"),
                )
                if result.content:
                    parse_result = self.parser.parse(result.content, url=result.url, **_raw_kwargs)
                elif result.url:
                    parse_result = self.parser.fetch_and_parse(result.url, **_raw_kwargs)
                else:
                    continue

                if parse_result.raw_content:
                    await self._save_raw_content(
                        run_id=crawl_run_id,
                        raw_dir=raw_dir,
                        result=result,
                        raw_html=parse_result.raw_content,
                        fetched_at=parse_result.parse_date,
                    )
                    saved += 1
            except Exception as e:
                logger.warning(f"[DataProcessor] Raw page save failed | title={result.title!r} error={e}")
                continue

        return saved
    
    async def _normalize_disease_names(
        self,
        df: pd.DataFrame,
        language: str = "en",
        disease_mapper = None,
    ) -> pd.DataFrame:
        """
        Normalise disease names in a DataFrame using the disease mapper.

        Args:
            df:             Input DataFrame.
            language:       Source language ("en" or "zh").
            disease_mapper: Mapper instance; created from factory if not supplied.

        Returns:
            DataFrame with ``Diseases``, ``DiseasesCN``, and ``disease_id`` columns populated.
        """
        logger.debug(f"_normalize_disease_names input shape: {df.shape}")

        # If no mapper supplied, create one from the factory
        if disease_mapper is None:
            disease_mapper = await create_disease_mapper(
                country_code="CN",  # default to CN
                language=language
            )

        # Apply mapper: local name → standard English name + disease_id
        source_col = "DiseasesCN" if language == "zh" else "Diseases"
        
        df = await disease_mapper.map_dataframe(
            df,
            disease_col=source_col,
        )
        
        logger.debug(f"map_dataframe returned shape: {df.shape}")
        
        # map_dataframe adds disease_id, standard_name_en, standard_name_zh — rename to canonical columns
        if 'standard_name_en' in df.columns:
            df['Diseases'] = df['standard_name_en']
        if 'standard_name_zh' in df.columns:
            df['DiseasesCN'] = df['standard_name_zh']
        
        # Drop rows where disease mapping failed (empty name)
        before_count = len(df)
        df = df[df["Diseases"].notna() & (df["Diseases"] != "")]
        after_count = len(df)
        
        if before_count > after_count:
            logger.warning(
                f"[Normalizer][{self.country_code}] Unmapped rows removed | removed={before_count - after_count}"
            )
        
        logger.debug(f"_normalize_disease_names output shape: {df.shape}")
        return df

    def _slugify(self, text: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
        return safe or "page"

    def _html_to_text(self, html: str) -> str:
        """Extract main text from an HTML page, stripping nav/header/footer elements."""
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove common navigation and layout elements
        for element in soup.find_all(['nav', 'header', 'footer', 'aside', 'script', 'style']):
            element.decompose()

        # Remove elements with common navigation class names
        for element in soup.find_all(class_=lambda x: x and any(
            nav in str(x).lower() for nav in ['nav', 'menu', 'sidebar', 'header', 'footer', 'breadcrumb']
        )):
            element.decompose()

        # Try to find the main content area
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=lambda x: x and 'content' in str(x).lower())
        if main_content:
            soup = main_content
        
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        return cleaned

    async def _save_raw_content(
        self,
        run_id: int,
        raw_dir: Path,
        result: CrawlerResult,
        raw_html: str,
        fetched_at: datetime,
    ) -> None:
        # Extract year from year_month (supports "2025 December", "2025-12", "202512" formats)
        year_str = "unknown"
        if result.year_month:
            import re
            year_match = re.search(r'(20\d{2})', result.year_month)
            if year_match:
                year_str = year_match.group(1)
        
        year_dir = raw_dir / year_str
        year_dir.mkdir(parents=True, exist_ok=True)

        label = result.year_month or result.title or "report"
        plain_text = self._html_to_text(raw_html)
        content_hash = hashlib.sha256(plain_text.encode("utf-8")).hexdigest()
        filename = f"{self._slugify(label)}_{content_hash[:8]}.txt"
        file_path = year_dir / filename

        # Prepend a metadata header to the plain text
        metadata_header = f"""# ========================================
# Raw data file metadata
# ========================================
# URL: {result.url or 'N/A'}
# Title: {result.title or 'N/A'}
# Report period: {result.year_month or 'N/A'}
# Data source: {result.metadata.get('source', 'N/A')}
# Fetched at: {fetched_at.strftime('%Y-%m-%d %H:%M:%S')}
# Content hash: {content_hash}
# DOI: {result.metadata.get('doi', 'N/A')}
# ========================================

"""
        full_content = metadata_header + plain_text
        file_path.write_text(full_content, encoding="utf-8")

        async with get_db() as db:
            db.add(
                CrawlRawPage(
                    run_id=run_id,
                    url=result.url or "",
                    title=result.title,
                    content_path=str(file_path),
                    content_hash=content_hash,
                    content_type="text/plain",
                    fetched_at=fetched_at,
                    source=result.metadata.get("source"),
                    metadata_={
                        "year_month": result.year_month,
                        "has_url": bool(result.url),
                    },
                )
            )
    
    def _calculate_rates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate incidence and mortality rates.

        TODO: pull population figures from the population database.
        Currently rates remain NULL (not -10 sentinel values).
        """
        
        return normalize_rate_columns(df, copy=True)
    
    def _validate_data(self, df: pd.DataFrame) -> bool:
        """Validate data quality of a normalised DataFrame.

        Args:
            df: Normalised disease data.

        Returns:
            True if the data passes all checks, False otherwise.
        """
        if df.empty:
            logger.warning(f"[DataProcessor][{self.country_code}] Validation failed: empty DataFrame")
            return False

        required_columns = ["Date", "YearMonth", "Diseases", "Cases", "Deaths"]
        for col in required_columns:
            if col not in df.columns:
                logger.warning(f"[DataProcessor][{self.country_code}] Validation failed: missing column | col={col!r}")
                return False

        numeric_columns = ["Cases", "Deaths"]
        for col in numeric_columns:
            try:
                df[col] = pd.to_numeric(df[col], errors="coerce")
                nan_ratio = df[col].isna().sum() / len(df)
                if nan_ratio > 0.5:
                    logger.warning(
                        f"[DataProcessor][{self.country_code}] Validation failed: too many NaNs | col={col!r} nan_ratio={nan_ratio:.0%}"
                    )
                    return False
            except Exception as e:
                logger.error(f"[DataProcessor][{self.country_code}] Validation error | col={col!r} error={e}")
                return False

        for col in numeric_columns:
            if (df[col] < 0).any():
                logger.warning(f"[DataProcessor][{self.country_code}] Negative values detected | col={col!r}")

        return True
    
    def _save_to_file(self, df: pd.DataFrame, year_month: str):
        """
        Save a DataFrame to a CSV file.

        Args:
            df:          DataFrame to save.
            year_month:  Period label used for the filename (e.g. "2024 January").
        """
        try:
            filename = f"{year_month}.csv"
            filepath = self.output_dir / filename
            df.to_csv(filepath, index=False, encoding="utf-8-sig")
            logger.info(f"[DataProcessor][{self.country_code}] Saved to file | path={filepath}")

        except Exception as e:
            logger.error(f"[DataProcessor][{self.country_code}] File save failed | error={e}")
    
    async def _save_to_database(self, df: pd.DataFrame, country_code: str) -> None:
        """Write a normalised DataFrame to the database.

        Delegates to RecordStore which uses batch pre-loading + PostgreSQL upsert,
        eliminating N+1 queries and the dedup_deleted undefined-variable risk.
        """
        await self._record_store.save_dataframe(df, country_code)
    
    def merge_data(
        self,
        data_list: List[pd.DataFrame],
        output_file: Optional[Path] = None,
    ) -> pd.DataFrame:
        """
        Merge a list of DataFrames and optionally write to file.

        Args:
            data_list:   List of DataFrames to merge.
            output_file: Optional output CSV path.

        Returns:
            Merged and deduplicated DataFrame.
        """
        if not data_list:
            logger.warning(f"[DataProcessor][{self.country_code}] No data to merge")
            return pd.DataFrame()
        
        try:
            # Concatenate all DataFrames
            merged_df = pd.concat(data_list, ignore_index=True)

            # Sort by date
            merged_df = merged_df.sort_values("Date", ascending=True)

            # Deduplicate
            before_count = len(merged_df)
            merged_df = merged_df.drop_duplicates(
                subset=["Date", "Diseases", "Province"],
                keep="last"
            )
            after_count = len(merged_df)
            
            if before_count > after_count:
                logger.info(f"[DataProcessor][{self.country_code}] Deduplication | removed={before_count - after_count}")

            # Save to file
            if output_file:
                merged_df.to_csv(output_file, index=False, encoding="utf-8-sig")
                logger.info(f"[DataProcessor][{self.country_code}] Merged data saved | path={output_file}")

            return merged_df

        except Exception as e:
            logger.error(f"[DataProcessor][{self.country_code}] Merge failed | error={e}")
            return pd.DataFrame()
    
    def process_single_url(
        self,
        url: str,
        metadata: Optional[Dict] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Convenience method: parse and process a single URL synchronously.

        Args:
            url:      Page URL.
            metadata: Optional crawl metadata.

        Returns:
            Processed DataFrame, or None on failure.
        """
        metadata = metadata or {}
        
        try:
            # Parse
            parse_result = self.parser.parse(url, **metadata)

            if not parse_result.success or not parse_result.has_data:
                logger.error(f"[DataProcessor] Parse failed | error={parse_result.error_message!r}")
                return None

            # Normalise
            df = self._normalize_disease_names(
                parse_result.data,
                language=metadata.get("language", "en"),
            )

            # Calculate rates
            df = self._calculate_rates(df)

            # Validate
            if not self._validate_data(df):
                logger.error(f"[DataProcessor] Validation failed | url={url!r}")
                return None

            return df

        except Exception as e:
            logger.error(f"[DataProcessor] process_single_url failed | url={url!r} error={e}")
            return None
