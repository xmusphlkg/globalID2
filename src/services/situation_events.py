"""Official event and CDC respiratory data adapters for Situation Room v2."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import httpx
import pandas as pd
import pycountry
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.database import get_db
from src.core.disease_mutation_lock import acquire_disease_data_mutation_lock
from src.domain import (
    DiseaseSeriesObservation,
    DiseaseSurveillanceSeries,
    PublicHealthEvent,
)


ROOT = Path(__file__).resolve().parents[2]
WHO_DON_API_URL = "https://www.who.int/api/hubs/diseaseoutbreaknews"
EVENT_SOURCE_URLS = {
    "who_don": WHO_DON_API_URL,
    "ecdc_cdtr": "https://www.ecdc.europa.eu/en/publications-and-data/monitoring/weekly-threats-reports",
    "africa_cdc_ebs": "https://africacdc.org/document-tag/ebs-weekly-report/",
    "paho_alerts": "https://www.paho.org/en/epidemiological-alerts-and-updates",
}
CDC_DATASETS = {
    "positivity": "https://data.cdc.gov/resource/seuz-s2cv.json",
    "hospital_admissions": "https://data.cdc.gov/resource/vdzy-6i9v.json",
    "ari_activity": "https://data.cdc.gov/resource/f3zz-zga5.json",
}

CDC_RESPIRATORY_DISEASES = {
    "Influenza": {
        "disease_id": "D038",
        "disease_name": "Influenza",
        "slug": "influenza",
        "admission": "totalconfflunewadm",
    },
    "RSV": {
        "disease_id": "D142",
        "disease_name": "Respiratory syncytial virus infection (RSV)",
        "slug": "respiratory-syncytial-virus-infection-rsv",
        "admission": "totalconfrsvnewadm",
    },
    "COVID-19": {
        "disease_id": "D004",
        "disease_name": "COVID-19",
        "slug": "covid-19",
        "admission": "totalconfc19newadm",
    },
}


def source_adapter_registry() -> list[dict[str, Any]]:
    """Stable control-plane catalogue of the Situation acquisition adapters."""
    event_labels = {
        "who_don": ("WHO Disease Outbreak News", "Official JSON API", True),
        "ecdc_cdtr": ("ECDC Communicable Disease Threats Report", "Official report index", True),
        "africa_cdc_ebs": ("Africa CDC Event-Based Surveillance", "Official report index", True),
        "paho_alerts": ("PAHO Epidemiological Alerts", "Allowed official feed/index only", True),
    }
    rows = [
        {
            "source_id": source_id,
            "health_key": source_id,
            "label": label,
            "source_kind": "official_event",
            "transport": transport,
            "url": EVENT_SOURCE_URLS[source_id],
            "stale_policy_hours": 72,
            "automatic_publication": auto_publish,
            "analysis_source_system": None,
            "contract": (
                ["DonId", "Title", "PublicationDate", "LastModified", "ItemDefaultUrl"]
                if source_id == "who_don"
                else ["stable URL", "title", "publication date"]
            ),
        }
        for source_id, (label, transport, auto_publish) in event_labels.items()
    ]
    cdc_labels = {
        "positivity": ("CDC Respiratory Test Positivity", "test_positivity", "SRC_US_CDC_RESP_NREVSS"),
        "hospital_admissions": ("CDC Respiratory Hospital Admissions", "hospitalized_case_notifications", "SRC_US_CDC_RESP_NHSN"),
        "ari_activity": ("CDC Acute Respiratory Illness Activity", "acute_respiratory_illness_activity", None),
    }
    rows.extend(
        {
            "source_id": f"cdc_{source_id}",
            "health_key": f"cdc_{source_id}",
            "label": label,
            "source_kind": "respiratory_metric",
            "transport": "CDC Socrata API",
            "url": CDC_DATASETS[source_id],
            "stale_policy_hours": 72,
            "automatic_publication": True,
            "analysis_source_system": analysis_source_system,
            "contract": [metric, "data_through", "jurisdiction", "source_url"],
        }
        for source_id, (label, metric, analysis_source_system) in cdc_labels.items()
    )
    return rows


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError):
        return None


def _extract_date(text_value: str) -> str | None:
    match = re.search(
        r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2})\b",
        text_value,
    )
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def parse_who_don(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the documented WHO DON hub response without retaining article text."""
    records: list[dict[str, Any]] = []
    for item in payload.get("value", []):
        external_id = str(item.get("DonId") or item.get("Id") or "").strip()
        title = str(item.get("Title") or "").strip()
        source_url = str(item.get("ItemDefaultUrl") or "").strip()
        if source_url.startswith("/"):
            source_url = urljoin("https://www.who.int", source_url)
        if not external_id or not title or not source_url.startswith("http"):
            continue
        records.append(
            {
                "source": "who_don",
                "external_id": external_id,
                "source_url": source_url,
                "title": title[:1000],
                "published_at": _iso_date(item.get("PublicationDate")),
                "updated_at_source": _iso_date(item.get("LastModified")),
                "agency_risk": None,
            }
        )
    return records


def _parse_official_index(source: str, html: str, base_url: str, required_path: str | None = None) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.select("article a[href], h2 a[href], h3 a[href], .card a[href], .views-row a[href]"):
        title = link.get_text(" ", strip=True)
        href = urljoin(base_url, str(link.get("href") or ""))
        if len(title) < 8 or not href.startswith("http") or href in seen:
            continue
        if required_path and required_path not in href:
            continue
        seen.add(href)
        parent_text = link.parent.get_text(" ", strip=True) if link.parent else ""
        risk_match = re.search(r"\brisk(?:\s+(?:is|level|assessment)?\s*(?:of|:)?\s*)(low|moderate|high|very high)\b", parent_text, re.IGNORECASE)
        records.append(
            {
                "source": source,
                "external_id": href.rstrip("/").rsplit("/", 1)[-1],
                "source_url": href,
                "title": title[:1000],
                "published_at": _extract_date(parent_text),
                "updated_at_source": None,
                "agency_risk": risk_match.group(1).lower() if risk_match else None,
            }
        )
    return records[:40]


def parse_ecdc_cdtr(html: str, base_url: str = EVENT_SOURCE_URLS["ecdc_cdtr"]) -> list[dict[str, Any]]:
    return _parse_official_index("ecdc_cdtr", html, base_url)


def parse_africa_cdc_ebs(html: str, base_url: str = EVENT_SOURCE_URLS["africa_cdc_ebs"]) -> list[dict[str, Any]]:
    return _parse_official_index("africa_cdc_ebs", html, base_url)


def parse_paho_alerts(html: str, base_url: str = EVENT_SOURCE_URLS["paho_alerts"]) -> list[dict[str, Any]]:
    return _parse_official_index("paho_alerts", html, base_url)


DISEASE_ALIASES: dict[str, list[str]] = {
    "D004": ["covid-19", "covid 19", "sars-cov-2"],
    "D014": ["avian influenza a h5n1", "h5n1"],
    "D015": ["avian influenza a h7n9", "h7n9"],
    "D016": ["novel influenza a", "avian influenza a h9n2", "h9n2"],
    "D038": ["influenza", "flu"],
    "D050": ["ebola", "ebola virus disease"],
    "D065": ["monkeypox", "mpox"],
    "D142": ["respiratory syncytial virus", "rsv"],
}
COUNTRY_DISPLAY_NAMES = {"CD": "Democratic Republic of the Congo", "CG": "Republic of the Congo"}
COUNTRY_ALIASES: dict[str, list[str]] = {
    "CD": ["democratic republic of the congo", "dr congo", "drc"],
    "CG": ["republic of the congo"],
    "GB": ["united kingdom", "uk"],
    "US": ["united states", "united states of america", "usa"],
    "CI": ["côte d’ivoire", "côte d'ivoire", "ivory coast"],
}


def load_disease_catalogue() -> list[dict[str, Any]]:
    path = ROOT / "configs" / "standard_diseases.csv"
    if not path.exists():
        return []
    records = pd.read_csv(path).fillna("").to_dict("records")
    for row in records:
        row["name_en"] = row.get("standard_name_en") or row.get("name_en") or ""
        row["name_zh"] = row.get("standard_name_zh") or row.get("name_zh") or ""
        row["aliases"] = DISEASE_ALIASES.get(str(row.get("disease_id")), [])
    return records


def _country_names() -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for country in pycountry.countries:
        values = {country.name.lower()}
        for attribute in ("official_name", "common_name"):
            value = getattr(country, attribute, None)
            if value:
                values.add(str(value).lower())
        values.update(COUNTRY_ALIASES.get(country.alpha_2, []))
        names[country.alpha_2] = sorted(values, key=len, reverse=True)
    return names


def map_event(title: str, disease_catalogue: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    normalized = " " + re.sub(r"[^a-z0-9]+", " ", title.lower()).strip() + " "
    disease_matches: list[tuple[int, dict[str, Any]]] = []
    for row in disease_catalogue:
        names = [row.get("name_en"), *(row.get("aliases") or [])]
        for name in names:
            candidate = re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()
            if len(candidate) >= 3 and f" {candidate} " in normalized:
                disease_matches.append((len(candidate), row))
                break
    disease = max(disease_matches, key=lambda match: match[0])[1] if disease_matches else None
    if disease and disease.get("disease_id") == "D038" and " avian influenza " in normalized:
        # Generic seasonal influenza is not an exact mapping for an avian
        # subtype. Known subtypes above win by their longer exact alias;
        # unknown avian subtypes stay candidates.
        disease = None
    geography_matches: list[tuple[int, int, int, str, str]] = []
    for code, aliases in _country_names().items():
        for alias in aliases:
            needle = f" {re.sub(r'[^a-z0-9]+', ' ', alias).strip()} "
            start = normalized.find(needle)
            if start >= 0:
                country = pycountry.countries.get(alpha_2=code)
                display_name = COUNTRY_DISPLAY_NAMES.get(code) or (country.name if country else code)
                geography_matches.append((len(needle), start, start + len(needle), code, display_name))
                break
    geographies: list[dict[str, str]] = []
    selected_spans: list[tuple[int, int]] = []
    for _, start, end, code, name in sorted(geography_matches, reverse=True):
        if any(start >= selected_start and end <= selected_end for selected_start, selected_end in selected_spans):
            continue
        selected_spans.append((start, end))
        geographies.append({"code": code, "name": name})
    geographies.sort(key=lambda item: item["code"])
    # "Congo" alone is ambiguous and must not auto-publish.
    if " congo " in normalized and not any(code in {"CD", "CG"} for code in [row["code"] for row in geographies]):
        geographies = []
    return disease, geographies


def _event_key(disease: dict[str, Any] | None, geographies: list[dict[str, str]], published_at: str | None) -> str | None:
    if not disease or not geographies:
        return None
    day = date.fromisoformat(published_at) if published_at else utc_now().date()
    bucket = day - timedelta(days=day.toordinal() % 45)
    return f"{disease.get('disease_id')}|{','.join(sorted(item['code'] for item in geographies))}|{bucket.isoformat()}"


def normalize_event(item: dict[str, Any], catalogue: Iterable[dict[str, Any]]) -> dict[str, Any]:
    disease, geographies = map_event(str(item.get("title") or ""), catalogue)
    required = all(item.get(field) for field in ("external_id", "title", "published_at", "source_url"))
    high_confidence = bool(required and disease and geographies)
    return {
        **item,
        "disease_id": disease.get("disease_id") if disease else None,
        "disease_name": disease.get("name_en") if disease else None,
        "geographies": geographies,
        "confidence": "high" if high_confidence else "low",
        "status": "published" if high_confidence else "candidate",
        "content_hash": _text_hash(str(item.get("title")) + str(item.get("source_url"))),
        "event_key": _event_key(disease, geographies, item.get("published_at")),
    }


async def fetch_external_events(config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    catalogue = load_disease_catalogue()
    results: list[dict[str, Any]] = []
    health: dict[str, dict[str, Any]] = {}
    headers = {"User-Agent": "GIDS Situation Room/2.0 (+https://globalinfectiousdisease.com/situation/methodology/)"}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        for source in config.get("event_sources", EVENT_SOURCE_URLS):
            url = EVENT_SOURCE_URLS.get(source)
            if not url:
                continue
            checked_at = utc_now().isoformat()
            try:
                if source == "who_don":
                    response = await client.get(url, params={"$top": 50, "$orderby": "PublicationDate desc"})
                    response.raise_for_status()
                    candidates = parse_who_don(response.json())
                else:
                    response = await client.get(url)
                    response.raise_for_status()
                    parser = {"ecdc_cdtr": parse_ecdc_cdtr, "africa_cdc_ebs": parse_africa_cdc_ebs, "paho_alerts": parse_paho_alerts}[source]
                    candidates = parser(response.text, url)
                health[source] = {"status": "fresh", "checked_at": checked_at, "url": url, "item_count": len(candidates)}
                results.extend(normalize_event(item, catalogue) for item in candidates)
            except Exception as exc:  # One source never blocks numerical surveillance.
                health[source] = {"status": "failed", "checked_at": checked_at, "url": url, "error": str(exc)[:240], "stale_after_hours": int(config.get("event_stale_hours", 72))}
    return results, health


async def persist_events(events: list[dict[str, Any]]) -> None:
    async with get_db() as db:
        for item in events:
            row = (
                await db.execute(
                    select(PublicHealthEvent).where(
                        PublicHealthEvent.source == item["source"],
                        PublicHealthEvent.external_id == item["external_id"],
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = PublicHealthEvent(
                    source=item["source"],
                    external_id=item["external_id"],
                    source_url=item["source_url"],
                    title=item["title"],
                    content_hash=item["content_hash"],
                )
                db.add(row)
            row.source_url = item["source_url"]
            row.title = item["title"]
            row.published_at = item.get("published_at")
            row.updated_at_source = item.get("updated_at_source")
            row.disease_id = item.get("disease_id")
            row.disease_name = item.get("disease_name")
            row.geographies = item.get("geographies") or []
            row.agency_risk = item.get("agency_risk")
            row.status = item.get("status") or "candidate"
            row.confidence = item.get("confidence") or "low"
            row.event_key = item.get("event_key")
            row.content_hash = item["content_hash"]
            row.metadata_ = {"source_url": item["source_url"], "checked_at": utc_now().isoformat()}


async def published_events(
    max_age_days: int = 45,
    *,
    source_health: dict[str, dict[str, Any]] | None = None,
    stale_hours: int = 72,
) -> list[dict[str, Any]]:
    cutoff_date = (utc_now() - timedelta(days=max_age_days)).date().isoformat()
    async with get_db() as db:
        rows = (
            await db.execute(
                select(PublicHealthEvent)
                .where(PublicHealthEvent.status == "published", PublicHealthEvent.published_at >= cutoff_date)
                .order_by(PublicHealthEvent.published_at.desc())
                .limit(50)
            )
        ).scalars().all()
    grouped_records: list[tuple[tuple[str | None, tuple[str, ...]], date, list[PublicHealthEvent]]] = []
    for row in rows:
        health = (source_health or {}).get(row.source) or {}
        if health.get("status") == "failed":
            last_success = (row.metadata_ or {}).get("checked_at")
            try:
                age = utc_now() - pd.Timestamp(last_success).to_pydatetime()
            except (TypeError, ValueError):
                continue
            if age > timedelta(hours=stale_hours):
                continue
        identity = (
            row.disease_id,
            tuple(sorted(place.get("code") for place in (row.geographies or []) if place.get("code"))),
        )
        try:
            published_day = date.fromisoformat(str(row.published_at))
        except ValueError:
            continue
        match = next(
            (
                group
                for group in grouped_records
                if group[0] == identity and abs((group[1] - published_day).days) <= 45
            ),
            None,
        )
        if match:
            match[2].append(row)
        else:
            grouped_records.append((identity, published_day, [row]))
    events: list[dict[str, Any]] = []
    risk_scores = {"low": 20.0, "moderate": 50.0, "high": 80.0, "very high": 100.0, "very_high": 100.0}
    for _, _, matches in grouped_records:
        primary = matches[0]
        events.append(
            {
                "id": f"event:{primary.id}",
                "kind": "official_event",
                "source": primary.source,
                "title": primary.title,
                "source_url": primary.source_url,
                "published_at": primary.published_at,
                "disease_id": primary.disease_id,
                "disease_name": primary.disease_name,
                "geographies": primary.geographies or [],
                "agency_risk": primary.agency_risk,
                "official_concern_score": risk_scores.get(str(primary.agency_risk or "").lower()),
                "confidence": primary.confidence,
                "source_status": "stale" if ((source_health or {}).get(primary.source) or {}).get("status") == "failed" else "fresh",
                "evidence_links": [{"source": row.source, "url": row.source_url, "title": row.title} for row in matches],
            }
        )
    return events


def parse_cdc_respiratory(positivity_rows: list[dict[str, Any]], admission_rows: list[dict[str, Any]], activity_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_positivity: dict[str, dict[str, Any]] = {}
    for row in sorted(positivity_rows, key=lambda item: str(item.get("week_end") or ""), reverse=True):
        pathogen = str(row.get("pathogen") or "")
        if pathogen in CDC_RESPIRATORY_DISEASES and pathogen not in latest_positivity:
            latest_positivity[pathogen] = row
    latest_admission_date = max((_iso_date(row.get("weekendingdate")) for row in admission_rows), default=None)
    latest_admissions = [row for row in admission_rows if _iso_date(row.get("weekendingdate")) == latest_admission_date]
    latest_activity_date = max((_iso_date(row.get("week_end")) for row in activity_rows), default=None)
    latest_activity = [row for row in activity_rows if _iso_date(row.get("week_end")) == latest_activity_date]
    label_order = {"Data Unavailable": -1, "Minimal": 0, "Very Low": 1, "Low": 2, "Moderate": 3, "High": 4, "Very High": 5}
    highest_activity = max((str(row.get("label") or "Data Unavailable") for row in latest_activity), key=lambda label: label_order.get(label, -1), default=None)
    cards: list[dict[str, Any]] = []
    for pathogen, info in CDC_RESPIRATORY_DISEASES.items():
        positivity = latest_positivity.get(pathogen)
        metrics: list[dict[str, Any]] = []
        dates: list[str] = []
        if positivity:
            through = _iso_date(positivity.get("week_end"))
            if through:
                dates.append(through)
            metrics.append({"metric_type": "test_positivity", "label": "US test positivity", "value": float(positivity.get("percent_test_positivity", 0)), "unit": "percent", "data_through": through, "source_url": CDC_DATASETS["positivity"], "analysis_role": "source_native_numeric_series"})
        admission_values = []
        for row in latest_admissions:
            try:
                admission_values.append(float(row.get(info["admission"], 0) or 0))
            except (TypeError, ValueError):
                continue
        if admission_values and latest_admission_date:
            dates.append(latest_admission_date)
            metrics.append({"metric_type": "hospitalized_case_notifications", "label": "US confirmed hospital admissions", "value": round(sum(admission_values), 1), "unit": "count", "data_through": latest_admission_date, "jurisdiction_count": len(admission_values), "source_url": CDC_DATASETS["hospital_admissions"], "analysis_role": "severity_series_pending_history_gate"})
        if highest_activity and latest_activity_date:
            dates.append(latest_activity_date)
            metrics.append({"metric_type": "acute_respiratory_illness_activity", "label": "Highest US jurisdiction ARI level (all respiratory illness)", "value": highest_activity, "unit": "CDC activity level", "data_through": latest_activity_date, "jurisdiction_count": len(latest_activity), "source_url": CDC_DATASETS["ari_activity"], "analysis_role": "context_only_not_pathogen_specific"})
        cards.append(
            {
                "id": f"cdc-respiratory:{info['slug']}",
                "kind": "respiratory_status",
                "disease_id": info["disease_id"],
                "disease_name": info["disease_name"],
                "disease_slug": info["slug"],
                "country_code": "US",
                "country_name": "United States",
                "source_system": "CDC Respiratory Viruses",
                "source_label": "CDC Respiratory Data Channel",
                "data_through": max(dates) if dates else None,
                "cadence": "weekly",
                "window": {"label": "Latest reported week"},
                "metrics": metrics,
                "quality": {"status": "provisional"},
                "risk": {"score": None, "level": "not_assessed", "confidence": "low", "missing_dimensions": ["composite_risk_not_calculated_across_metrics"]},
                "evidence_links": [{"title": "CDC respiratory virus data", "url": CDC_DATASETS["positivity"]}],
            }
        )
    return cards


def normalize_cdc_respiratory_series(
    positivity_rows: list[dict[str, Any]],
    admission_rows: list[dict[str, Any]],
) -> pd.DataFrame:
    """Normalize CDC national weekly history into exact source-native series.

    Test positivity remains a percentage and is never combined with admissions.
    Admission rows are restricted to CDC's own ``USA`` aggregate so state rows
    cannot be accidentally added into a second national total.
    """
    records: list[dict[str, Any]] = []
    for row in positivity_rows:
        pathogen = str(row.get("pathogen") or "")
        info = CDC_RESPIRATORY_DISEASES.get(pathogen)
        when = _iso_date(row.get("week_end"))
        try:
            value = float(row.get("percent_test_positivity"))
        except (TypeError, ValueError):
            continue
        if not info or not when:
            continue
        records.append(
            {
                "time": pd.Timestamp(when, tz="UTC"),
                "value": value,
                "quality_status": "provisional",
                "geography_key": "national",
                "dimension_key": "all",
                "dimensions": {},
                "series_code": f"SIT_CDC_NREVSS_POS_{info['disease_id']}",
                "disease_id": info["disease_id"],
                "disease_name": info["disease_name"],
                "disease_slug": info["slug"],
                "country_code": "US",
                "country_name": "United States",
                "source_system": "SRC_US_CDC_RESP_NREVSS",
                "source_label": "CDC NREVSS national test positivity",
                "metric_type": "test_positivity",
                "reporting_basis": "laboratory_surveillance",
                "temporal_granularity": "weekly",
                "unit": "percent",
                "aggregation_policy": "non_additive",
                "missing_value_policy": "missing_is_unknown",
                "source_url": CDC_DATASETS["positivity"],
                "series_metadata": {
                    "source_url": CDC_DATASETS["positivity"],
                    "dataset_id": "seuz-s2cv",
                    "pathogen": pathogen,
                    "provisional": True,
                },
            }
        )
    for row in admission_rows:
        if str(row.get("jurisdiction") or "").upper() != "USA":
            continue
        when = _iso_date(row.get("weekendingdate"))
        if not when:
            continue
        for pathogen, info in CDC_RESPIRATORY_DISEASES.items():
            try:
                value = float(row.get(info["admission"]))
            except (TypeError, ValueError):
                continue
            records.append(
                {
                    "time": pd.Timestamp(when, tz="UTC"),
                    "value": value,
                    "quality_status": "provisional",
                    "geography_key": "national",
                    "dimension_key": "all",
                    "dimensions": {},
                    "series_code": f"SIT_CDC_NHSN_ADM_{info['disease_id']}",
                    "disease_id": info["disease_id"],
                    "disease_name": info["disease_name"],
                    "disease_slug": info["slug"],
                    "country_code": "US",
                    "country_name": "United States",
                    "source_system": "SRC_US_CDC_RESP_NHSN",
                    "source_label": "CDC NHSN national confirmed respiratory admissions",
                    "metric_type": "hospitalized_case_notifications",
                    "reporting_basis": "hospital_reporting",
                    "temporal_granularity": "weekly",
                    "unit": "count",
                    "aggregation_policy": "reported_aggregate",
                    "missing_value_policy": "missing_is_unknown",
                    "source_url": CDC_DATASETS["hospital_admissions"],
                    "series_metadata": {
                        "source_url": CDC_DATASETS["hospital_admissions"],
                        "dataset_id": "vdzy-6i9v",
                        "pathogen": pathogen,
                        "jurisdiction": "USA",
                        "provisional": True,
                    },
                }
            )
    return pd.DataFrame(records)


async def persist_cdc_respiratory_series(frame: pd.DataFrame) -> dict[str, int]:
    """Upsert CDC Registry definitions and their complete available history."""
    if frame.empty:
        return {"series_upserted": 0, "observations_upserted": 0}
    now = utc_now().replace(tzinfo=None)
    definitions: list[dict[str, Any]] = []
    for _, first in frame.drop_duplicates("series_code").iterrows():
        definitions.append(
            {
                "series_code": first["series_code"],
                "disease_id": first["disease_id"],
                "target_group_code": None,
                "country_code": first["country_code"],
                "scope_code": None,
                "source_system": first["source_system"],
                "source_series_code": first["series_code"],
                "source_label": first["source_label"],
                "definition_version": "situation-v1",
                "case_definition": None,
                "case_definition_uri": first["source_url"],
                "metric_type": first["metric_type"],
                "reporting_basis": first["reporting_basis"],
                "temporal_granularity": first["temporal_granularity"],
                "unit": first["unit"],
                "mapping_relation": "exact",
                "comparability": "conditional",
                "aggregation_policy": first["aggregation_policy"],
                "availability_status": "active",
                "missing_value_policy": first["missing_value_policy"],
                "is_active": True,
                "metadata": first["series_metadata"],
                "created_at": now,
                "updated_at": now,
            }
        )
    observations = [
        {
            "time": pd.Timestamp(row.time).to_pydatetime(),
            "series_code": row.series_code,
            "geography_key": row.geography_key,
            "dimension_key": row.dimension_key,
            "dimensions": row.dimensions,
            "value": float(row.value),
            "unit": row.unit,
            "suppressed": False,
            "suppression_reason": None,
            "quality_status": row.quality_status,
            "raw_data": {
                "dataset_id": row.series_metadata.get("dataset_id"),
                "data_through": pd.Timestamp(row.time).date().isoformat(),
            },
            "metadata": {
                "source_url": row.source_url,
                "situation_room_adapter": True,
                "allow_equal_quality_overwrite": True,
            },
            "created_at": now,
            "updated_at": now,
        }
        for row in frame.itertuples(index=False)
    ]
    async with get_db() as db:
        await acquire_disease_data_mutation_lock(db)
        series_statement = pg_insert(DiseaseSurveillanceSeries.__table__).values(definitions)
        series_statement = series_statement.on_conflict_do_update(
            index_elements=["series_code"],
            set_={
                "source_label": series_statement.excluded.source_label,
                "case_definition_uri": series_statement.excluded.case_definition_uri,
                "metric_type": series_statement.excluded.metric_type,
                "reporting_basis": series_statement.excluded.reporting_basis,
                "temporal_granularity": series_statement.excluded.temporal_granularity,
                "unit": series_statement.excluded.unit,
                "aggregation_policy": series_statement.excluded.aggregation_policy,
                "availability_status": series_statement.excluded.availability_status,
                "is_active": series_statement.excluded.is_active,
                "metadata": series_statement.excluded["metadata"],
                "updated_at": now,
            },
        )
        await db.execute(series_statement)
        for offset in range(0, len(observations), 500):
            statement = pg_insert(DiseaseSeriesObservation.__table__).values(
                observations[offset : offset + 500]
            )
            statement = statement.on_conflict_do_update(
                constraint="uq_disease_series_observation_identity",
                set_={
                    "value": statement.excluded.value,
                    "unit": statement.excluded.unit,
                    "quality_status": statement.excluded.quality_status,
                    "raw_data": statement.excluded.raw_data,
                    "metadata": statement.excluded["metadata"],
                    "updated_at": now,
                },
            )
            await db.execute(statement)
    return {
        "series_upserted": len(definitions),
        "observations_upserted": len(observations),
    }


async def fetch_cdc_respiratory_history() -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Fetch full usable CDC history plus the latest contextual cards."""
    checked_at = utc_now().isoformat()
    headers = {"User-Agent": "GIDS Situation Room/2.0"}
    requests = {
        "cdc_positivity": (
            CDC_DATASETS["positivity"],
            {"$limit": 5000, "$order": "week_end DESC"},
        ),
        "cdc_hospital_admissions": (
            CDC_DATASETS["hospital_admissions"],
            {"$limit": 5000, "$where": "jurisdiction='USA'", "$order": "weekendingdate DESC"},
        ),
        "cdc_ari_activity": (
            CDC_DATASETS["ari_activity"],
            {"$limit": 5000, "$order": "week_end DESC"},
        ),
    }
    health: dict[str, dict[str, Any]] = {}
    payloads: dict[str, list[dict[str, Any]]] = {}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        responses = await asyncio.gather(
            *(client.get(url, params=params) for url, params in requests.values()),
            return_exceptions=True,
        )
    for (source, (url, _)), response in zip(requests.items(), responses, strict=True):
        if isinstance(response, Exception):
            health[source] = {"status": "failed", "checked_at": checked_at, "url": url, "error": str(response)[:240], "stale_after_hours": 72}
            payloads[source] = []
            continue
        try:
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("CDC Socrata response must be an array")
            payloads[source] = rows
            health[source] = {"status": "fresh", "checked_at": checked_at, "url": url, "item_count": len(rows)}
        except Exception as exc:
            payloads[source] = []
            health[source] = {"status": "failed", "checked_at": checked_at, "url": url, "error": str(exc)[:240], "stale_after_hours": 72}
    positivity_rows = payloads.get("cdc_positivity") or []
    admission_rows = payloads.get("cdc_hospital_admissions") or []
    activity_rows = payloads.get("cdc_ari_activity") or []
    frame = normalize_cdc_respiratory_series(positivity_rows, admission_rows)
    cards = parse_cdc_respiratory(positivity_rows, admission_rows, activity_rows)
    for source, details in health.items():
        if details.get("status") != "fresh":
            continue
        if source == "cdc_positivity":
            subset = frame[frame["metric_type"] == "test_positivity"] if not frame.empty else frame
        elif source == "cdc_hospital_admissions":
            subset = frame[frame["metric_type"] == "hospitalized_case_notifications"] if not frame.empty else frame
        else:
            subset = pd.DataFrame()
        details["normalized_observation_count"] = int(len(subset))
        details["normalized_series_count"] = int(subset["series_code"].nunique()) if not subset.empty else 0
        details["data_through"] = (
            pd.Timestamp(subset["time"].max()).date().isoformat() if not subset.empty else None
        )
    return frame, cards, health


async def fetch_cdc_respiratory() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _, cards, health = await fetch_cdc_respiratory_history()
    statuses = [item.get("status") for item in health.values()]
    return cards, {
        "status": "fresh" if statuses and all(status == "fresh" for status in statuses) else "failed",
        "checked_at": max((str(item.get("checked_at") or "") for item in health.values()), default=utc_now().isoformat()),
        "url": "https://www.cdc.gov/respiratory-viruses/data/index.html",
        "item_count": len(cards),
        "datasets": health,
    }
