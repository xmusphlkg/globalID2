"""Switzerland FOPH IDD infectious disease crawler.

The Federal Office of Public Health (FOPH/BAG) Infectious Diseases Dashboard
exposes a REST API under ``https://www.idd.bag.admin.ch/api/v1``.  For GlobalID
we use the mandatory reporting ``cases/value`` series and normalize each disease
to a national row at the best published grain:

* monthly where available,
* ISO week for Covid-19 and influenza,
* yearly for diseases that only expose annual totals.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Switzerland FOPH IDD Mandatory Reporting System"
DEFAULT_PORTAL_URL = "https://www.idd.bag.admin.ch/en/portal-data"
DEFAULT_API_BASE_URL = "https://www.idd.bag.admin.ch/api/v1"
DEFAULT_HISTORY_START_YEAR = 2013
DEFAULT_REFRESH_RECENT_MONTHS = 6
DEFAULT_REFRESH_RECENT_WEEKS = 12
DEFAULT_REFRESH_RECENT_YEARS = 2

CASE_VALUE_TOPIC = "cases"
CASE_VALUE_METRIC = "value"
SUPPORTED_PERIODS = {"month", "iso_week", "year"}
PERIOD_PRIORITY = {"year": 1, "iso_week": 2, "month": 3}

DISEASE_DISPLAY_NAMES: dict[str, str] = {
    "aids": "AIDS",
    "botulism": "Botulism",
    "brucellosis": "Brucellosis",
    "campylobacteriosis": "Campylobacteriosis",
    "chikungunya": "Chikungunya",
    "chlamydiosis": "Chlamydia",
    "cholera": "Cholera",
    "cjd": "Creutzfeldt-Jakob disease",
    "covid19": "SARS-CoV-2 (Covid-19)",
    "dengueFever": "Dengue fever",
    "diphtheria": "Diphtheria",
    "ehec": "EHEC",
    "gonorrhea": "Gonorrhoea",
    "haemophilusInfluenzae": "Haemophilus influenzae disease",
    "hanta": "Hantavirus infection",
    "hepatitis_a": "Hepatitis A",
    "hepatitis_b": "Hepatitis B",
    "hepatitis_c": "Hepatitis C",
    "hepatitis_e": "Hepatitis E",
    "hiv": "Human Immunodeficiency Virus (HIV)",
    "influenza": "Influenza (Seasonal flu)",
    "ipd": "Invasive pneumococcal disease",
    "legionellosis": "Legionellosis",
    "listeriosis": "Listeriosis",
    "lyme_borreliosis": "Lyme disease",
    "malaria": "Malaria",
    "measles": "Measles",
    "meningo": "Meningococcal disease",
    "monkeypox": "Mpox",
    "qFever": "Q fever",
    "rubella": "Rubella",
    "salmonellosis": "Salmonellosis",
    "shigellosis": "Shigellosis",
    "syphilis": "Syphilis",
    "tetanus": "Tetanus",
    "tick-borne_encephalitis": "Tick-borne encephalitis (TBE)",
    "trichinellosis": "Trichinellosis",
    "tuberculosis": "Tuberculosis",
    "tularemia": "Tularaemia",
    "typhoidParatyphoidFever": "Typhoid and paratyphoid fever",
    "westnileFever": "West Nile fever",
    "yellowFever": "Yellow fever",
    "zika": "Zika virus infection",
}


@dataclass(frozen=True)
class CHSeriesSource:
    slug: str
    display_name: str
    identifier: str
    period_type: str
    config: Dict[str, str]
    geography: str
    source_date: Optional[str]


@dataclass
class CHFetchSummary:
    row_count: int
    latest_date: Optional[date]
    series_fetched: int
    source_url: str
    version: Optional[str]


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _parse_number(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = _norm_text(value).replace(",", "")
    if not text or text in {"-", "—", "N/A", "na", "null", "None"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_idd_period(value: object, period_type: str) -> Optional[date]:
    """Parse IDD period values such as ``201301``, ``202009``, or ``2025``."""
    parsed = _parse_number(value)
    if parsed is None:
        return None

    if period_type == "month":
        year = parsed // 100
        month = parsed % 100
        if 1900 <= year <= 2100 and 1 <= month <= 12:
            return date(year, month, 1)
        return None

    if period_type == "iso_week":
        year = parsed // 100
        week = parsed % 100
        if not (1900 <= year <= 2100 and 1 <= week <= 53):
            return None
        try:
            return date.fromisocalendar(year, week, 1)
        except ValueError:
            return None

    if period_type == "year":
        if 1900 <= parsed <= 2100:
            return date(parsed, 1, 1)
        return None

    return None


def _period_value_text(value: object) -> str:
    text = _norm_text(value)
    if text:
        return text
    parsed = _parse_number(value)
    return "" if parsed is None else str(parsed)


def _display_name_for_slug(slug: str) -> str:
    return DISEASE_DISPLAY_NAMES.get(slug) or slug.replace("_", " ").replace("-", " ").title()


def _is_demographic_total(config: Dict[str, str]) -> bool:
    for key, value in config.items():
        if key == "sex" and value != "all":
            return False
        if key == "type" and value != "all":
            return False
        if key == "testResult" and value != "all":
            return False
        if key.startswith("agegroup_") and value != "all":
            return False
        if key == "all" and value not in {"positive", "all"}:
            return False
    return True


def _geography_score(config: Dict[str, str]) -> tuple[int, str]:
    if config.get("georegion") == "country" and config.get("country") == "CH":
        return 100, "CH"
    if config.get("georegion") == "CHFL" and config.get("CHFL") == "CHFL":
        return 80, "CHFL"
    if config.get("country") == "CH":
        return 70, "CH"
    if config.get("CHFL") == "CHFL":
        return 60, "CHFL"
    return 0, ""


def choose_national_series_config(configs: Sequence[Dict[str, str]]) -> tuple[Dict[str, str], str]:
    """Choose the best Switzerland national configuration from IDD details."""
    scored: List[tuple[int, Dict[str, str], str]] = []
    for config in configs:
        geo_score, geography = _geography_score(config)
        if geo_score <= 0:
            continue
        demographic_score = 20 if _is_demographic_total(config) else -50
        compactness_score = -len(config)
        scored.append((geo_score + demographic_score + compactness_score, config, geography))

    if not scored:
        raise ValueError("No Switzerland national series configuration found")

    scored.sort(key=lambda item: item[0], reverse=True)
    _, config, geography = scored[0]
    return dict(config), geography


def _flatten_values(values: object) -> Iterable[tuple[Optional[str], Dict[str, Any]]]:
    if isinstance(values, list):
        for item in values:
            if isinstance(item, dict):
                yield None, item
        return

    if isinstance(values, dict):
        for group, group_values in values.items():
            if isinstance(group_values, list):
                for item in group_values:
                    if isinstance(item, dict):
                        yield str(group), item


class SwitzerlandIDDCrawler(BaseCrawler):
    """Crawler for Switzerland FOPH/BAG IDD national case series."""

    SOURCE_URL = DEFAULT_PORTAL_URL

    def __init__(
        self,
        *,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; CH-IDD)",
            timeout=120,
            max_retries=3,
            delay=0.15,
        )
        cfg = get_country_bootstrap_config("CH")
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
        self.portal_url = str(crawler_cfg.get("portal_url") or DEFAULT_PORTAL_URL)
        self.api_base_url = str(crawler_cfg.get("api_base_url") or DEFAULT_API_BASE_URL).rstrip("/")
        self.refresh_recent_months = int(
            crawler_cfg.get("refresh_recent_months") or DEFAULT_REFRESH_RECENT_MONTHS
        )
        self.refresh_recent_weeks = int(
            crawler_cfg.get("refresh_recent_weeks") or DEFAULT_REFRESH_RECENT_WEEKS
        )
        self.refresh_recent_years = int(
            crawler_cfg.get("refresh_recent_years") or DEFAULT_REFRESH_RECENT_YEARS
        )
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/ch")

    def _api_url(self, path: str) -> str:
        return f"{self.api_base_url}/{path.lstrip('/')}"

    def fetch_version(self) -> Optional[str]:
        response = self.get(self._api_url("data/version"))
        payload = response.json()
        return _norm_text(payload.get("name")) if isinstance(payload, dict) else None

    def fetch_sets(self) -> List[str]:
        response = self.get(self._api_url("data/sets"))
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("CH IDD data/sets response is not a list")
        return [str(item) for item in payload]

    def _candidate_identifiers(self) -> Dict[str, str]:
        by_slug: Dict[str, tuple[int, str]] = {}
        for identifier in self.fetch_sets():
            parts = identifier.split("/")
            if len(parts) != 4:
                continue
            slug, topic, metric, period_type = parts
            if topic != CASE_VALUE_TOPIC or metric != CASE_VALUE_METRIC:
                continue
            if period_type not in SUPPORTED_PERIODS:
                continue

            priority = PERIOD_PRIORITY[period_type]
            current = by_slug.get(slug)
            if current is None or priority > current[0]:
                by_slug[slug] = (priority, identifier)

        return {slug: identifier for slug, (_, identifier) in by_slug.items()}

    def fetch_series_sources(
        self,
        *,
        disease_slugs: Optional[Set[str]] = None,
    ) -> List[CHSeriesSource]:
        """Discover published disease case series and their national configs."""
        version = self.fetch_version()
        sources: List[CHSeriesSource] = []
        for slug, identifier in sorted(self._candidate_identifiers().items()):
            if disease_slugs and slug not in disease_slugs:
                continue
            details_url = self._api_url(f"data/{identifier}/details")
            details = self.get(details_url).json()
            configs = details.get("availableSeriesConfigurations") if isinstance(details, dict) else None
            if not isinstance(configs, list):
                logger.warning(f"[CH-IDD] Missing series configs | identifier={identifier}")
                continue
            try:
                config, geography = choose_national_series_config(configs)
            except ValueError as exc:
                logger.warning(f"[CH-IDD] Skipping series | identifier={identifier} error={exc}")
                continue

            source_date = (
                _norm_text(details.get("sourceDate"))
                if isinstance(details, dict)
                else None
            )
            sources.append(
                CHSeriesSource(
                    slug=slug,
                    display_name=_display_name_for_slug(slug),
                    identifier=identifier,
                    period_type=identifier.split("/")[-1],
                    config=config,
                    geography=geography,
                    source_date=source_date,
                )
            )

            if self.save_raw:
                self._save_raw_json(
                    ["details", f"{slug}_{identifier.split('/')[-1]}.json"],
                    {"version": version, "details": details, "selected_config": config},
                )

        logger.info(f"[CH-IDD] Series discovery complete | series={len(sources)} version={version}")
        return sources

    def _fetch_one_series(self, series: CHSeriesSource) -> tuple[List[Dict[str, str]], Optional[str]]:
        url = self._api_url(f"data/{series.identifier}")
        response = self.post(url, json=series.config)
        payload = response.json()
        version = _norm_text(payload.get("version")) if isinstance(payload, dict) else None
        values = payload.get("values") if isinstance(payload, dict) else None
        rows: List[Dict[str, str]] = []

        for group, item in _flatten_values(values):
            period_date = parse_idd_period(item.get("x"), series.period_type)
            cases = _parse_number(item.get("y"))
            if period_date is None or cases is None:
                continue
            properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
            iso_week = ""
            if series.period_type == "iso_week":
                iso = period_date.isocalendar()
                iso_week = f"{iso.year}-W{iso.week:02d}"
            rows.append(
                {
                    "Date": period_date.isoformat(),
                    "RawDiseaseLabel": series.display_name,
                    "DiseaseCode": series.slug,
                    "Year": str(period_date.year),
                    "Month": str(period_date.month),
                    "ISOWeek": iso_week,
                    "PeriodType": series.period_type,
                    "PeriodValue": _period_value_text(item.get("x")),
                    "Cases": str(max(0, cases)),
                    "Geography": series.geography,
                    "Group": group or "",
                    "DataComplete": _norm_text(properties.get("dataComplete")),
                    "Trend": _norm_text(properties.get("trend")),
                    "SourceDate": _norm_text(payload.get("sourceDate") or series.source_date)
                    if isinstance(payload, dict)
                    else series.source_date or "",
                    "Version": version,
                    "Source": DEFAULT_SOURCE_NAME,
                    "SourceURL": url,
                }
            )

        if self.save_raw:
            self._save_raw_json(
                ["data", f"{series.slug}_{series.period_type}.json"],
                {"request": series.config, "response": payload},
            )

        return rows, version

    def _save_raw_json(self, parts: Sequence[str], payload: Dict[str, Any]) -> None:
        path = self.raw_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _latest_dates_by_period(rows: List[Dict[str, str]]) -> dict[str, List[date]]:
        by_period: dict[str, Set[date]] = {}
        for row in rows:
            try:
                parsed = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            by_period.setdefault(row.get("PeriodType", ""), set()).add(parsed)
        return {period: sorted(values) for period, values in by_period.items()}

    def _filter_recent_rows(self, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
        by_period = self._latest_dates_by_period(rows)
        keep: Set[date] = set()
        keep.update(by_period.get("month", [])[-max(1, self.refresh_recent_months):])
        keep.update(by_period.get("iso_week", [])[-max(1, self.refresh_recent_weeks):])
        keep.update(by_period.get("year", [])[-max(1, self.refresh_recent_years):])

        return [
            row
            for row in rows
            if (parsed := parse_idd_period(row.get("PeriodValue"), row.get("PeriodType", ""))) in keep
        ]

    @staticmethod
    def _filter_rows_for_months(
        rows: List[Dict[str, str]],
        months: Iterable[Tuple[int, int]],
    ) -> List[Dict[str, str]]:
        requested = {(int(year), int(month)) for year, month in months}
        return [
            row
            for row in rows
            if (parsed := parse_idd_period(row.get("PeriodValue"), row.get("PeriodType", ""))) is not None
            and (parsed.year, parsed.month) in requested
        ]

    @staticmethod
    def _filter_rows_from_year(rows: List[Dict[str, str]], start_year: Optional[int]) -> List[Dict[str, str]]:
        if start_year is None:
            return rows
        return [
            row
            for row in rows
            if (parsed := parse_idd_period(row.get("PeriodValue"), row.get("PeriodType", ""))) is not None
            and parsed.year >= int(start_year)
        ]

    def crawl_national(
        self,
        output_csv: Path,
        *,
        history: bool = False,
        months: Optional[List[Tuple[int, int]]] = None,
        start_year: Optional[int] = None,
        disease_slugs: Optional[List[str]] = None,
    ) -> CHFetchSummary:
        """Fetch FOPH IDD case series and write normalized national rows."""
        slug_filter = {slug.strip() for slug in disease_slugs or [] if slug.strip()} or None
        version = self.fetch_version()
        all_rows: List[Dict[str, str]] = []
        series_count = 0
        for series in self.fetch_series_sources(disease_slugs=slug_filter):
            try:
                rows, series_version = self._fetch_one_series(series)
            except Exception as exc:
                logger.warning(
                    f"[CH-IDD] Series fetch failed | identifier={series.identifier} error={exc}"
                )
                continue
            all_rows.extend(rows)
            series_count += 1
            if series_version:
                version = series_version
            time.sleep(0.02)

        if not all_rows:
            raise RuntimeError("[CH-IDD] No national case rows parsed from IDD API")

        if months is not None:
            all_rows = self._filter_rows_for_months(all_rows, months)
        elif not history:
            all_rows = self._filter_recent_rows(all_rows)

        all_rows = self._filter_rows_from_year(all_rows, start_year)
        if not all_rows:
            raise RuntimeError("[CH-IDD] No rows remained after local period filtering")

        all_rows.sort(
            key=lambda row: (
                row["Date"],
                row["RawDiseaseLabel"],
                row["PeriodType"],
                row["DiseaseCode"],
            )
        )
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "",
            "Disease",
            "DiseaseCode",
            "Year",
            "Month",
            "ISOWeek",
            "Date",
            "PeriodType",
            "PeriodValue",
            "Cases",
            "Geography",
            "Group",
            "DataComplete",
            "Trend",
            "SourceDate",
            "Version",
            "Source",
            "SourceURL",
        ]
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for idx, row in enumerate(all_rows, start=1):
                writer.writerow(
                    {
                        "": str(idx),
                        "Disease": row["RawDiseaseLabel"],
                        **{name: row.get(name, "") for name in fieldnames if name not in {"", "Disease"}},
                    }
                )

        latest_date = max(
            (
                datetime.strptime(row["Date"], "%Y-%m-%d").date()
                for row in all_rows
                if row.get("Date")
            ),
            default=None,
        )
        logger.info(
            f"[CH-IDD] CSV written | path={output_csv} rows={len(all_rows)} "
            f"series={series_count} latest={latest_date} version={version}"
        )
        return CHFetchSummary(
            row_count=len(all_rows),
            latest_date=latest_date,
            series_fetched=series_count,
            source_url=self.portal_url,
            version=version,
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        output_csv = Path(
            kwargs.get("output_csv") or "data/current/ch/switzerland_idd_cases.csv"
        )
        summary = self.crawl_national(
            output_csv,
            history=bool(kwargs.get("history", False)),
            months=kwargs.get("months"),
            start_year=kwargs.get("start_year"),
            disease_slugs=kwargs.get("disease_slugs"),
        )
        return [
            CrawlerResult(
                title="Switzerland FOPH IDD mandatory reporting case series",
                url=self.portal_url,
                date=datetime.now(timezone.utc),
                metadata={
                    "source": "foph_idd",
                    "country_code": "CH",
                    "row_count": summary.row_count,
                    "latest_date": summary.latest_date.isoformat()
                    if summary.latest_date
                    else None,
                    "series_fetched": summary.series_fetched,
                    "version": summary.version,
                },
            )
        ]

    def parse(self, response: Any) -> List[CrawlerResult]:
        """BaseCrawler contract; parsing is integrated in ``crawl_national``."""
        return []
