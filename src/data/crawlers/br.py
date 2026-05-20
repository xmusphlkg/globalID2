"""Brazil SINAN / DATASUS crawler.

SINAN publishes national annual microdata files as DATASUS ``.dbc`` files
under the public DATASUS FTP.  This crawler discovers final and preliminary
files, downloads the requested years, decompresses DBC to DBF, and aggregates
microdata rows to the project's national monthly grain.
"""

from __future__ import annotations

import csv
import json
import hashlib
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Brazil DATASUS SINAN Open Data"
DEFAULT_FINAL_URL = "ftp://ftp.datasus.gov.br/dissemin/publicos/SINAN/DADOS/FINAIS/"
DEFAULT_PRELIM_URL = "ftp://ftp.datasus.gov.br/dissemin/publicos/SINAN/DADOS/PRELIM/"
DEFAULT_HISTORY_START_YEAR = 2000
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "br" / "sinan_monthly_aggregates"


SINAN_DISEASE_PREFIXES: Dict[str, str] = {
    "ACBI": "Work accident with biological material",
    "ACGR": "Serious work accident",
    "AIDA": "AIDS in adults",
    "AIDC": "AIDS in children",
    "ANIM": "Accident caused by venomous animals",
    "ANTR": "Rabies post-exposure prophylaxis",
    "BOTU": "Botulism",
    "CANC": "Work-related cancer",
    "CHAG": "Acute Chagas disease",
    "CHIK": "Chikungunya fever",
    "COLE": "Cholera",
    "COQU": "Pertussis",
    "DCRJ": "Creutzfeldt-Jakob disease",
    "DENG": "Dengue",
    "DERM": "Work-related dermatosis",
    "DIFT": "Diphtheria",
    "ESPO": "Sporotrichosis",
    "ESQU": "Schistosomiasis",
    "EXAN": "Exanthematous diseases",
    "FMAC": "Spotted fever",
    "FTIF": "Typhoid fever",
    "HANS": "Leprosy",
    "HANT": "Hantavirus disease",
    "HEPA": "Viral hepatitis",
    "HIVA": "HIV infection in adults",
    "HIVC": "HIV infection in children",
    "HIVE": "HIV infection in pregnant women",
    "HIVG": "HIV infection",
    "IEXO": "Exogenous poisoning",
    "LEIV": "Visceral leishmaniasis",
    "LEPT": "Leptospirosis",
    "LER": "Work-related repetitive strain injury",
    "LERD": "Work-related repetitive strain injury",
    "LTAN": "American tegumentary leishmaniasis",
    "MALA": "Malaria",
    "MENI": "Meningitis",
    "MENT": "Work-related mental disorder",
    "NTRA": "Work-related disorder",
    "PAIR": "Noise-induced hearing loss",
    "PEST": "Plague",
    "PFAN": "Acute flaccid paralysis",
    "PNEU": "Pneumoconiosis",
    "RAIV": "Rabies",
    "ROTA": "Rotavirus",
    "SDTA": "Foodborne disease outbreak",
    "SIFA": "Acquired syphilis",
    "SIFC": "Congenital syphilis",
    "SIFG": "Syphilis in pregnancy",
    "SRC": "Congenital rubella syndrome",
    "TETA": "Tetanus",
    "TETN": "Neonatal tetanus",
    "TOXC": "Congenital toxoplasmosis",
    "TOXG": "Toxoplasmosis in pregnancy",
    "TRAC": "Trachoma",
    "TUBE": "Tuberculosis",
    "VARC": "Varicella",
    "VIOL": "Domestic/sexual/other violence",
    "ZIKA": "Zika virus disease",
}

DEFAULT_PREFIXES = sorted(SINAN_DISEASE_PREFIXES)


@dataclass(frozen=True)
class SINANFile:
    prefix: str
    disease_name: str
    year: int
    filename: str
    url: str
    dataset_status: str
    size_bytes: int
    modified_at: Optional[datetime]


@dataclass
class BRFetchSummary:
    row_count: int
    latest_date: Optional[date]
    files_fetched: int
    source_url: str
    rows: List[Dict[str, str]] = field(default_factory=list)


def _two_digit_year_to_full(value: str) -> int:
    year = int(value)
    return 1900 + year if year >= 90 else 2000 + year


def _last_n_months(count: int = 3) -> Set[Tuple[int, int]]:
    now = datetime.now()
    months: Set[Tuple[int, int]] = set()
    for delta in range(max(1, count)):
        month = now.month - delta
        year = now.year
        if month <= 0:
            month += 12
            year -= 1
        months.add((year, month))
    return months


def _parse_ftp_listing_line(
    line: str,
    *,
    base_url: str,
    dataset_status: str,
) -> Optional[SINANFile]:
    match = re.search(
        r"(?P<month>\d{2})-(?P<day>\d{2})-(?P<mod_year>\d{2})\s+"
        r"(?P<time>\d{2}:\d{2})(?P<ampm>AM|PM)\s+"
        r"(?P<size>\d+)\s+"
        r"(?P<prefix>[A-Z]+)BR(?P<year>\d{2})\.dbc",
        line.strip(),
    )
    if not match:
        return None

    prefix = match.group("prefix").upper()
    filename = f"{prefix}BR{match.group('year')}.dbc"
    file_year = _two_digit_year_to_full(match.group("year"))
    modified_at = None
    try:
        modified_at = datetime.strptime(
            (
                f"{match.group('month')}-{match.group('day')}-"
                f"{match.group('mod_year')} {match.group('time')}{match.group('ampm')}"
            ),
            "%m-%d-%y %I:%M%p",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    return SINANFile(
        prefix=prefix,
        disease_name=SINAN_DISEASE_PREFIXES.get(prefix, prefix),
        year=file_year,
        filename=filename,
        url=urljoin(base_url, filename),
        dataset_status=dataset_status,
        size_bytes=int(match.group("size")),
        modified_at=modified_at,
    )


def parse_ftp_listing(
    listing_text: str,
    *,
    base_url: str,
    dataset_status: str,
) -> List[SINANFile]:
    """Parse DATASUS FTP directory listing text into file metadata."""
    files: List[SINANFile] = []
    for line in listing_text.splitlines():
        parsed = _parse_ftp_listing_line(
            line,
            base_url=base_url,
            dataset_status=dataset_status,
        )
        if parsed is not None:
            files.append(parsed)
    return files


def _parse_record_date(value: object) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _select_record_date(record: Dict[str, object], fallback_year: int) -> date:
    """Choose the monthly bucket date for one SINAN microdata record."""
    for field in ("DT_NOTIFIC", "DT_SIN_PRI", "DT_DIAG", "DT_ACID"):
        parsed = _parse_record_date(record.get(field))
        if parsed is not None:
            return date(parsed.year, parsed.month, 1)

    year_text = str(record.get("NU_ANO") or fallback_year).strip()
    try:
        year = int(float(year_text))
    except ValueError:
        year = fallback_year
    return date(year, 1, 1)


def _load_dbf_records(dbf_path: Path) -> Iterable[Dict[str, object]]:
    from dbfread import DBF

    table = DBF(str(dbf_path), encoding="latin1", char_decode_errors="ignore", load=False)
    for record in table:
        yield dict(record)


class BrazilSINANCrawler(BaseCrawler):
    """Crawler for Brazil DATASUS SINAN annual DBC microdata files."""

    SOURCE_URL = DEFAULT_FINAL_URL

    def __init__(
        self,
        *,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
    ) -> None:
        cfg = get_country_bootstrap_config("BR")
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
        timeout_seconds = int(crawler_cfg.get("timeout_seconds") or 180)
        max_retries = max(1, int(crawler_cfg.get("max_retries") or 3))
        max_workers = max(1, int(crawler_cfg.get("max_workers") or 4))
        request_delay = float(crawler_cfg.get("request_delay_seconds") or crawler_cfg.get("request_delay") or 0.2)

        super().__init__(
            user_agent="GlobalID/2.0 (Brazil SINAN DATASUS)",
            timeout=timeout_seconds,
            max_retries=max_retries,
            delay=request_delay,
        )
        self.final_url = str(crawler_cfg.get("final_ftp_url") or DEFAULT_FINAL_URL)
        self.prelim_url = str(crawler_cfg.get("prelim_ftp_url") or DEFAULT_PRELIM_URL)
        configured_prefixes = crawler_cfg.get("default_prefixes") or DEFAULT_PREFIXES
        self.default_prefixes = sorted({str(p).upper() for p in configured_prefixes})
        self.full_history_start_year = int(
            crawler_cfg.get("full_history_start_year") or DEFAULT_HISTORY_START_YEAR
        )
        self.refresh_recent_months = int(crawler_cfg.get("refresh_recent_months") or 3)
        self.max_workers = max_workers
        self.request_delay_seconds = request_delay
        self.request_retries = max_retries
        self.file_index_ttl_seconds = int(
            crawler_cfg.get("file_index_cache_ttl_seconds") or crawler_cfg.get("cache_ttl_seconds") or 3600
        )
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/br")
        self.cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self._file_index: Optional[List[SINANFile]] = None

    def _history_months(self, *, start_year: Optional[int] = None, end_date: Optional[date] = None) -> List[Tuple[int, int]]:
        upper = end_date or datetime.now().date()
        start = int(start_year or self.full_history_start_year)
        if start > upper.year:
            start = upper.year

        months: List[Tuple[int, int]] = []
        for year in range(start, upper.year + 1):
            last_month = 12 if year < upper.year else upper.month
            for month in range(1, last_month + 1):
                months.append((year, month))
        return months

    def _file_cache_path(self, item: SINANFile) -> Path:
        signature = self._file_cache_signature(item)
        safe_filename = f"{signature}.json"
        return self.cache_dir / item.dataset_status / safe_filename

    def _file_cache_signature(self, item: SINANFile) -> str:
        sig = "|".join([
            item.filename,
            item.dataset_status,
            str(item.size_bytes),
            item.modified_at.isoformat() if item.modified_at else "none",
        ])
        return hashlib.sha1(sig.encode("utf-8")).hexdigest()

    def _load_cached_counts(
        self,
        item: SINANFile,
        months: Optional[Set[Tuple[int, int]]] = None,
    ) -> Optional[Dict[Tuple[int, int], int]]:
        cache_path = self._file_cache_path(item)
        if not cache_path.exists():
            return None
        try:
            with cache_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return None
        if payload.get("signature") != self._file_cache_signature(item):
            return None

        rows = payload.get("rows")
        if not isinstance(rows, list):
            return None

        counts: Dict[Tuple[int, int], int] = {}
        for row in rows:
            try:
                date_text = str(row.get("Date"))
                row_months = datetime.strptime(date_text, "%Y-%m-%d").date()
            except Exception:
                continue
            try:
                counts[(row_months.year, row_months.month)] = int(row.get("Cases", 0))
            except (TypeError, ValueError):
                continue

        cached_scope = payload.get("scope")
        if months is None:
            return counts if cached_scope == "full" else None

        if cached_scope == "full":
            return {key: counts[key] for key in counts.keys() if key in months}

        cached_months: Optional[Set[Tuple[int, int]]] = None
        if isinstance(cached_scope, list):
            cached_months = set()
            for token in cached_scope:
                if not isinstance(token, str) or len(token) != 7 or token[4] != "-":
                    continue
                try:
                    cached_months.add((int(token[:4]), int(token[5:])))
                except ValueError:
                    continue
        elif counts:
            # Legacy cache format or malformed scope metadata: treat as partial coverage.
            cached_months = set(counts.keys())

        if cached_months is not None and months.issubset(cached_months):
            return {key: counts[key] for key in counts.keys() if key in months}
        return None

    def _write_cached_counts(
        self,
        item: SINANFile,
        counts: Dict[Tuple[int, int], int],
        months: Optional[Set[Tuple[int, int]]] = None,
    ) -> None:
        cache_path = self._file_cache_path(item)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Persist all parsed months for the file as a full cache.
        # The parser always materializes every record for the selected source file,
        # so a full cache avoids reparsing this file on subsequent incremental fetches
        # as long as the underlying file signature stays unchanged.
        cache_scope = "full"
        payload = {
            "signature": self._file_cache_signature(item),
            "file": item.filename,
            "prefix": item.prefix,
            "year": item.year,
            "dataset_status": item.dataset_status,
            "scope": cache_scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [
                {"Date": date(year, month, 1).isoformat(), "Cases": count}
                for (year, month), count in sorted(counts.items())
            ],
        }
        with cache_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

    def _request_with_retries(self, url: str) -> bytes:
        last_error = None
        for attempt in range(1, self.request_retries + 1):
            try:
                if attempt > 1:
                    backoff = 1.0 + (attempt - 1) * 0.25
                    delay = self.request_delay_seconds + backoff
                    logger.warning(
                        f"[BR-SINAN] retrying request | url={url} attempt={attempt}/{self.request_retries} "
                        f"backoff={delay:.1f}s"
                    )
                    time.sleep(delay)
                else:
                    time.sleep(self.request_delay_seconds)

                with urlopen(url, timeout=self.timeout) as response:
                    return response.read()
            except (HTTPError, URLError, OSError, TimeoutError) as exc:
                last_error = exc
            except Exception as exc:  # pragma: no cover - defensive; keep behavior unchanged for unknown transport errors
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"unknown request error for {url}")

    def _fetch_listing_text(self, url: str) -> str:
        payload = self._request_with_retries(url)
        return payload.decode("latin1", errors="replace")

    def _file_index_cache_path(self) -> Path:
        return self.cache_dir / "index.json"

    def _read_cached_file_index(self) -> Optional[List[SINANFile]]:
        cache_path = self._file_index_cache_path()
        if not cache_path.exists():
            return None
        if time.time() - cache_path.stat().st_mtime > self.file_index_ttl_seconds:
            return None

        try:
            with cache_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return None

        raw_files = payload.get("files")
        if not isinstance(raw_files, list):
            return None

        result: List[SINANFile] = []
        for row in raw_files:
            try:
                modified_at = row.get("modified_at")
                parsed_modified = (
                    datetime.fromisoformat(modified_at)
                    if isinstance(modified_at, str)
                    else None
                )
                result.append(
                    SINANFile(
                        prefix=str(row["prefix"]),
                        disease_name=str(row.get("disease_name", row["prefix"])),
                        year=int(row["year"]),
                        filename=str(row["filename"]),
                        url=str(row["url"]),
                        dataset_status=str(row["dataset_status"]),
                        size_bytes=int(row["size_bytes"]),
                        modified_at=parsed_modified,
                    )
                )
            except Exception:
                continue
        return result if result else None

    def _write_cached_file_index(self, files: List[SINANFile]) -> None:
        cache_path = self._file_index_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": [
                {
                    "prefix": item.prefix,
                    "disease_name": item.disease_name,
                    "year": item.year,
                    "filename": item.filename,
                    "url": item.url,
                    "dataset_status": item.dataset_status,
                    "size_bytes": item.size_bytes,
                    "modified_at": item.modified_at.isoformat() if item.modified_at else None,
                }
                for item in files
            ],
        }
        with cache_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

    def fetch_file_index(self) -> List[SINANFile]:
        """Fetch final and preliminary SINAN directory listings."""
        if self._file_index is not None:
            return list(self._file_index)

        cached_index = self._read_cached_file_index()
        if cached_index is not None:
            self._file_index = sorted(
                cached_index,
                key=lambda item: (item.prefix, item.year),
            )
            logger.info(
                f"[BR-SINAN] Index loaded from cache | files={len(self._file_index)} ttl={self.file_index_ttl_seconds}s"
            )
            return list(self._file_index)

        all_files: List[SINANFile] = []
        for url, status in (
            (self.final_url, "final"),
            (self.prelim_url, "preliminary"),
        ):
            try:
                listing_text = self._fetch_listing_text(url)
            except Exception as exc:
                logger.warning(f"[BR-SINAN] listing fetch failed | url={url} error={exc}")
                continue
            all_files.extend(
                parse_ftp_listing(listing_text, base_url=url, dataset_status=status)
            )

        # Prefer final files when the same prefix/year is present in both folders.
        best: Dict[Tuple[str, int], SINANFile] = {}
        for item in all_files:
            key = (item.prefix, item.year)
            existing = best.get(key)
            if existing is None or (
                existing.dataset_status == "preliminary"
                and item.dataset_status == "final"
            ):
                best[key] = item

        files = sorted(best.values(), key=lambda item: (item.prefix, item.year))
        logger.info(f"[BR-SINAN] Index complete | files={len(files)}")
        self._file_index = files
        self._write_cached_file_index(files)
        return files

    def _download_file(self, sinan_file: SINANFile, target_dir: Path) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / sinan_file.filename
        if target_path.exists() and target_path.stat().st_size == sinan_file.size_bytes:
            return target_path

        tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

        payload = self._request_with_retries(sinan_file.url)
        with tmp_path.open("wb") as handle:
            handle.write(payload)
        tmp_path.replace(target_path)
        return target_path

    @staticmethod
    def _decompress_to_dbf(dbc_path: Path, dbf_path: Path) -> None:
        import datasus_dbc

        datasus_dbc.decompress(str(dbc_path), str(dbf_path))

    def aggregate_file(
        self,
        sinan_file: SINANFile,
        *,
        months: Optional[Set[Tuple[int, int]]] = None,
        working_dir: Optional[Path] = None,
    ) -> List[Dict[str, str]]:
        """Download, decompress, and aggregate one SINAN file to monthly rows."""
        cached = self._load_cached_counts(sinan_file, months=months)
        if cached is not None:
            logger.info(
                f"[BR-SINAN] cache hit | file={sinan_file.filename} "
                f"months={len(cached)}"
            )
            monthly_counts = defaultdict(int, cached)
        else:
            local_raw_dir = self.raw_dir / sinan_file.dataset_status / str(sinan_file.year)
            if self.save_raw:
                dbc_path = self._download_file(sinan_file, local_raw_dir)
            else:
                scratch = Path(tempfile.mkdtemp(prefix="globalid_br_sinan_"))
                dbc_path = self._download_file(sinan_file, scratch)

            dbf_parent = Path(working_dir) if working_dir is not None else dbc_path.parent
            dbf_parent.mkdir(parents=True, exist_ok=True)
            dbf_path = dbf_parent / f"{dbc_path.stem}.dbf"
            try:
                self._decompress_to_dbf(dbc_path, dbf_path)
                monthly_counts: Dict[Tuple[int, int], int] = defaultdict(int)
                for record in _load_dbf_records(dbf_path):
                    bucket_date = _select_record_date(record, sinan_file.year)
                    bucket = (bucket_date.year, bucket_date.month)
                    if months is not None and bucket not in months:
                        continue
                    monthly_counts[bucket] += 1
            finally:
                if dbf_path.exists():
                    dbf_path.unlink()
                if not self.save_raw:
                    shutil.rmtree(dbc_path.parent, ignore_errors=True)

            self._write_cached_counts(sinan_file, monthly_counts, months=months)

        rows: List[Dict[str, str]] = []
        for year, month in sorted(monthly_counts):
            rows.append(
                {
                    "Date": date(year, month, 1).isoformat(),
                    "RawDiseaseLabel": sinan_file.disease_name,
                    "DiseaseCode": sinan_file.prefix,
                    "Year": str(year),
                    "Month": str(month),
                    "Cases": str(monthly_counts[(year, month)]),
                    "DatasetYear": str(sinan_file.year),
                    "DatasetStatus": sinan_file.dataset_status,
                    "SourceFile": sinan_file.filename,
                    "Source": DEFAULT_SOURCE_NAME,
                    "SourceURL": sinan_file.url,
                }
            )
        return rows

    def crawl_monthly_national(
        self,
        output_csv: Path,
        *,
        months: Optional[List[Tuple[int, int]]] = None,
        prefixes: Optional[List[str]] = None,
        write_csv: bool = True,
        file_index: Optional[List[SINANFile]] = None,
    ) -> BRFetchSummary:
        target_months = (
            set(months) if months is not None else _last_n_months(self.refresh_recent_months)
        )
        target_years = {year for year, _month in target_months}
        requested_prefixes = {
            str(prefix).strip().upper()
            for prefix in (prefixes if prefixes is not None else self.default_prefixes)
            if str(prefix).strip()
        }

        index = list(file_index) if file_index is not None else self.fetch_file_index()
        candidate_files = [
            item
            for item in index
            if item.prefix in requested_prefixes and item.year in target_years
        ]
        if not candidate_files:
            raise RuntimeError("[BR-SINAN] No SINAN DBC files matched requested months/prefixes")

        all_rows: List[Dict[str, str]] = []
        failed_files = 0
        logger.info(
            f"[BR-SINAN] Aggregation start | files={len(candidate_files)} workers={self.max_workers} "
            f"months={len(target_months)}"
        )
        with tempfile.TemporaryDirectory(prefix="globalid_br_sinan_dbf_") as tmp_dir:
            working_dir = Path(tmp_dir)
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                pending = {
                    pool.submit(
                        self.aggregate_file,
                        item,
                        months=target_months,
                        working_dir=working_dir,
                    ): item.filename
                    for item in candidate_files
                }
                completed = 0
                for future in as_completed(pending):
                    item_name = pending[future]
                    completed += 1
                    try:
                        all_rows.extend(future.result())
                    except Exception as exc:
                        failed_files += 1
                        logger.warning(f"[BR-SINAN] file skipped | file={item_name} error={exc}")
                    if completed % 10 == 0 or completed == len(candidate_files):
                        logger.info(
                            f"[BR-SINAN] Aggregation progress | completed={completed}/{len(candidate_files)} "
                            f"rows={len(all_rows)} failed={failed_files}"
                        )

        if not all_rows:
            raise RuntimeError("[BR-SINAN] No national monthly rows parsed from SINAN files")

        # Coalesce final/preliminary rows if multiple files produce the same bucket.
        grouped: Dict[Tuple[str, str], Dict[str, object]] = {}
        for row in all_rows:
            key = (row["Date"], row["DiseaseCode"])
            bucket = grouped.setdefault(
                key,
                {
                    "Date": row["Date"],
                    "RawDiseaseLabel": row["RawDiseaseLabel"],
                    "DiseaseCode": row["DiseaseCode"],
                    "Year": row["Year"],
                    "Month": row["Month"],
                    "Cases": 0,
                    "DatasetStatuses": set(),
                    "SourceFiles": [],
                    "SourceURLs": [],
                },
            )
            bucket["Cases"] = int(bucket["Cases"]) + int(row["Cases"])
            bucket["DatasetStatuses"].add(row["DatasetStatus"])
            bucket["SourceFiles"].append(row["SourceFile"])
            bucket["SourceURLs"].append(row["SourceURL"])

        output_rows: List[Dict[str, str]] = []
        for bucket in grouped.values():
            output_rows.append(
                {
                    "Date": str(bucket["Date"]),
                    "RawDiseaseLabel": str(bucket["RawDiseaseLabel"]),
                    "DiseaseCode": str(bucket["DiseaseCode"]),
                    "Year": str(bucket["Year"]),
                    "Month": str(bucket["Month"]),
                    "Cases": str(bucket["Cases"]),
                    "DatasetStatus": "|".join(sorted(bucket["DatasetStatuses"])),
                    "SourceFiles": "|".join(bucket["SourceFiles"]),
                    "SourceURLs": "|".join(bucket["SourceURLs"]),
                    "Source": DEFAULT_SOURCE_NAME,
                }
            )

        output_rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        if write_csv:
            output_csv.parent.mkdir(parents=True, exist_ok=True)
            fieldnames = [
                "",
                "Disease",
                "DiseaseCode",
                "Year",
                "Month",
                "Date",
                "Cases",
                "DatasetStatus",
                "SourceFiles",
                "SourceURLs",
                "Source",
            ]
            with output_csv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for idx, row in enumerate(output_rows, start=1):
                    writer.writerow(
                        {
                            "": str(idx),
                            "Disease": row["RawDiseaseLabel"],
                            "DiseaseCode": row["DiseaseCode"],
                            "Year": row["Year"],
                            "Month": row["Month"],
                            "Date": row["Date"],
                            "Cases": row["Cases"],
                            "DatasetStatus": row["DatasetStatus"],
                            "SourceFiles": row["SourceFiles"],
                            "SourceURLs": row["SourceURLs"],
                            "Source": row["Source"],
                        }
                    )

        latest_date = max(
            (datetime.strptime(row["Date"], "%Y-%m-%d").date() for row in output_rows),
            default=None,
        )
        logger.info(
            f"[BR-SINAN] {'CSV written' if write_csv else 'Aggregation done'} | path={output_csv} "
            f"rows={len(output_rows)} files={len(candidate_files)} latest={latest_date}"
        )
        return BRFetchSummary(
            row_count=len(output_rows),
            latest_date=latest_date,
            files_fetched=len(candidate_files),
            source_url=self.final_url,
            rows=output_rows,
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        output_csv = Path(kwargs.get("output_csv") or "data/current/br/brazil_national_monthly.csv")
        months = kwargs.get("months")
        prefixes = kwargs.get("prefixes")
        summary = self.crawl_monthly_national(output_csv, months=months, prefixes=prefixes)
        return [
            CrawlerResult(
                title="Brazil DATASUS SINAN national monthly open data",
                url=self.final_url,
                date=datetime.now(timezone.utc),
                metadata={
                    "source": "sinan_datasus",
                    "country_code": "BR",
                    "row_count": summary.row_count,
                    "latest_date": summary.latest_date.isoformat()
                    if summary.latest_date
                    else None,
                    "files_fetched": summary.files_fetched,
                },
            )
        ]

    def parse(self, response: Any) -> List[CrawlerResult]:
        """BaseCrawler contract; parsing is integrated in ``crawl_monthly_national``."""
        return []
