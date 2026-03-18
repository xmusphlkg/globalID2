"""Japan IDWR crawler.

Fetches JP weekly data into globalID2-managed CSV format used by updater.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config
from .base import BaseCrawler

logger = get_logger(__name__)
ROOT = Path(__file__).resolve().parents[3]

JP_ENGLISH_DISEASE_ALIASES: Dict[str, str] = {
    "Acquired immunodeficiency syndrome (AIDS)": "AIDS",
    "Acute Flaccid Paralysis (excluding Acute poliomyelitis)": "Acute flaccid paralysis",
    "Acute encephalitis(excluding JE and WNE)": "Acute encephalitis",
    "Amebiasis": "Amoebic dysentery",
    "Avian influenza (exclud. Avian influenza both H5N1 and H7N9)": "Avian influenza (excluding H5N1)",
    "Avian influenza H5N1": "Avian influenza (H5N1)",
    "Avian influenza H7N9": "Avian influenza (H7N9)",
    "Chlamydial pneumonia(excluding psittacosis)": "Chlamydial pneumonia",
    "Disseminated cryptococcal infection": "Disseminated cryptococcosis",
    "Enterohemorrhagic Escherichia coli infection": "Enterohemorrhagic E. coli infection (EHEC)",
    "Epidemic typhus": "Typhus (Rickettsial)",
    "Erythema infection": "Erythema infectiosum (Fifth disease)",
    "Exanthem subitum": "Roseola (Exanthem subitum)",
    "Glanders": "Pseudoglanders",
    "Hand, foot and mouth disease": "Hand-foot-and-mouth disease",
    "Herpes B virus infection": "Herpes B (Macacine herpesvirus 1) infection",
    "Infectious gastroenteritis (only by Rotavirus)": "Infectious gastroenteritis (Rotavirus)",
    "Influenza(excld. avian influenza and pandemic influenza)": "Influenza",
    "Invasive haemophilus influenzae infection": "Invasive Haemophilus influenzae infection",
    "Invasive meningococcal infection": "Invasive meningococcal disease",
    "Invasive streptococcal pneumoniae infection": "Invasive pneumococcal disease",
    "Kyasanur forest disease": "Casanul forest disease",
    "Lyssavirus infection(excluding rabies)": "Lissavirus infection",
    "Marburg disease": "Marburg hemorrhagic fever",
    "Middle East Respiratory Syndrome Coronavirus": "MERS (Middle East respiratory syndrome)",
    "Multiple drug-resistant Acinetobacter infection": "Drug-resistant Acinetobacter infection",
    "Pertussis": "Pertussis (Whooping cough)",
    "Psittacosis": "Psittacosis (Ornithosis)",
    "Respiratory syncytial virus infection": "Respiratory syncytial virus infection (RSV)",
    "Rift valley fever": "Rift Valley fever",
    "Rocky mountain spotted fever": "Rocky Mountain spotted fever",
    "Scrub typhus(Tsutsugamushi disease)": "Scrub typhus (Tsutsugamushi disease)",
    "Severe Acute Respiratory Syndrome(SARS)": "SARS (Severe Acute Respiratory Syndrome)",
    "Severe Fever with Thrombocytopenia Syndrome(SFTS)": "Severe fever with thrombocytopenia syndrome (SFTS)",
    "Severe invasive streptococcal infections(TSLS)": "Severe invasive group A streptococcal infection",
    "Shigellosis": "Bacterial dysentery",
    "Tick-borne encephalitis": "Ticks-borne encephalitis",
    "Vancomycin-resistant S. aureus infection": "Vancomycin-resistant Staphylococcus aureus infection",
    "Varicella (limited to hospiltalized case)": "Chickenpox (hospitalized cases)",
    "Viral hepatitis(excluding hepatitis A and E)": "Viral hepatitis",
}


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _parse_int(value: object) -> Optional[int]:
    txt = _norm_text(value)
    if not txt:
        return None
    try:
        return int(float(txt))
    except ValueError:
        return None


def _mmwr_week_end_date(year: int, week: int) -> date:
    jan_4 = date(year, 1, 4)
    week_1_start = jan_4 - timedelta(days=(jan_4.weekday() + 1) % 7)
    return week_1_start + timedelta(weeks=week - 1, days=6)


@dataclass
class JPFetchSummary:
    row_count: int
    latest_date: Optional[date]
    csv_url: str
    debug_logs: List[str]


class JapanIDWRCrawler(BaseCrawler):
    """Crawler for JP weekly standardized rows."""

    def __init__(self) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0)",
            timeout=60,
            max_retries=3,
            delay=0.5,
        )

        cfg = get_country_bootstrap_config("JP")
        self.page_url = _norm_text(cfg.get("data_source_url")) or "https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/index.html"
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg.get("crawler_config"), dict) else {}
        self.explicit_csv_url = _norm_text(crawler_cfg.get("weekly_csv_url"))
        self.max_candidate_csvs = int(crawler_cfg.get("max_candidate_csvs") or 5)
        self.refresh_recent_years = int(crawler_cfg.get("refresh_recent_years") or 2)
        self.raw_dir = ROOT / "data/raw/jp"

    async def crawl(self, **kwargs):  # pragma: no cover - not used via base crawl path
        raise NotImplementedError("Use crawl_standardized_csv()")

    def parse(self, response):  # pragma: no cover - not used via base parse path
        return []

    def crawl_standardized_csv(self, output_csv: Path, reporting_area: str = "総数", force: bool = False) -> JPFetchSummary:
        debug_logs: List[str] = []
        existing_weeks = self._load_existing_year_weeks(output_csv)
        if self.explicit_csv_url:
            csv_urls = [self.explicit_csv_url]
            raw_csv_urls = list(csv_urls)
            debug_logs.append(f"[discover] using explicit CSV URL: {self.explicit_csv_url}")
        else:
            debug_logs.append(f"[discover] existing weeks in output CSV: {len(existing_weeks)}")
            debug_logs.append(f"[discover] mode: {'force-all-weeks' if force else 'missing-weeks-only'}")
            csv_urls, raw_csv_urls, discover_logs = self._discover_weekly_csv_urls(
                existing_weeks=existing_weeks,
                force=force,
            )
            debug_logs.extend(discover_logs)

            for raw_csv_url in raw_csv_urls:
                try:
                    self._download_csv_table(raw_csv_url)
                    debug_logs.append(f"[raw] saved {raw_csv_url}")
                except Exception as exc:
                    logger.warning(f"Skip raw CSV cache due to download failure: {raw_csv_url} ({exc})")
                    debug_logs.append(f"[raw] skip {raw_csv_url} ({exc})")

        if not csv_urls and output_csv.exists():
            latest = None
            row_count = 0
            with output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    row_count += 1
                    year = _parse_int(row.get("Current MMWR Year"))
                    week = _parse_int(row.get("MMWR WEEK"))
                    if year is None or week is None:
                        continue
                    day = _mmwr_week_end_date(year, week)
                    if latest is None or day > latest:
                        latest = day
            debug_logs.append("[discover] no missing weeks found; keep existing standardized CSV")
            return JPFetchSummary(
                row_count=row_count,
                latest_date=latest,
                csv_url="",
                debug_logs=debug_logs,
            )

        merged: Dict[tuple[str, str, str, str], Tuple[int, Dict[str, str]]] = {}
        for csv_url in csv_urls:
            try:
                table = self._download_csv_table(csv_url)
            except Exception as exc:
                logger.warning(f"Skip CSV due to download failure: {csv_url} ({exc})")
                debug_logs.append(f"[download] skip {csv_url} ({exc})")
                continue
            source_kind = self._csv_kind_from_url(csv_url)
            normalized = self._normalize_rows(table, reporting_area=reporting_area, source_kind=source_kind)
            debug_logs.append(f"[parse] {source_kind}: {csv_url} -> {len(normalized)} rows")
            for row in normalized:
                key = (
                    row.get("Reporting Area", ""),
                    row.get("Current MMWR Year", ""),
                    row.get("MMWR WEEK", ""),
                    row.get("Disease", ""),
                )
                priority = 0 if source_kind == "zensu" else 1
                existing = merged.get(key)
                if existing is None or priority < existing[0]:
                    merged[key] = (priority, row)

        normalized = [row for _, row in merged.values()]
        normalized.sort(key=lambda r: (r["Current MMWR Year"], r["MMWR WEEK"], r["Disease"]))

        if not normalized:
            raise RuntimeError(
                "JP crawler found candidate CSV links but did not parse any valid rows. "
                f"Entry page: {self.page_url}"
            )

        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "Reporting Area",
                    "Current MMWR Year",
                    "MMWR WEEK",
                    "Disease",
                    "Current week",
                    "Current week, flag",
                ],
            )
            writer.writeheader()
            writer.writerows(normalized)

        latest = None
        for row in normalized:
            year = _parse_int(row.get("Current MMWR Year"))
            week = _parse_int(row.get("MMWR WEEK"))
            if year is None or week is None:
                continue
            day = _mmwr_week_end_date(year, week)
            if latest is None or day > latest:
                latest = day

        return JPFetchSummary(
            row_count=len(normalized),
            latest_date=latest,
            csv_url=";".join(csv_urls[:20]),
            debug_logs=debug_logs,
        )

    @staticmethod
    def _parse_year_week_from_url(url: str) -> Tuple[Optional[int], Optional[int]]:
        m = re.search(r"/(provisional|rapid)/(20\d{2})/(\d{1,2})/index\.html", url.lower())
        if not m:
            return None, None
        return int(m.group(2)), int(m.group(3))

    @staticmethod
    def _load_existing_year_weeks(output_csv: Path) -> Set[Tuple[int, int]]:
        existing: Set[Tuple[int, int]] = set()
        if not output_csv.exists():
            return existing

        with output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                year = _parse_int(row.get("Current MMWR Year"))
                week = _parse_int(row.get("MMWR WEEK"))
                if year is None or week is None:
                    continue
                if 1 <= week <= 53:
                    existing.add((year, week))
        return existing

    def _discover_year_index_urls(self) -> List[str]:
        response = self.get(self.page_url)
        base_url = response.url or self.page_url
        soup = BeautifulSoup(response.text, "html.parser")

        candidates: Set[str] = set()
        for tag in soup.find_all("a", href=True):
            href = _norm_text(tag.get("href"))
            if not href:
                continue
            full = urljoin(base_url, href)
            if re.search(r"/(provisional|rapid)/(20\d{2})/index\.html", full.lower()):
                candidates.add(full)

        if re.search(r"/(provisional|rapid)/(20\d{2})/index\.html", base_url.lower()):
            candidates.add(base_url)

        week_match = re.search(r"(/(provisional|rapid)/(20\d{2}))/\d{1,2}/index\.html", base_url.lower())
        if week_match:
            candidates.add(urljoin(base_url, f"{week_match.group(1)}/index.html"))

        if not candidates:
            raise RuntimeError(
                "Unable to discover JP provisional year pages. "
                "Use entry: https://id-info.jihs.go.jp/surveillance/idwr/provisional/sokuhou.html"
            )

        return sorted(candidates, reverse=True)

    def _discover_week_index_urls(self, year_index_url: str) -> List[str]:
        response = self.get(year_index_url)
        base_url = response.url or year_index_url
        soup = BeautifulSoup(response.text, "html.parser")

        candidates: Set[str] = set()
        for tag in soup.find_all("a", href=True):
            href = _norm_text(tag.get("href"))
            if not href:
                continue
            full = urljoin(base_url, href)
            if re.search(r"/(provisional|rapid)/(20\d{2})/(\d{1,2})/index\.html", full.lower()):
                candidates.add(full)

        sorted_candidates = sorted(
            candidates,
            key=lambda u: self._parse_year_week_from_url(u),
            reverse=True,
        )
        return sorted_candidates

    def _discover_weekly_csv_urls(
        self,
        *,
        existing_weeks: Set[Tuple[int, int]],
        force: bool = False,
    ) -> Tuple[List[str], List[str], List[str]]:
        logs: List[str] = []
        year_index_urls = self._discover_year_index_urls()
        logs.append(f"[discover] year pages: {len(year_index_urls)}")

        selected_week_pages: List[str] = []
        for year_url in year_index_urls:
            week_urls = self._discover_week_index_urls(year_url)
            logs.append(f"[discover] {year_url} -> {len(week_urls)} week pages")
            for week_url in week_urls:
                y, w = self._parse_year_week_from_url(week_url)
                if y is None or w is None:
                    continue
                if force or (y, w) not in existing_weeks:
                    selected_week_pages.append(week_url)
                    logs.append(f"[select] week {y}-W{w:02d}: {week_url}")

        logs.append(f"[discover] missing week pages selected: {len(selected_week_pages)}")

        csv_urls: List[str] = []
        raw_csv_urls: List[str] = []
        for week_page in selected_week_pages:
            try:
                response = self.get(week_page)
            except Exception as exc:
                logger.warning(f"Skip week page due to fetch failure: {week_page} ({exc})")
                continue

            base_url = response.url or week_page
            soup = BeautifulSoup(response.text, "html.parser")
            candidates: Dict[str, str] = {}
            all_candidates: List[str] = []
            for tag in soup.find_all("a", href=True):
                href = _norm_text(tag.get("href"))
                if not href:
                    continue
                if not href.lower().endswith(".csv"):
                    continue
                full = urljoin(base_url, href)
                all_candidates.append(full)
                kind = self._csv_kind_from_url(full)
                if kind in {"zensu", "teiten"}:
                    candidates[kind] = full

            raw_csv_urls.extend(sorted(set(all_candidates)))

            if not candidates:
                continue

            y, w = self._parse_year_week_from_url(week_page)
            if "zensu" in candidates:
                csv_urls.append(candidates["zensu"])
            if "teiten" in candidates:
                csv_urls.append(candidates["teiten"])
            logs.append(
                f"[week] {y}-W{w:02d} csvs: "
                + ", ".join(f"{k}={v}" for k, v in sorted(candidates.items()))
            )

        unique_csv_urls = sorted(set(csv_urls), key=self._candidate_sort_key, reverse=True)
        unique_raw_csv_urls = sorted(set(raw_csv_urls), key=self._candidate_sort_key, reverse=True)
        return unique_csv_urls, unique_raw_csv_urls, logs

    @staticmethod
    def _csv_kind_from_url(url: str) -> str:
        name = Path(urlparse(url).path).name.lower()
        if name.startswith("zensu"):
            return "zensu"
        if name.startswith("teiten") and "rui" not in name and "ari" not in name:
            return "teiten"
        if "zensu" in name:
            return "zensu"
        if "teiten" in name and "rui" not in name and "ari" not in name:
            return "teiten"
        return "other"

    @staticmethod
    def _candidate_sort_key(url: str) -> tuple[int, int, int, str]:
        text = url.lower()
        # Prefer nationwide weekly files first when multiple CSV flavors exist.
        total_bias = 1 if any(k in text for k in ("zensu", "all", "total")) else 0
        full_date_match = re.search(r"(20\d{2})(\d{2})(\d{2})", text)
        if full_date_match:
            y, m, d = full_date_match.groups()
            return total_bias, int(y), int(m), int(d), text

        year_week_match = re.search(r"(20\d{2})[^0-9]{0,3}(\d{1,2})", text)
        if year_week_match:
            y, w = year_week_match.groups()
            return total_bias, int(y), 0, int(w), text

        year_match = re.search(r"(20\d{2})", text)
        if year_match:
            return total_bias, int(year_match.group(1)), 0, 0, text

        return total_bias, 0, 0, 0, text

    def _download_csv_table(self, csv_url: str) -> List[List[str]]:
        response = self.get(csv_url)
        content = response.content

        parsed = urlparse(csv_url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 4:
            year, week = parts[-3], parts[-2]
            filename = parts[-1]
            raw_path = self.raw_dir / year / week / filename
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(content)

        text = None
        for enc in ("utf-8-sig", "cp932", "shift_jis", "euc_jp"):
            try:
                text = content.decode(enc)
                break
            except Exception:
                continue
        if text is None:
            raise RuntimeError(f"Unable to decode CSV content: {csv_url}")

        reader = csv.reader(text.splitlines())
        return [list(row) for row in reader]

    def _normalize_rows(self, rows: List[List[str]], reporting_area: str, source_kind: str) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []

        if not rows:
            return normalized

        header0 = _norm_text(rows[0][0] if rows[0] else "").lower()

        # Format A: legacy standardized CSV (already fielded by Reporting Area / MMWR WEEK ...).
        if "reporting area" in header0:
            header = rows[0]
            for raw in rows[1:]:
                src = {header[i]: raw[i] if i < len(raw) else "" for i in range(len(header))}
                area = _norm_text(src.get("Reporting Area") or src.get("states") or reporting_area)
                if area != reporting_area:
                    continue

                year = _parse_int(src.get("Current MMWR Year") or src.get("year"))
                week = _parse_int(src.get("MMWR WEEK") or src.get("week"))
                disease = _norm_text(src.get("Disease") or src.get("label"))
                cases = _parse_int(src.get("Current week") or src.get("m1"))
                flag = _norm_text(src.get("Current week, flag") or src.get("m1_flag"))

                if year is None or week is None or not disease or cases is None:
                    continue
                if week <= 0 or week > 53:
                    continue

                normalized.append(
                    {
                        "Reporting Area": area,
                        "Current MMWR Year": str(year),
                        "MMWR WEEK": str(week),
                        "Disease": disease,
                        "Current week": str(max(0, cases)),
                        "Current week, flag": flag,
                    }
                )
            normalized.sort(key=lambda r: (r["Current MMWR Year"], r["MMWR WEEK"], r["Disease"]))
            return normalized

        # Format B: JIHS provisional zensu matrix.
        # Row 2 holds disease names, row 3 holds "報告/累積", and row "総数" holds totals.
        year: Optional[int] = None
        week: Optional[int] = None
        for row in rows[:10]:
            line = " ".join(_norm_text(c) for c in row if _norm_text(c))
            if not line:
                continue

            m = re.search(r"(20\d{2})[^0-9]{0,10}(\d{1,2})(?:th|st|nd|rd)?\s*week", line, flags=re.I)
            if m:
                year = int(m.group(1))
                week = int(m.group(2))
                break

            m2 = re.search(r"(\d{1,2})(?:th|st|nd|rd)?\s*week[^0-9]{0,10}(20\d{2})", line, flags=re.I)
            if m2:
                week = int(m2.group(1))
                year = int(m2.group(2))
                break

            m3 = re.search(r"(20\d{2})年\s*(\d{1,2})週", line)
            if m3:
                year = int(m3.group(1))
                week = int(m3.group(2))
                break

        if year is None or week is None:
            return normalized

        if week <= 0 or week > 53:
            return normalized

        total_row: List[str] = []
        total_start_idx: Optional[int] = None
        total_row_idx: Optional[int] = None
        for ridx, row in enumerate(rows):
            for cidx, cell in enumerate(row):
                if _norm_text(cell) in {"総数", "Total No."}:
                    total_row = row
                    total_start_idx = cidx
                    total_row_idx = ridx
                    break
            if total_row:
                break

        header_row: List[str] = []
        header_start_idx: Optional[int] = None
        if total_row_idx is not None:
            for ridx in range(total_row_idx - 1, -1, -1):
                row = rows[ridx]
                for cidx, cell in enumerate(row):
                    if _norm_text(cell).lower() in {"prefecture", "都道府県"}:
                        header_row = row
                        header_start_idx = cidx
                        break
                if header_row:
                    break

        if not header_row or not total_row or total_start_idx is None:
            return normalized

        start_col = max((header_start_idx or 0) + 1, total_start_idx + 1)
        max_len = min(len(header_row), len(total_row))
        for idx in range(start_col, max_len):
            disease = _norm_text(header_row[idx])
            if not disease:
                continue
            disease = JP_ENGLISH_DISEASE_ALIASES.get(disease, disease)
            lower_disease = disease.lower()
            if lower_disease == "current week" or lower_disease.startswith("cum"):
                continue

            cases = _parse_int(total_row[idx] if idx < len(total_row) else "")
            if cases is None:
                continue

            normalized.append(
                {
                    "Reporting Area": reporting_area,
                    "Current MMWR Year": str(year),
                    "MMWR WEEK": str(week),
                    "Disease": disease,
                    "Current week": str(max(0, cases)),
                    "Current week, flag": "",
                }
            )

        normalized.sort(key=lambda r: (r["Current MMWR Year"], r["MMWR WEEK"], r["Disease"]))
        return normalized
