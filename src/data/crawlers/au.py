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

import asyncio
import base64
import csv
import gzip
import html as html_lib
import io
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

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

AU_STATE_SUBDIVISIONS: Dict[str, Dict[str, str]] = {
    "AU-ACT": {
        "source_label": "ACT",
        "name": "Australian Capital Territory",
        "name_zh": "澳大利亚首都领地",
    },
    "AU-NSW": {
        "source_label": "NSW",
        "name": "New South Wales",
        "name_zh": "新南威尔士州",
    },
    "AU-NT": {
        "source_label": "NT",
        "name": "Northern Territory",
        "name_zh": "北领地",
    },
    "AU-QLD": {
        "source_label": "QLD",
        "name": "Queensland",
        "name_zh": "昆士兰州",
    },
    "AU-SA": {
        "source_label": "SA",
        "name": "South Australia",
        "name_zh": "南澳大利亚州",
    },
    "AU-TAS": {
        "source_label": "TAS",
        "name": "Tasmania",
        "name_zh": "塔斯马尼亚州",
    },
    "AU-VIC": {
        "source_label": "VIC",
        "name": "Victoria",
        "name_zh": "维多利亚州",
    },
    "AU-WA": {
        "source_label": "WA",
        "name": "Western Australia",
        "name_zh": "西澳大利亚州",
    },
}

_AU_STATE_ALIASES: Dict[str, str] = {}
for _code, _meta in AU_STATE_SUBDIVISIONS.items():
    for _alias in {
        _code,
        _code.removeprefix("AU-"),
        _meta["source_label"],
        _meta["name"],
    }:
        _AU_STATE_ALIASES[" ".join(_alias.split()).casefold()] = _code
_AU_STATE_ALIASES.update(
    {
        "australian capital territory": "AU-ACT",
        "act": "AU-ACT",
        "new south wales": "AU-NSW",
        "nsw": "AU-NSW",
        "northern territory": "AU-NT",
        "nt": "AU-NT",
        "queensland": "AU-QLD",
        "qld": "AU-QLD",
        "south australia": "AU-SA",
        "sa": "AU-SA",
        "tasmania": "AU-TAS",
        "tas": "AU-TAS",
        "victoria": "AU-VIC",
        "vic": "AU-VIC",
        "western australia": "AU-WA",
        "wa": "AU-WA",
    }
)


def normalize_au_state_code(value: object) -> Optional[str]:
    """Resolve a NINDSS state/territory label to an ISO subdivision code."""

    normalized = " ".join(str(value or "").replace("_", " ").split()).casefold()
    if not normalized:
        return None
    return _AU_STATE_ALIASES.get(normalized)


def au_state_source_label(code: str) -> str:
    """Return the source label used by NINDSS for an AU subdivision code."""

    normalized = str(code or "").strip().upper()
    if normalized not in AU_STATE_SUBDIVISIONS:
        raise ValueError(f"Unsupported Australian state/territory code: {code!r}")
    return AU_STATE_SUBDIVISIONS[normalized]["source_label"]


# ── Helpers (ported from GetDataFunctions.py) ─────────────────────────────────

def _norm_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _parse_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        text = str(value).strip().strip("'\"").replace(",", "")
        if text.endswith(("L", "l")):
            text = text[:-1]
        if not text:
            return None
        if "." in text:
            return int(float(text))
        return int(text)
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
            dm0_like_keys = {"G0", "G", "G1", "G2", "C", "M0", "M", "V", "X", "R"}
            if any(any(k in dm0_like_keys for k in item.keys()) for item in obj):
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
                        results[st] = _parse_int(val) if val is not None else 0
                        if results[st] is None:
                            results[st] = 0
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
                    parsed = _parse_int(v)
                    if parsed is not None:
                        value = parsed
                    else:
                        try:
                            value = float(str(v).strip().strip("'\"").replace(",", ""))
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
            parsed = _parse_int(value)
            if parsed is not None:
                value = parsed
            else:
                try:
                    value = float(value.strip().strip("'\"").replace(",", ""))
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

    def __init__(
        self,
        *,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> None:
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
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir is not None else None
        self._runtime_hints: Dict[str, Any] = {}

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
        Load Power BI auth/config using progressively more tolerant strategies:
          1) intercept live browser traffic via Playwright
          2) extract embed token/config from dashboard HTML
        """
        for attempt in range(1, 3):
            if self._load_config_via_playwright():
                return True
            logger.warning(f"[AU-NINDSS] Playwright token capture attempt {attempt}/2 failed")
        logger.warning(
            "[AU-NINDSS] Playwright token capture unavailable or did not observe a query request; "
            "falling back to HTML embed config extraction"
        )
        if self._load_config_via_html():
            return True
        return False

    def _load_config_via_playwright(self) -> bool:
        """
        Launch a headless browser, navigate to the NINDSS dashboard, and
        intercept the Authorization Bearer token from the first Power BI
        query request.  Populates self._config on success.
        """
        logger.info(
            f"[AU-NINDSS] Starting browser token capture | dashboard={self.dashboard_url}"
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return self._load_config_via_playwright_threaded()

        captured = self._capture_playwright_runtime_config()
        if not captured:
            return False

        return self._set_runtime_config(
            token=captured.get("token", ""),
            api_url=captured.get("url", ""),
            report_id=captured.get("report_id") or _REPORT_ID,
            dataset_id=_DATASET_ID,
            model_id=_MODEL_ID,
            log_source="playwright",
            runtime_hints=captured.get("runtime_hints"),
        )

    def _load_config_via_playwright_threaded(self) -> bool:
        captured: Dict[str, Any] = {}
        errors: List[BaseException] = []

        def runner() -> None:
            try:
                captured.update(self._capture_playwright_runtime_config() or {})
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(
            target=runner,
            name="au-nindss-playwright",
            daemon=True,
        )
        thread.start()
        thread.join()

        if errors:
            exc = errors[0]
            logger.error(f"[AU-NINDSS] Browser token capture failed | error={exc}")
            return False
        if not captured:
            return False

        logger.info("[AU-NINDSS] Playwright token capture executed in helper thread")
        return self._set_runtime_config(
            token=captured.get("token", ""),
            api_url=captured.get("url", ""),
            report_id=captured.get("report_id") or _REPORT_ID,
            dataset_id=_DATASET_ID,
            model_id=_MODEL_ID,
            log_source="playwright-thread",
            runtime_hints=captured.get("runtime_hints"),
        )

    def _capture_playwright_runtime_config(self) -> Optional[Dict[str, Any]]:
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
                        runtime_hints = self._extract_runtime_hints_from_post_data(
                            getattr(req, "post_data", None)
                        )
                        if runtime_hints:
                            merged_hints = captured.setdefault("runtime_hints", {})
                            if self._should_replace_location_hint(
                                merged_hints.get("location"),
                                runtime_hints.get("location"),
                            ):
                                merged_hints["location"] = runtime_hints["location"]

                page.on("request", on_request)
                page.goto(self.dashboard_url, timeout=90_000)

                # The portal first loads a Power BI embed iframe, then the iframe
                # triggers the dedicated QES requests carrying the MWCToken.
                # Waiting for the iframe plus a longer grace period is noticeably
                # more reliable than only sleeping after `goto`.
                try:
                    page.locator("iframe").first.wait_for(timeout=20_000)
                except Exception:
                    logger.debug("[AU-NINDSS] Power BI iframe did not appear before timeout")

                waited = 0
                while waited < 60 and "token" not in captured:
                    page.wait_for_timeout(1_000)
                    waited += 1
                    if waited == 20 and "token" not in captured:
                        try:
                            page.reload(timeout=90_000)
                        except Exception:
                            logger.debug("[AU-NINDSS] Page reload during token capture failed")

                browser.close()

        except ImportError:
            logger.warning(
                "[AU-NINDSS] playwright not installed — "
                "run: pip install playwright && playwright install chromium"
            )
            return False
        except Exception as exc:
            logger.error(f"[AU-NINDSS] Browser token capture failed | error={exc}")
            return None

        if not captured.get("token"):
            logger.error("[AU-NINDSS] Timed out waiting for token from browser traffic")
            return None

        self._maybe_save_auth_capture(captured)
        return captured

    def _extract_runtime_hints_from_post_data(self, post_data: Optional[str]) -> Dict[str, Any]:
        if not post_data:
            return {}
        try:
            payload = json.loads(post_data)
        except Exception:
            return {}

        queries = payload.get("queries", [])
        for query_entry in queries:
            commands = (
                query_entry.get("Query", {})
                .get("Commands", [])
            )
            for command in commands:
                semantic = command.get("SemanticQueryDataShapeCommand", {})
                query = semantic.get("Query", {})
                from_items = query.get("From", [])
                entities = {
                    _norm_text(item.get("Name")): _norm_text(item.get("Entity"))
                    for item in from_items
                    if isinstance(item, dict)
                }
                location_alias = next(
                    (name for name, entity in entities.items() if entity == "DELTALOAD_DATAMART LOCATION_DIM"),
                    "",
                )
                fact_alias = next(
                    (
                        name
                        for name, entity in entities.items()
                        if entity == "DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT"
                    ),
                    "",
                )
                disease_alias = next(
                    (name for name, entity in entities.items() if entity == "DELTALOAD_DATAMART DISEASE_DIM"),
                    "",
                )
                case_alias = next(
                    (name for name, entity in entities.items() if entity == "DELTALOAD_DATAMART CASE_DIM"),
                    "",
                )
                if not location_alias or not fact_alias:
                    continue

                state_seen = False
                measure_property = ""
                for select_item in query.get("Select", []):
                    column = select_item.get("Column", {})
                    column_expr = column.get("Expression", {}).get("SourceRef", {})
                    if (
                        _norm_text(column_expr.get("Source")) == location_alias
                        and _norm_text(column.get("Property")) == "STATE"
                    ):
                        state_seen = True
                    measure = select_item.get("Measure", {})
                    measure_expr = measure.get("Expression", {}).get("SourceRef", {})
                    if _norm_text(measure_expr.get("Source")) == fact_alias:
                        measure_property = _norm_text(measure.get("Property"))

                if state_seen and measure_property:
                    score = 0
                    if measure_property == "Count_Notification":
                        score += 10
                    if measure_property.lower() == "count_notification":
                        score += 5
                    if not any("HierarchyLevel" in item for item in query.get("Select", [])):
                        score += 3
                    return {
                        "location": {
                            "fact_alias": fact_alias,
                            "location_alias": location_alias,
                            "disease_alias": disease_alias or "d11",
                            "case_alias": case_alias or "d3",
                            "state_property": "STATE",
                            "measure_property": measure_property,
                            "_score": score,
                        }
                    }
        return {}

    @staticmethod
    def _should_replace_location_hint(
        current: Optional[Dict[str, Any]],
        candidate: Optional[Dict[str, Any]],
    ) -> bool:
        if not isinstance(candidate, dict):
            return False
        if not isinstance(current, dict):
            return True
        return int(candidate.get("_score", 0)) > int(current.get("_score", 0))

    def _maybe_save_auth_capture(self, captured: Dict[str, Any]) -> None:
        if not self.save_raw or self.raw_dir is None:
            return
        auth_dir = self.raw_dir / "_auth"
        auth_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "dashboard_url": self.dashboard_url,
            "api_url": captured.get("url"),
            "report_id": captured.get("report_id") or _REPORT_ID,
            "runtime_hints": captured.get("runtime_hints", {}),
            "token_prefix": _norm_text(captured.get("token"))[:24],
        }
        (auth_dir / "latest_capture.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_config_via_html(self) -> bool:
        if not self._http:
            self._http = self._make_http_session()
        try:
            resp = self._http.get(self.dashboard_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[AU-NINDSS] Failed to fetch dashboard HTML | error={exc}")
            return False

        extracted = self._extract_config_from_html(resp.text)
        if not extracted:
            logger.error("[AU-NINDSS] No Power BI embed config found in dashboard HTML")
            return False

        return self._set_runtime_config(
            token=extracted.get("accessToken", ""),
            api_url=extracted.get("apiUrl", ""),
            report_id=extracted.get("reportId") or _REPORT_ID,
            dataset_id=extracted.get("datasetId") or _DATASET_ID,
            model_id=extracted.get("modelId") or _MODEL_ID,
            log_source="html",
        )

    def _extract_config_from_html(self, html_content: str) -> Optional[Dict[str, Any]]:
        access_token = ""
        api_url = ""
        report_id = ""
        dataset_id = ""
        model_id: Optional[int] = None

        match = re.search(r"var\s+pbiAppConfig\s*=\s*({.*?});", html_content, re.S)
        if match:
            try:
                app_cfg = json.loads(match.group(1))
                access_token = _norm_text(app_cfg.get("accessToken"))
                report_id = _norm_text(app_cfg.get("reportId"))
                api_url = self._derive_api_url_from_embed_url(_norm_text(app_cfg.get("embedUrl")))
            except json.JSONDecodeError:
                logger.debug("[AU-NINDSS] Failed to decode pbiAppConfig JSON from HTML")

        if not access_token:
            match = re.search(r'embedconfig="([^"]+)"', html_content, re.I)
            if match:
                try:
                    cfg = self._decode_embed_config(match.group(1))
                    embed_token = cfg.get("EmbedToken", {}) if isinstance(cfg, dict) else {}
                    access_token = _norm_text(embed_token.get("token"))
                    report_id = report_id or _norm_text(cfg.get("Id") or cfg.get("id"))
                    api_url = api_url or self._derive_api_url_from_embed_url(
                        _norm_text(cfg.get("EmbedUrl"))
                    )
                except Exception as exc:
                    logger.debug(f"[AU-NINDSS] Failed to decode embedconfig | error={exc}")

        if not access_token:
            match = (
                re.search(r'"accessToken"\s*:\s*"([^"]+)"', html_content)
                or re.search(r"'accessToken'\s*:\s*'([^']+)'", html_content)
            )
            if match:
                access_token = _norm_text(match.group(1))

        if not access_token:
            return None

        # The static portal HTML currently exposes an embed token (often
        # starting with `H4s...`) that is not accepted by the QES public/query
        # endpoint. Only accept HTML extraction when it looks like the older
        # JWT-style access token used by the direct API path.
        if not access_token.startswith("ey"):
            logger.warning(
                f"[AU-NINDSS] Ignoring HTML-extracted token with unsupported format "
                f"prefix={access_token[:5]!r}"
            )
            return None

        return {
            "accessToken": access_token,
            "apiUrl": api_url,
            "reportId": report_id,
            "datasetId": dataset_id,
            "modelId": model_id,
        }

    def _decode_embed_config(self, raw_embed_config: str) -> Dict[str, Any]:
        encoded = html_lib.unescape(raw_embed_config).strip()
        padding = (-len(encoded)) % 4
        if padding:
            encoded += "=" * padding
        try:
            decoded = base64.b64decode(encoded)
        except Exception:
            decoded = base64.urlsafe_b64decode(encoded)

        if decoded.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(decoded)) as gz:
                decoded = gz.read()

        return json.loads(decoded.decode("utf-8"))

    def _derive_api_url_from_embed_url(self, embed_url: str) -> str:
        if not embed_url:
            return ""
        try:
            parsed = urlparse(embed_url)
            config_param = parse_qs(parsed.query).get("config", [])
            if not config_param:
                return ""
            cfg_raw = unquote(config_param[0])
            cfg = json.loads(cfg_raw)
            cluster_url = _norm_text(cfg.get("clusterUrl"))
            if cluster_url:
                parsed_cluster = urlparse(cluster_url)
                if parsed_cluster.scheme and parsed_cluster.netloc:
                    return (
                        f"{parsed_cluster.scheme}://{parsed_cluster.netloc}"
                        f"/webapi/capacities/{self.capacity_id}"
                        "/workloads/QES/QueryExecutionService/automatic/public/query"
                    )
        except Exception as exc:
            logger.debug(f"[AU-NINDSS] Failed to derive apiUrl from embed URL | error={exc}")
        return ""

    def _default_api_url(self) -> str:
        cap_slug = self.capacity_id.replace("-", "").lower()
        return (
            f"https://{cap_slug}.pbidedicated.windows.net"
            f"/webapi/capacities/{self.capacity_id}"
            "/workloads/QES/QueryExecutionService/automatic/public/query"
        )

    def _set_runtime_config(
        self,
        token: str,
        api_url: str,
        report_id: str,
        dataset_id: Any,
        model_id: Any,
        log_source: str,
        runtime_hints: Optional[Dict[str, Any]] = None,
    ) -> bool:
        token = _norm_text(token)
        if token.lower().startswith("bearer "):
            token = token.split(" ", 1)[1]
        if not token:
            return False

        api_url = _norm_text(api_url) or self._default_api_url()
        if api_url:
            parsed = urlparse(api_url)
            if not (parsed.scheme and parsed.netloc):
                api_url = self._default_api_url()

        parsed_model_id = _parse_int(model_id)
        self._config = {
            "accessToken": token,
            "apiUrl": api_url,
            "reportId": _norm_text(report_id) or _REPORT_ID,
            "datasetId": _norm_text(dataset_id) or _DATASET_ID,
            "modelId": parsed_model_id if parsed_model_id is not None else _MODEL_ID,
        }
        self._runtime_hints = runtime_hints or self._runtime_hints or {}
        if not self._http:
            self._http = self._make_http_session()

        logger.info(
            f"[AU-NINDSS] Token/config loaded via {log_source} | "
            f"token_prefix={token[:15]}... api_url={api_url[:60]}..."
        )
        location_measure = (
            self._runtime_hints.get("location", {}).get("measure_property")
            if isinstance(self._runtime_hints.get("location"), dict)
            else None
        )
        if location_measure:
            logger.info(f"[AU-NINDSS] Runtime location measure detected | measure={location_measure}")
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
        location_hints = (
            self._runtime_hints.get("location", {})
            if isinstance(self._runtime_hints.get("location"), dict)
            else {}
        )
        fact_alias = _norm_text(location_hints.get("fact_alias")) or "d1"
        location_alias = _norm_text(location_hints.get("location_alias")) or "d"
        disease_alias = _norm_text(location_hints.get("disease_alias")) or "d11"
        case_alias = _norm_text(location_hints.get("case_alias")) or "d3"
        state_property = _norm_text(location_hints.get("state_property")) or "STATE"
        measure_property = _norm_text(location_hints.get("measure_property")) or "Count_Notification"
        return {
            "version": "1.0.0",
            "queries": [{
                "Query": {
                    "Commands": [{
                        "SemanticQueryDataShapeCommand": {
                            "Query": {
                                "Version": 2,
                                "From": [
                                    {"Name": fact_alias, "Entity": "DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT", "Type": 0},
                                    {"Name": location_alias,  "Entity": "DELTALOAD_DATAMART LOCATION_DIM", "Type": 0},
                                    {"Name": disease_alias, "Entity": "DELTALOAD_DATAMART DISEASE_DIM", "Type": 0},
                                    {"Name": case_alias, "Entity": "DELTALOAD_DATAMART CASE_DIM", "Type": 0},
                                ],
                                "Select": [
                                    {
                                        "Column": {
                                            "Expression": {"SourceRef": {"Source": location_alias}},
                                            "Property": state_property,
                                        },
                                        "Name": "DELTALOAD_DATAMART LOCATION_DIM.STATE",
                                    },
                                    {
                                        "Measure": {
                                            "Expression": {"SourceRef": {"Source": fact_alias}},
                                            "Property": measure_property,
                                        },
                                        "Name": "DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT.M_Notification",
                                    },
                                ],
                                "Where": [
                                    {"Condition": {"Not": {"Expression": {"In": {
                                        "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": location_alias}}, "Property": state_property}}],
                                        "Values": [
                                            [{"Literal": {"Value": "'AUS'"}}],
                                            [{"Literal": {"Value": "'Unknown'"}}],
                                        ],
                                    }}}}},
                                    {"Condition": {"In": {
                                        "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": disease_alias}}, "Property": "DISEASE NAME"}}],
                                        "Values": [[{"Literal": {"Value": f"'{disease}'"}}]],
                                    }}},
                                    {"Condition": {"In": {
                                        "Expressions": [
                                            {"Column": {"Expression": {"SourceRef": {"Source": fact_alias}}, "Property": "DIAGNOSIS_YEAR_HIERARCHY"}},
                                            {"Column": {"Expression": {"SourceRef": {"Source": fact_alias}}, "Property": "DIAGNOSIS_QUARTER"}},
                                            {"Column": {"Expression": {"SourceRef": {"Source": fact_alias}}, "Property": "DIAGNOSIS_MONTHNAME"}},
                                        ],
                                        "Values": [[
                                            {"Literal": {"Value": f"'{year}'"}},
                                            {"Literal": {"Value": f"'{quarter}'"}},
                                            {"Literal": {"Value": f"'{month_name}'"}},
                                        ]],
                                    }}},
                                    {"Condition": {"Comparison": {
                                        "ComparisonKind": 1,
                                        "Left": {"Column": {"Expression": {"SourceRef": {"Source": fact_alias}}, "Property": "DAX_Year"}},
                                        "Right": {"Literal": {"Value": "1990L"}},
                                    }}},
                                    {"Condition": {"In": {
                                        "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": case_alias}}, "Property": "CONFIRMATION_STATUS"}}],
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

    def _archive_month_fetch(
        self,
        *,
        year: int,
        month: int,
        disease: str,
        payload: Dict[str, Any],
        raw: Optional[Dict[str, Any]],
        parsed_counts: Optional[Dict[str, int]],
    ) -> None:
        if not self.save_raw or self.raw_dir is None:
            return
        month_dir = self.raw_dir / f"{year}" / f"{month:02d}"
        month_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[^A-Za-z0-9._-]+', "_", disease).strip("_") or "unknown_disease"
        archive = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "disease": disease,
            "year": year,
            "month": month,
            "dashboard_url": self.dashboard_url,
            "api_url": self._config.get("apiUrl") if self._config else None,
            "runtime_hints": self._runtime_hints,
            "request_payload": payload,
            "response_json": raw,
            "parsed_counts": parsed_counts,
        }
        (month_dir / f"{safe_name}.json").write_text(
            json.dumps(archive, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _fetch_month_disease(
        self, year: int, month: int, disease: str
    ) -> Optional[Dict[str, int]]:
        """Fetch {state: count} for one disease/month. Returns None on failure."""
        quarter = _quarter_for_month(month)
        mname = _month_name(month)
        payload = self._build_location_payload(str(year), quarter, mname, disease)
        dm0, raw = self._execute_payload(payload)
        if dm0 is None or raw is None:
            self._archive_month_fetch(
                year=year,
                month=month,
                disease=disease,
                payload=payload,
                raw=raw,
                parsed_counts=None,
            )
            return None
        parsed_counts = _parse_dm0_to_state_counts(dm0, raw, str(year))
        self._archive_month_fetch(
            year=year,
            month=month,
            disease=disease,
            payload=payload,
            raw=raw,
            parsed_counts=parsed_counts,
        )
        return parsed_counts

    # ── Internal sync fetch (shared by crawl() and crawl_monthly_national_csv) ─

    def _fetch_months_concurrent_state_counts(
        self,
        months_to_fetch: List[Tuple[int, int]],
        diseases: List[str],
    ) -> Dict[Tuple[int, int, str, str], int]:
        """
        Fetch state/territory counts for every disease × month combination.

        Returns {(year, month, disease, jurisdiction_code): state_total}.
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
        totals: Dict[Tuple[int, int, str, str], int] = {}
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
                        for raw_state, value in state_counts.items():
                            raw_state_text = _norm_text(raw_state)
                            if raw_state_text.upper() in _SKIP_STATES:
                                continue
                            jurisdiction_code = normalize_au_state_code(raw_state_text)
                            if jurisdiction_code is None:
                                parsed_value = _parse_int(value)
                                if parsed_value:
                                    raise ValueError(
                                        "AU NINDSS returned an unknown non-empty "
                                        f"state/territory label: {raw_state_text!r}"
                                    )
                                continue
                            parsed_value = _parse_int(value)
                            if parsed_value is None:
                                continue
                            # Preserve explicit zero-count disease/month rows.
                            # A confirmed 0 is materially different from "missing".
                            totals[(y, m, dis, jurisdiction_code)] = int(parsed_value)
                except ValueError:
                    raise
                except Exception as exc:
                    logger.debug(
                        f"[AU-NINDSS] fetch failed | "
                        f"{y}-{m:02d} {dis} error={exc}"
                    )
                if done % 50 == 0:
                    logger.info(f"[AU-NINDSS] Progress | {done}/{len(tasks)}")
        return totals

    def _fetch_months_concurrent(
        self,
        months_to_fetch: List[Tuple[int, int]],
        diseases: List[str],
    ) -> Dict[Tuple[int, int, str], int]:
        """
        Fetch national totals for every disease x month combination.

        Returns {(year, month, disease): national_total}.
        """
        state_totals = self._fetch_months_concurrent_state_counts(
            months_to_fetch,
            diseases,
        )
        national_totals: Dict[Tuple[int, int, str], int] = {}
        for (year, month, disease, _state_code), value in state_totals.items():
            key = (year, month, disease)
            national_totals[key] = national_totals.get(key, 0) + int(value)
        return national_totals

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
                "AU NINDSS: failed to capture Power BI auth token via Playwright after retries"
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
                "AU NINDSS: failed to capture Power BI auth token via Playwright after retries"
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

    def crawl_monthly_subdivision_csv(
        self,
        output_csv: Path,
        *,
        jurisdiction_code: str,
        months: Optional[List[Tuple[int, int]]] = None,
    ) -> AUFetchSummary:
        """
        Fetch NINDSS disease data and write one state/territory CSV.

        The facts remain monthly case notifications for the whole published
        subdivision jurisdiction, represented as ``country:AU-XX:national``.
        """

        target_code = str(jurisdiction_code or "").strip().upper()
        if target_code not in AU_STATE_SUBDIVISIONS:
            raise ValueError(
                f"Unsupported Australian state/territory code: {jurisdiction_code}"
            )
        target_meta = AU_STATE_SUBDIVISIONS[target_code]

        if not self._load_config():
            raise RuntimeError(
                "AU NINDSS: failed to capture Power BI auth token via Playwright after retries"
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
            months_to_fetch = []
            for delta in range(3):
                m = now.month - delta
                y = now.year
                if m <= 0:
                    m += 12
                    y -= 1
                months_to_fetch.append((y, m))

        totals = self._fetch_months_concurrent_state_counts(months_to_fetch, diseases)

        output_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "",
            "Disease",
            "DiseaseFull",
            "Group",
            "Year",
            "Month",
            "Date",
            "Cases",
            "Population",
            "Incidence",
            "JurisdictionCode",
            "ParentCountryCode",
            "LocationType",
            "ReportingArea",
            "Geocode",
            "GeographyKey",
        ]
        latest_date: Optional[date] = None
        rows_written = 0

        with output_csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            rows = [
                ((y, m, disease), value)
                for (y, m, disease, state_code), value in totals.items()
                if state_code == target_code
            ]
            for idx, ((y, m, disease), state_total) in enumerate(
                sorted(rows, key=lambda x: (x[0][0], x[0][1], x[0][2])),
                start=1,
            ):
                row_date = date(y, m, 1)
                if latest_date is None or row_date > latest_date:
                    latest_date = row_date
                writer.writerow(
                    {
                        "": str(idx),
                        "Disease": disease,
                        "DiseaseFull": disease,
                        "Group": "state_territory_total",
                        "Year": str(y),
                        "Month": str(m),
                        "Date": row_date.isoformat(),
                        "Cases": str(state_total),
                        "Population": "",
                        "Incidence": "",
                        "JurisdictionCode": target_code,
                        "ParentCountryCode": "AU",
                        "LocationType": "subdivision",
                        "ReportingArea": target_meta["name"],
                        "Geocode": target_code,
                        "GeographyKey": f"country:{target_code}:national",
                    }
                )
                rows_written += 1

        logger.info(
            f"[AU-NINDSS] Subdivision CSV written | path={output_csv} "
            f"jurisdiction={target_code} rows={rows_written} latest={latest_date}"
        )
        return AUFetchSummary(
            row_count=rows_written,
            latest_date=latest_date,
            csv_url=self.dashboard_url,
        )

    def crawl_monthly_subdivision_csvs(
        self,
        output_dir: Path,
        *,
        jurisdiction_codes: Optional[List[str]] = None,
        months: Optional[List[Tuple[int, int]]] = None,
    ) -> Dict[str, AUFetchSummary]:
        """Fetch NINDSS once and write CSVs for multiple state/territory codes."""

        target_codes = [
            str(code or "").strip().upper()
            for code in (jurisdiction_codes or list(AU_STATE_SUBDIVISIONS))
        ]
        invalid = [code for code in target_codes if code not in AU_STATE_SUBDIVISIONS]
        if invalid:
            raise ValueError(
                "Unsupported Australian state/territory code(s): "
                + ", ".join(invalid)
            )

        if not self._load_config():
            raise RuntimeError(
                "AU NINDSS: failed to capture Power BI auth token via Playwright after retries"
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
            months_to_fetch = []
            for delta in range(3):
                m = now.month - delta
                y = now.year
                if m <= 0:
                    m += 12
                    y -= 1
                months_to_fetch.append((y, m))

        totals = self._fetch_months_concurrent_state_counts(months_to_fetch, diseases)
        output_dir.mkdir(parents=True, exist_ok=True)

        summaries: Dict[str, AUFetchSummary] = {}
        fieldnames = [
            "",
            "Disease",
            "DiseaseFull",
            "Group",
            "Year",
            "Month",
            "Date",
            "Cases",
            "Population",
            "Incidence",
            "JurisdictionCode",
            "ParentCountryCode",
            "LocationType",
            "ReportingArea",
            "Geocode",
            "GeographyKey",
        ]
        for target_code in target_codes:
            target_meta = AU_STATE_SUBDIVISIONS[target_code]
            output_csv = output_dir / f"{target_code.lower()}_nindss_monthly.csv"
            latest_date: Optional[date] = None
            rows_written = 0
            with output_csv.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                rows = [
                    ((y, m, disease), value)
                    for (y, m, disease, state_code), value in totals.items()
                    if state_code == target_code
                ]
                for idx, ((y, m, disease), state_total) in enumerate(
                    sorted(rows, key=lambda x: (x[0][0], x[0][1], x[0][2])),
                    start=1,
                ):
                    row_date = date(y, m, 1)
                    if latest_date is None or row_date > latest_date:
                        latest_date = row_date
                    writer.writerow(
                        {
                            "": str(idx),
                            "Disease": disease,
                            "DiseaseFull": disease,
                            "Group": "state_territory_total",
                            "Year": str(y),
                            "Month": str(m),
                            "Date": row_date.isoformat(),
                            "Cases": str(state_total),
                            "Population": "",
                            "Incidence": "",
                            "JurisdictionCode": target_code,
                            "ParentCountryCode": "AU",
                            "LocationType": "subdivision",
                            "ReportingArea": target_meta["name"],
                            "Geocode": target_code,
                            "GeographyKey": f"country:{target_code}:national",
                        }
                    )
                    rows_written += 1
            summaries[target_code] = AUFetchSummary(
                row_count=rows_written,
                latest_date=latest_date,
                csv_url=self.dashboard_url,
            )

        logger.info(
            f"[AU-NINDSS] Subdivision CSV batch written | "
            f"jurisdictions={len(summaries)} output_dir={output_dir}"
        )
        return summaries

    def parse(self, response: Any) -> List[CrawlerResult]:
        return []
