"""
GlobalID V2 Australia NINDSS Crawler

Fetches infectious disease data from the Australian National Notifiable
Diseases Surveillance System (NINDSS) Power BI dashboard.

Authentication flow (mirrors ID_AU/ScriptGetData/GetData.py):
  1. Launch a headless Chromium browser via Playwright.
  2. Navigate to the NINDSS dashboard and intercept the Authorization
     Bearer token that the browser sends to the Power BI query endpoint.
  3. Use that token to execute DAX queries and retrieve disease counts.

Public interface
----------------
  crawl(years, fill_missing)        -> List[CrawlerResult]
  crawl_monthly_national_csv(path)  -> AUFetchSummary   (used by processor)
  parse(response)                   -> []                (BaseCrawler contract)
"""
from __future__ import annotations

import csv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config
from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

# ── Power BI constants (from ID_AU/ScriptGetData/GetData.py) ─────────────────
_CAPACITY_ID = "86715F84-E812-421E-972F-2211ACC9903A"
_DASHBOARD_URL = "https://nindss.health.gov.au/pbi-dashboard/"
_REPORT_ID = "bc027587-5e9e-4920-bf03-a45fd3079f25"
_DATASET_ID = "3471d96b-c14c-403f-b3a6-016f1deac28e"
_MODEL_ID = 3305775

# States to skip when summing to national total (these are aggregate rows)
_SKIP_STATES = {"AUS", "UNKNOWN", "TOTAL", "ALL"}


# ── Helpers (ported from GetDataFunctions.py) ─────────────────────────────────

def _norm_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _parse_int(value: object) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _find_dm0(obj: Any) -> Optional[List[Dict]]:
    """Recursively find the first DM0 array in a DSR response structure."""
    if isinstance(obj, dict):
        for v in obj.values():
            res = _find_dm0(v)
            if res:
                return res
    elif isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            if any(any(k == "G0" or k.startswith("C") for k in item.keys()) for item in obj):
                return obj
        for item in obj:
            res = _find_dm0(item)
            if res:
                return res
    return None


def _parse_dm0_to_state_counts(dm0: List[Dict], raw: Dict, year: Any) -> Dict[str, int]:
    """
    Parse DM0 list into {state: count} mapping.
    Ported from ID_AU/ScriptGetData/GetDataFunctions.py::parse_dm0_to_state_counts.
    """
    results: Dict[str, Any] = {}

    # Strategy 1: use SH/DM1 metadata to map column indices to state names
    try:
        ds = raw["results"][0]["result"]["data"]["dsr"]["DS"][0]
        sh = ds.get("SH", [])[0]
        dm1 = sh.get("DM1") if sh else None
        if dm1 and isinstance(dm1, list) and dm1[0].get("G1"):
            states = [entry.get("G1") for entry in dm1]
            for block in dm0:
                year_key = block.get("G0") or block.get("G")
                if str(year_key) == str(year):
                    for i, xi in enumerate(block.get("X", [])):
                        st = states[i] if i < len(states) else f"idx_{i}"
                        val = None
                        if isinstance(xi, dict):
                            for kk in ("M0", "M", "V"):
                                if kk in xi:
                                    val = xi[kk]
                                    break
                            if val is None:
                                for vv in xi.values():
                                    if isinstance(vv, (int, float)):
                                        val = vv
                                        break
                        else:
                            val = xi
                        results[st] = int(val) if val is not None else 0
                    return results
    except Exception:
        pass

    # Strategy 2: generic fallback
    for item in dm0:
        if not isinstance(item, dict):
            continue
        state = None
        value = None

        for gkey in ("G0", "G", "G1", "G2"):
            if gkey in item and item[gkey] is not None:
                state = item[gkey]
                break

        if "C" in item and isinstance(item["C"], (list, tuple)) and item["C"]:
            c = item["C"]
            state = state or (c[0] if len(c) >= 1 else state)
            if len(c) >= 2:
                value = c[1]

        if value is None:
            for k, v in item.items():
                if k == "C" and isinstance(v, (list, tuple)):
                    continue
                if k.startswith("M") or (k.startswith("C") and not isinstance(v, (list, tuple))):
                    try:
                        value = int(v)
                    except Exception:
                        try:
                            value = float(v)
                        except Exception:
                            value = v
                    break

        if value is None and "V" in item:
            value = item["V"]
        if value is None and "R" in item:
            value = 0

        if state is None:
            for v in item.values():
                if isinstance(v, str):
                    state = v
                    break
                if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
                    state = v[0]
                    break
        if state is None:
            state = str(item)

        if isinstance(state, (list, tuple)) and state:
            state = state[0]
        if isinstance(state, dict):
            for candidate in ("C", "Label", "G", "G1", "G0"):
                if candidate in state:
                    s = state[candidate]
                    if isinstance(s, (list, tuple)) and s:
                        state = s[0]
                        break
                    if isinstance(s, str):
                        state = s
                        break
            else:
                state = str(state)

        if isinstance(value, (list, tuple)):
            value = value[1] if len(value) >= 2 else (value[0] if value else None)
        if isinstance(value, str):
            try:
                value = int(value)
            except Exception:
                try:
                    value = float(value)
                except Exception:
                    pass
        if value is None:
            value = 0

        k_str = str(state)
        v_norm = int(value) if isinstance(value, float) and float(value).is_integer() else value
        results[k_str] = v_norm

    return results


def _month_name(m: int) -> str:
    return datetime(2000, m, 1).strftime("%B")


def _quarter_for_month(m: int) -> str:
    # NOTE: The PowerBI dashboard requires TWO spaces after "Quarter".
    return f"Quarter  {(m - 1) // 3 + 1}"


# ── Return-value dataclass ────────────────────────────────────────────────────

@dataclass
class AUFetchSummary:
    row_count: int
    latest_date: Optional[date]
    csv_url: str


# ── Main crawler class ────────────────────────────────────────────────────────

class AustraliaNINDSSCrawler(BaseCrawler):
    """
    Australia NINDSS Power BI crawler.

    Uses Playwright to capture a Bearer token from live browser traffic,
    then queries the Power BI DAX endpoint to retrieve notifiable disease
    case counts by state, aggregating to national totals.
    """

    def __init__(self) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; AU-NINDSS)",
            timeout=60,
            max_retries=3,
            delay=0.3,
        )
        cfg = get_country_bootstrap_config("AU")
        crawler_cfg = (
            cfg.get("crawler_config", {})
            if isinstance(cfg.get("crawler_config"), dict)
            else {}
        )
        self.dashboard_url: str = (
            _norm_text(crawler_cfg.get("dashboard_url")) or _DASHBOARD_URL
        )
        self.capacity_id: str = (
            _norm_text(crawler_cfg.get("capacity_id")) or _CAPACITY_ID
        )
        self._config: Optional[Dict[str, Any]] = None
        self._http: Optional[requests.Session] = None

    # ── Authentication ────────────────────────────────────────────────────────

    @staticmethod
    def _make_http_session() -> requests.Session:
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _load_config(self) -> bool:
        """
        Launch a headless browser, navigate to the NINDSS dashboard, and
        intercept the Authorization Bearer token from the first Power BI
        query request.  Populates self._config on success.
        """
        logger.info(
            f"[AU-NINDSS] Starting browser token capture | dashboard={self.dashboard_url}"
        )
        captured: Dict[str, Any] = {}
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                def on_request(req: Any) -> None:
                    if "/webapi/capacities" in req.url and "public/query" in req.url:
                        headers = req.headers
                        auth = headers.get("authorization") or headers.get("Authorization", "")
                        if auth:
                            captured["token"] = auth
                            captured["url"] = req.url
                            captured["report_id"] = headers.get("x-powerbi-reportid", _REPORT_ID)

                page.on("request", on_request)
                page.goto(self.dashboard_url, timeout=90_000)

                waited = 0
                while waited < 45 and "token" not in captured:
                    time.sleep(1)
                    waited += 1

                browser.close()

        except ImportError:
            logger.error(
                "[AU-NINDSS] playwright not installed — "
                "run: pip install playwright && playwright install chromium"
            )
            return False
        except Exception as exc:
            logger.error(f"[AU-NINDSS] Browser token capture failed | error={exc}")
            return False

        if not captured.get("token"):
            logger.error("[AU-NINDSS] Timed out waiting for token from browser traffic")
            return False

        token = captured["token"]
        if token.lower().startswith("bearer "):
            token = token.split(" ", 1)[1]

        # Build API URL from intercepted URL or fall back to capacity slug URL
        api_url = ""
        raw_url = captured.get("url", "")
        if raw_url:
            parsed = urlparse(raw_url)
            if parsed.scheme and parsed.netloc:
                # Use the exact URL that was intercepted (most reliable)
                api_url = raw_url
        if not api_url:
            cap_slug = self.capacity_id.replace("-", "").lower()
            api_url = (
                f"https://{cap_slug}.pbidedicated.windows.net"
                f"/webapi/capacities/{self.capacity_id}"
                "/workloads/QES/QueryExecutionService/automatic/public/query"
            )

        self._config = {
            "accessToken": token,
            "apiUrl": api_url,
            "reportId": captured.get("report_id") or _REPORT_ID,
            "datasetId": _DATASET_ID,
            "modelId": _MODEL_ID,
        }
        self._http = self._make_http_session()

        logger.info(
            f"[AU-NINDSS] Token captured | token_prefix={token[:15]}... "
            f"api_url={api_url[:60]}..."
        )
        return True

    # ── Query execution ───────────────────────────────────────────────────────

    def _auth_headers(self) -> Dict[str, str]:
        assert self._config
        token = self._config["accessToken"]
        if not token.lower().startswith(("bearer ", "mwctoken ", "token ")):
            token = f"Bearer {token}"
        return {
            "Authorization": token,
            "Content-Type": "application/json;charset=UTF-8",
            "X-PowerBI-ReportId": self._config["reportId"],
            "RequestId": str(int(time.time() * 1000)),
        }

    def _execute_payload(
        self, payload: Dict[str, Any], timeout: int = 60
    ) -> Tuple[Optional[List], Optional[Dict]]:
        """POST a Power BI query payload; return (dm0, raw_response)."""
        if not self._config or not self._http:
            return None, None
        try:
            resp = self._http.post(
                self._config["apiUrl"],
                headers=self._auth_headers(),
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = resp.json()
            dsr = (
                raw.get("results", [{}])[0]
                .get("result", {})
                .get("data", {})
                .get("dsr")
            )
            dm0 = _find_dm0(dsr) if dsr is not None else None
            return dm0, raw
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 401:
                logger.warning("[AU-NINDSS] 401 Unauthorized — token may have expired")
            else:
                logger.debug(f"[AU-NINDSS] payload request failed | error={exc}")
            return None, None

    def _execute_dax_list(self, entity: str, column: str) -> List[str]:
        """Execute a simple DAX DISTINCT query and return the string list."""
        if not self._config or not self._http:
            return []
        payload = {
            "version": "1.0.0",
            "queries": [{
                "Query": {
                    "Commands": [{
                        "SemanticQueryDataShapeCommand": {
                            "Query": {
                                "Version": 2,
                                "From": [{"Name": "n", "Entity": entity}],
                                "Select": [{
                                    "Column": {
                                        "Expression": {"SourceRef": {"Source": "n"}},
                                        "Property": column,
                                    }
                                }],
                                "OrderBy": [{
                                    "Direction": 1,
                                    "Expression": {
                                        "Column": {
                                            "Expression": {"SourceRef": {"Source": "n"}},
                                            "Property": column,
                                        }
                                    },
                                }],
                            },
                            "Binding": {
                                "Primary": {"Groupings": [{"Projections": [0]}]},
                                "DataReduction": {"DataVolume": 4, "Primary": {"Top": {}}},
                            },
                        }
                    }]
                },
                "ApplicationContext": {
                    "DatasetId": self._config["datasetId"],
                    "Sources": [{"ReportId": self._config["reportId"]}],
                },
            }],
            "cancelRequests": True,
            "modelId": self._config["modelId"],
        }
        try:
            resp = self._http.post(
                self._config["apiUrl"],
                headers=self._auth_headers(),
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            items = (
                data["results"][0]["result"]["data"]["dsr"]["DS"][0]["PH"][0]["DM0"]
            )
            return [str(item["G0"]) for item in items if "G0" in item]
        except Exception as exc:
            logger.debug(f"[AU-NINDSS] DAX list query failed | entity={entity} error={exc}")
            return []

    def get_all_diseases(self) -> List[str]:
        items = self._execute_dax_list(
            entity="DELTALOAD_DATAMART DISEASE_DIM",
            column="DISEASE NAME",
        )
        return sorted(d for d in items if d and "All Notifiable" not in d)

    def get_all_years(self) -> List[str]:
        items = self._execute_dax_list(
            entity="DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT",
            column="DAX_Year",
        )
        try:
            return sorted((str(y) for y in items if y), key=int, reverse=True)
        except Exception:
            return sorted(items, reverse=True)

    # ── Per-disease location payload ──────────────────────────────────────────

    def _build_location_payload(
        self, year: str, quarter: str, month_name: str, disease: str
    ) -> Dict[str, Any]:
        return {
            "version": "1.0.0",
            "queries": [{
                "Query": {
                    "Commands": [{
                        "SemanticQueryDataShapeCommand": {
                            "Query": {
                                "Version": 2,
                                "From": [
                                    {"Name": "d1", "Entity": "DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT", "Type": 0},
                                    {"Name": "d",  "Entity": "DELTALOAD_DATAMART LOCATION_DIM", "Type": 0},
                                    {"Name": "d11","Entity": "DELTALOAD_DATAMART DISEASE_DIM", "Type": 0},
                                    {"Name": "d3", "Entity": "DELTALOAD_DATAMART CASE_DIM", "Type": 0},
                                ],
                                "Select": [
                                    {
                                        "Column": {
                                            "Expression": {"SourceRef": {"Source": "d"}},
                                            "Property": "STATE",
                                        },
                                        "Name": "DELTALOAD_DATAMART LOCATION_DIM.STATE",
                                    },
                                    {
                                        "Measure": {
                                            "Expression": {"SourceRef": {"Source": "d1"}},
                                            "Property": "Count_Notification_ForGraph",
                                        },
                                        "Name": "DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT.M_Notification_ForGraph",
                                    },
                                ],
                                "Where": [
                                    {"Condition": {"Not": {"Expression": {"In": {
                                        "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": "STATE"}}],
                                        "Values": [
                                            [{"Literal": {"Value": "'AUS'"}}],
                                            [{"Literal": {"Value": "'Unknown'"}}],
                                        ],
                                    }}}}},
                                    {"Condition": {"In": {
                                        "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "d11"}}, "Property": "DISEASE NAME"}}],
                                        "Values": [[{"Literal": {"Value": f"'{disease}'"}}]],
                                    }}},
                                    {"Condition": {"In": {
                                        "Expressions": [
                                            {"Column": {"Expression": {"SourceRef": {"Source": "d1"}}, "Property": "DIAGNOSIS_YEAR_HIERARCHY"}},
                                            {"Column": {"Expression": {"SourceRef": {"Source": "d1"}}, "Property": "DIAGNOSIS_QUARTER"}},
                                            {"Column": {"Expression": {"SourceRef": {"Source": "d1"}}, "Property": "DIAGNOSIS_MONTHNAME"}},
                                        ],
                                        "Values": [[
                                            {"Literal": {"Value": f"'{year}'"}},
                                            {"Literal": {"Value": f"'{quarter}'"}},
                                            {"Literal": {"Value": f"'{month_name}'"}},
                                        ]],
                                    }}},
                                    {"Condition": {"Comparison": {
                                        "ComparisonKind": 1,
                                        "Left": {"Column": {"Expression": {"SourceRef": {"Source": "d1"}}, "Property": "DAX_Year"}},
                                        "Right": {"Literal": {"Value": "1990L"}},
                                    }}},
                                    {"Condition": {"In": {
                                        "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "d3"}}, "Property": "CONFIRMATION_STATUS"}}],
                                        "Values": [
                                            [{"Literal": {"Value": "'Confirmed'"}}],
                                            [{"Literal": {"Value": "'Probable'"}}],
                                        ],
                                    }}},
                                ],
                            },
                            "Binding": {
                                "Primary": {"Groupings": [{"Projections": [0, 1]}]},
                                "DataReduction": {"DataVolume": 4, "Primary": {"Window": {"Count": 200}}},
                            },
                        }
                    }]
                },
                "ApplicationContext": {
                    "DatasetId": self._config["datasetId"],
                    "Sources": [{"ReportId": self._config["reportId"]}],
                },
            }],
            "modelId": self._config["modelId"],
            "cancelRequests": True,
        }

    def _fetch_month_disease(
        self, year: int, month: int, disease: str
    ) -> Optional[Dict[str, int]]:
        """Fetch {state: count} for one disease/month. Returns None on failure."""
        quarter = _quarter_for_month(month)
        mname = _month_name(month)
        payload = self._build_location_payload(str(year), quarter, mname, disease)
        dm0, raw = self._execute_payload(payload)
        if dm0 is None or raw is None:
            return None
        return _parse_dm0_to_state_counts(dm0, raw, str(year))

    # ── Internal sync fetch (shared by crawl() and crawl_monthly_national_csv) ─

    def _fetch_months_concurrent(
        self,
        months_to_fetch: List[Tuple[int, int]],
        diseases: List[str],
    ) -> Dict[Tuple[int, int, str], int]:
        """
        Fetch national totals for every disease × month combination.

        Returns {(year, month, disease): national_total}.
        """
        tasks = [
            (y, m, disease)
            for (y, m) in months_to_fetch
            for disease in diseases
        ]
        logger.info(
            f"[AU-NINDSS] Fetching | months={len(months_to_fetch)} "
            f"diseases={len(diseases)} total_requests={len(tasks)}"
        )
        totals: Dict[Tuple[int, int, str], int] = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(self._fetch_month_disease, y, m, dis): (y, m, dis)
                for y, m, dis in tasks
            }
            done = 0
            for future in as_completed(futures):
                y, m, dis = futures[future]
                done += 1
                try:
                    state_counts = future.result()
                    if state_counts:
                        national = sum(
                            v for k, v in state_counts.items()
                            if k.upper() not in _SKIP_STATES
                            and isinstance(v, (int, float))
                        )
                        if national > 0:
                            totals[(y, m, dis)] = int(national)
                except Exception as exc:
                    logger.debug(
                        f"[AU-NINDSS] fetch failed | "
                        f"{y}-{m:02d} {dis} error={exc}"
                    )
                if done % 50 == 0:
                    logger.info(f"[AU-NINDSS] Progress | {done}/{len(tasks)}")
        return totals

    # ── Public interface ──────────────────────────────────────────────────────

    async def crawl(
        self,
        years: Optional[List[int]] = None,
        fill_missing: bool = False,
    ) -> List[CrawlerResult]:
        """
        Fetch NINDSS disease counts as CrawlerResult objects.

        Default (years=None): fetches the most recent 3 months — fast for
        smoke tests and incremental updates.  Pass explicit years for a
        fuller pull.
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._crawl_sync(years=years))

    def _crawl_sync(self, years: Optional[List[int]] = None) -> List[CrawlerResult]:
        if not self._load_config():
            raise RuntimeError(
                "AU NINDSS: failed to capture Power BI auth token via Playwright"
            )

        diseases = self.get_all_diseases()
        if not diseases:
            raise RuntimeError(
                "AU NINDSS: could not retrieve disease list from Power BI"
            )

        now = datetime.now()
        if years is None:
            # Recent 3 months
            months_to_fetch: List[Tuple[int, int]] = []
            for delta in range(3):
                m = now.month - delta
                y = now.year
                if m <= 0:
                    m += 12
                    y -= 1
                months_to_fetch.append((y, m))
        else:
            months_to_fetch = [
                (y, m)
                for y in years
                for m in range(1, 13 if y < now.year else now.month + 1)
            ]

        totals = self._fetch_months_concurrent(months_to_fetch, diseases)

        results: List[CrawlerResult] = []
        for (y, m, disease), national in sorted(totals.items()):
            results.append(
                CrawlerResult(
                    title=f"Australia NINDSS: {disease} {y}-{m:02d}",
                    url=self.dashboard_url,
                    year_month=f"{y} {_month_name(m)}",
                    date=datetime(y, m, 1),
                    metadata={
                        "disease": disease,
                        "year": y,
                        "month": m,
                        "source": "NINDSS",
                    },
                    raw_data={"national_total": national},
                )
            )

        logger.info(
            f"[AU-NINDSS] Done | "
            f"months={len(months_to_fetch)} total_results={len(results)}"
        )
        return results

    def crawl_monthly_national_csv(
        self,
        output_csv: Path,
        months: Optional[List[Tuple[int, int]]] = None,
    ) -> AUFetchSummary:
        """
        Fetch NINDSS disease data and write to a standardised CSV.

        Args:
            output_csv: destination path for the CSV file.
            months: explicit list of (year, month) pairs to fetch.  When
                    None the most recent 3 months are used (fast default for
                    incremental updates).  Pass a list to target specific
                    months, e.g. when back-filling missing DB records.

        CSV columns: (index), Disease, DiseaseFull, Group, Year, Month, Date,
                     Cases, Population, Incidence
        """
        if not self._load_config():
            raise RuntimeError(
                "AU NINDSS: failed to capture Power BI auth token via Playwright"
            )

        diseases = self.get_all_diseases()
        if not diseases:
            raise RuntimeError(
                "AU NINDSS: could not retrieve disease list from Power BI"
            )

        now = datetime.now()
        if months is not None:
            months_to_fetch = sorted(set(months))
        else:
            # Default: last 3 months (fast for incremental / smoke-test runs)
            months_to_fetch = []
            for delta in range(3):
                m = now.month - delta
                y = now.year
                if m <= 0:
                    m += 12
                    y -= 1
                months_to_fetch.append((y, m))

        totals = self._fetch_months_concurrent(months_to_fetch, diseases)

        output_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "", "Disease", "DiseaseFull", "Group",
            "Year", "Month", "Date", "Cases", "Population", "Incidence",
        ]
        latest_date: Optional[date] = None
        rows_written = 0

        with output_csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for idx, ((y, m, disease), national) in enumerate(
                sorted(totals.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])),
                start=1,
            ):
                row_date = date(y, m, 1)
                if latest_date is None or row_date > latest_date:
                    latest_date = row_date
                writer.writerow({
                    "": str(idx),
                    "Disease": disease,
                    "DiseaseFull": disease,
                    "Group": "national_total",
                    "Year": str(y),
                    "Month": str(m),
                    "Date": row_date.isoformat(),
                    "Cases": str(national),
                    "Population": "",
                    "Incidence": "",
                })
                rows_written += 1

        logger.info(
            f"[AU-NINDSS] CSV written | path={output_csv} "
            f"rows={rows_written} latest={latest_date}"
        )
        return AUFetchSummary(
            row_count=rows_written,
            latest_date=latest_date,
            csv_url=self.dashboard_url,
        )

    def parse(self, response: Any) -> List[CrawlerResult]:
        return []
