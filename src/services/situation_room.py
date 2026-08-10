"""Deterministic data and event pipeline for the public Situation Room.

The module deliberately keeps numerical surveillance signals separate from
external event notices.  It has no LLM dependency and is safe to run before a
static-site export: network fetching happens only in ``refresh_situation``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from sqlalchemy import delete, select, text

from src.core.database import get_db, init_database
from src.domain import PublicHealthEvent, SituationSnapshot

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "situation_room.json"
METHOD_VERSION = "situation_room_v1.0"

EVENT_SOURCE_URLS = {
    "who_don": "https://www.who.int/emergencies/disease-outbreak-news",
    "ecdc_cdtr": "https://www.ecdc.europa.eu/en/publications-and-data/monitoring/weekly-threats-reports",
    "africa_cdc_ebs": "https://africacdc.org/document-tag/ebs-weekly-report/",
    "paho_alerts": "https://www.paho.org/en/epidemiological-alerts-and-updates",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config.setdefault("method_version", METHOD_VERSION)
    config.setdefault("thresholds", {})
    return config


def _number(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100.0, 2)


def _cadence_from_granularity(value: Any, periods: pd.Series) -> str:
    raw = str(value or "").lower()
    if raw in {"daily", "weekly", "monthly", "annual"}:
        return raw
    ordered = pd.to_datetime(periods, errors="coerce").dropna().sort_values()
    if len(ordered) < 2:
        return "unknown"
    days = ordered.diff().dropna().dt.total_seconds().median() / 86400
    if 5 <= days <= 10:
        return "weekly"
    if 25 <= days <= 35:
        return "monthly"
    if days >= 300:
        return "annual"
    return "daily"


def _seasonal_baseline(values: pd.Series, dates: pd.Series, cadence: str) -> pd.Series:
    """Return prior same-season observations within the preceding five years."""
    latest = pd.Timestamp(dates.iloc[-1])
    prior_values = values.iloc[:-1].reset_index(drop=True)
    prior_dates = pd.to_datetime(dates.iloc[:-1], errors="coerce").reset_index(drop=True)
    start = latest - pd.DateOffset(years=5)
    selected = []
    for value, period in zip(prior_values, prior_dates, strict=False):
        if pd.isna(period) or period < start:
            continue
        if cadence == "monthly" and period.month == latest.month:
            selected.append(value)
        elif cadence == "weekly":
            week = int(period.isocalendar().week)
            target = int(latest.isocalendar().week)
            distance = min(abs(week - target), 53 - abs(week - target))
            if distance <= 2:
                selected.append(value)
        elif cadence == "daily" and abs((period.replace(year=latest.year) - latest).days) <= 7:
            selected.append(value)
    return pd.Series(selected, dtype=float)


def _ewma_alert(values: pd.Series, smoothing: float, limit_sigma: float) -> tuple[float | None, float | None, bool]:
    baseline = values.iloc[:-1]
    if len(baseline) < 2:
        return None, None, False
    mean = float(baseline.mean())
    std = float(baseline.std(ddof=1))
    if not std:
        return None, None, False
    ewma = mean
    for value in values:
        ewma = smoothing * float(value) + (1.0 - smoothing) * ewma
    variance = smoothing / (2.0 - smoothing) * (1 - (1 - smoothing) ** (2 * len(values)))
    upper = mean + limit_sigma * std * math.sqrt(max(variance, 0.0))
    return round(ewma, 3), round(upper, 3), bool(ewma > upper)


def _z_scores(latest: float, baseline: pd.Series) -> tuple[float | None, float | None, float | None]:
    if len(baseline) < 2:
        return None, None, None
    median = float(baseline.median())
    mad = float((baseline - median).abs().median())
    std = float(baseline.std(ddof=1))
    robust_z = 0.6745 * (latest - median) / mad if mad else None
    z_score = (latest - float(baseline.mean())) / std if std else None
    return (
        round(robust_z, 3) if robust_z is not None else None,
        round(z_score, 3) if z_score is not None else None,
        round(median, 3),
    )


def analyze_series(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any] | None:
    """Analyze one already-isolated source series and return a public signal."""
    if frame.empty:
        return None
    work = frame.copy()
    work["time"] = pd.to_datetime(work["time"], errors="coerce", utc=True)
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    work = work[work["time"].notna() & work["value"].notna()].sort_values("time")
    if work.empty:
        return None
    cadence = _cadence_from_granularity(work.get("temporal_granularity", pd.Series(dtype=str)).iloc[0] if "temporal_granularity" in work else None, work["time"])
    if cadence in {"annual", "unknown"}:
        return None
    threshold = config["thresholds"]
    minimum = int(threshold["minimum_observations"].get(cadence, 10))
    if len(work) < minimum:
        return None
    values = work["value"].reset_index(drop=True)
    periods = work["time"].reset_index(drop=True)
    current_window = int(threshold["current_window_periods"])
    if len(values) < current_window * 2:
        return None
    current = float(values.iloc[-current_window:].sum())
    previous = float(values.iloc[-2 * current_window:-current_window].sum())
    absolute_change = current - previous
    change_pct = _pct_change(current, previous)
    latest = float(values.iloc[-1])
    seasonal = _seasonal_baseline(values, periods, cadence)
    robust_z, z_score, seasonal_median = _z_scores(latest, seasonal)
    ewma, ewma_upper, ewma_alert = _ewma_alert(values, float(threshold["ewma_lambda"]), float(threshold["ewma_limit_sigma"]))
    low_base = previous < float(threshold["low_base_previous_cases"])
    zero_periods = 0
    for value in reversed(values.iloc[:-1].tolist()):
        if value == 0:
            zero_periods += 1
        else:
            break
    reappearing = zero_periods >= int(threshold["reappearance_zero_periods"]) and current >= float(threshold["minimum_current_cases"])
    candidate = (
        current >= float(threshold["minimum_current_cases"])
        and absolute_change >= float(threshold["minimum_absolute_increase"])
        and (change_pct or 0) >= float(threshold["minimum_relative_increase_pct"])
        and ((robust_z or -math.inf) >= float(threshold["robust_z_elevated"]) or ewma_alert)
    )
    if low_base and not reappearing:
        candidate = False
    if not candidate and not reappearing:
        return None
    confirmations = int((robust_z or -math.inf) >= float(threshold["robust_z_strong"])) + int((z_score or -math.inf) >= float(threshold["z_strong"])) + int(ewma_alert)
    strong = confirmations >= 2 or reappearing
    level = "strong" if strong else "elevated" if confirmations >= 1 else "watch"
    quality = str(work.get("quality_status", pd.Series(["validated"])).iloc[-1] or "validated")
    confidence = "medium" if quality == "provisional" else "high"
    first = work.iloc[-1]
    return {
        "id": "signal:" + hashlib.sha256(f"{first.get('series_code')}|{first.get('geography_key')}|{first.get('disease_id')}".encode()).hexdigest()[:16],
        "kind": "statistical_signal",
        "disease_id": first.get("disease_id"),
        "disease_name": first.get("disease_name") or first.get("disease_id"),
        "disease_slug": first.get("disease_slug"),
        "country_code": first.get("country_code"),
        "country_name": first.get("country_name") or first.get("country_code"),
        "series_code": first.get("series_code"),
        "source_label": first.get("source_label"),
        "geography_key": first.get("geography_key"),
        "cadence": cadence,
        "data_through": pd.Timestamp(periods.iloc[-1]).date().isoformat(),
        "window": {"periods": current_window, "current_cases": int(current), "previous_cases": int(previous), "absolute_change": int(absolute_change), "change_pct": None if low_base else change_pct},
        "baseline": {"same_season_median": seasonal_median, "sample_size": int(len(seasonal))},
        "statistics": {"robust_z": robust_z, "z_score": z_score, "ewma": ewma, "ewma_upper_limit": ewma_upper, "ewma_alert": ewma_alert},
        "signal_level": level,
        "confidence": confidence,
        "reappearing": reappearing,
        "unusual": strong,
    }


async def fetch_series_frame() -> pd.DataFrame:
    """Read source-native observations without coalescing incompatible series."""
    query = text("""
        SELECT o.time, o.value, o.quality_status, o.geography_key, o.series_code,
               s.disease_id, s.country_code, s.source_label, s.temporal_granularity,
               d.standard_name_en AS disease_name, c.name_en AS country_name
        FROM disease_series_observations o
        JOIN disease_surveillance_series s ON s.series_code = o.series_code
        LEFT JOIN standard_diseases d ON d.disease_id = s.disease_id
        LEFT JOIN countries c ON c.code = s.country_code
        WHERE o.suppressed = false AND o.quality_status <> 'rejected'
          AND s.is_active = true AND s.metric_type = 'cases'
          AND s.mapping_relation = 'exact' AND s.aggregation_policy = 'direct_only'
          AND s.reporting_basis NOT IN ('sentinel', 'unknown')
    """)
    async with get_db() as db:
        try:
            rows = (await db.execute(query)).mappings().all()
        except Exception:
            rows = []
    frame = pd.DataFrame([dict(row) for row in rows])
    if not frame.empty:
        frame["disease_slug"] = frame["disease_name"].fillna(frame["disease_id"]).map(
            lambda value: re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
        )
    return frame


def analyze_frame(frame: pd.DataFrame, config: dict[str, Any]) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    group_columns = ["series_code", "geography_key"]
    signals = [analyze_series(group, config) for _, group in frame.groupby(group_columns, dropna=False)]
    return sorted([signal for signal in signals if signal], key=lambda row: (row["signal_level"] == "strong", row["statistics"].get("robust_z") or -99, row["window"]["absolute_change"]), reverse=True)


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event_candidates(source: str, html: str, base_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in soup.select("article a[href], h2 a[href], h3 a[href], .card a[href]"):
        title = link.get_text(" ", strip=True)
        href = urljoin(base_url, str(link.get("href") or ""))
        if len(title) < 8 or not href.startswith("http") or href in seen:
            continue
        if source == "who_don" and "/item/" not in href:
            continue
        seen.add(href)
        parent_text = link.parent.get_text(" ", strip=True) if link.parent else ""
        risk_match = re.search(r"\brisk(?:\s+(?:is|level|assessment)?\s*(?:of|:)?\s*)(low|moderate|high|very high)\b", parent_text, re.IGNORECASE)
        records.append({"source": source, "external_id": href.rsplit("/", 1)[-1], "source_url": href, "title": title[:1000], "published_at": _extract_date(parent_text), "agency_risk": risk_match.group(1).lower() if risk_match else None})
    return records[:40]


def _extract_date(text_value: str) -> str | None:
    match = re.search(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2})\b", text_value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def _event_mapping(title: str, disease_catalogue: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    title_lower = title.lower()
    disease = None
    for row in disease_catalogue:
        names = [row.get("name_en"), row.get("name_zh"), *(row.get("aliases") or [])]
        if any(isinstance(name, str) and len(name) > 3 and name.lower() in title_lower for name in names):
            disease = row
            break
    geographies = []
    # Event pages can be auto-published only when a configured ISO country name
    # occurs literally in the official title; ambiguous prose remains a candidate.
    for code, name in _country_names().items():
        if name.lower() in title_lower:
            geographies.append({"code": code, "name": name})
    return disease, geographies


def _country_names() -> dict[str, str]:
    # The source system already uses these countries.  The small explicit map
    # avoids guessing from third-party geocoders in a public-health workflow.
    return {"US": "United States", "JP": "Japan", "CA": "Canada", "GB": "United Kingdom", "FR": "France", "DE": "Germany", "ES": "Spain", "IT": "Italy", "UG": "Uganda", "CD": "Democratic Republic of the Congo", "BR": "Brazil", "MX": "Mexico", "AR": "Argentina", "AU": "Australia", "NZ": "New Zealand"}


async def fetch_external_events(config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Fetch current index pages and return compact event candidates and freshness."""
    catalogue = _load_disease_catalogue()
    results: list[dict[str, Any]] = []
    freshness: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "GIDS Situation Room/1.0"}) as client:
        for source in config.get("event_sources", EVENT_SOURCE_URLS):
            url = EVENT_SOURCE_URLS.get(source)
            if not url:
                continue
            try:
                response = await client.get(url)
                response.raise_for_status()
                candidates = _event_candidates(source, response.text, url)
                freshness[source] = {"status": "fresh", "checked_at": utc_now().isoformat(), "url": url, "item_count": len(candidates)}
            except Exception as exc:  # Source failure must not block statistical signals.
                freshness[source] = {"status": "stale", "checked_at": utc_now().isoformat(), "url": url, "error": str(exc)[:240]}
                continue
            for item in candidates:
                disease, geographies = _event_mapping(item["title"], catalogue)
                confidence = "high" if disease and geographies else "low"
                item.update({
                    "disease_id": disease.get("disease_id") if disease else None,
                    "disease_name": disease.get("name_en") if disease else None,
                    "geographies": geographies,
                    "confidence": confidence,
                    "status": "published" if confidence == "high" else "candidate",
                    "content_hash": _text_hash(item["title"] + item["source_url"]),
                    "event_key": _event_key(disease, geographies, item.get("published_at")),
                })
                results.append(item)
    return results, freshness


def _event_key(disease: dict[str, Any] | None, geographies: list[dict[str, str]], published_at: str | None) -> str | None:
    if not disease or not geographies:
        return None
    day = date.fromisoformat(published_at) if published_at else utc_now().date()
    bucket = day - timedelta(days=day.toordinal() % 45)
    return f"{disease.get('disease_id')}|{','.join(sorted(g['code'] for g in geographies))}|{bucket.isoformat()}"


def _load_disease_catalogue() -> list[dict[str, Any]]:
    path = ROOT / "configs" / "standard_diseases.csv"
    if not path.exists():
        return []
    return pd.read_csv(path).fillna("").to_dict("records")


async def persist_events(events: list[dict[str, Any]]) -> None:
    async with get_db() as db:
        for item in events:
            row = (await db.execute(select(PublicHealthEvent).where(PublicHealthEvent.source == item["source"], PublicHealthEvent.external_id == item["external_id"]))).scalar_one_or_none()
            if row is None:
                row = PublicHealthEvent(source=item["source"], external_id=item["external_id"], source_url=item["source_url"], title=item["title"], content_hash=item["content_hash"])
                db.add(row)
            row.published_at = item.get("published_at")
            row.disease_id = item.get("disease_id")
            row.disease_name = item.get("disease_name")
            row.geographies = item.get("geographies") or []
            row.agency_risk = item.get("agency_risk")
            row.status = item.get("status") or "candidate"
            row.confidence = item.get("confidence") or "low"
            row.event_key = item.get("event_key")
            row.metadata_ = {"source_url": item["source_url"]}


async def published_events() -> list[dict[str, Any]]:
    cutoff = utc_now() - timedelta(days=45)
    async with get_db() as db:
        rows = (await db.execute(select(PublicHealthEvent).where(PublicHealthEvent.status == "published", PublicHealthEvent.created_at >= cutoff).order_by(PublicHealthEvent.published_at.desc()).limit(30))).scalars().all()
    grouped: dict[str, list[PublicHealthEvent]] = {}
    for row in rows:
        grouped.setdefault(row.event_key or f"source:{row.id}", []).append(row)
    events = []
    for matches in grouped.values():
        primary = matches[0]
        events.append({
            "id": f"event:{primary.id}", "kind": "official_event", "source": primary.source,
            "title": primary.title, "source_url": primary.source_url,
            "published_at": primary.published_at, "disease_id": primary.disease_id,
            "disease_name": primary.disease_name, "geographies": primary.geographies or [],
            "agency_risk": primary.agency_risk, "confidence": primary.confidence,
            "evidence_links": [{"source": row.source, "url": row.source_url, "title": row.title} for row in matches],
        })
    return events


def build_snapshot(signals: list[dict[str, Any]], events: list[dict[str, Any]], freshness: dict[str, Any], config: dict[str, Any], generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or utc_now()
    respiratory_slugs = set(config.get("respiratory_slugs") or [])
    respiratory = [row for row in signals if str(row.get("disease_slug") or "") in respiratory_slugs]
    unusual = [row for row in signals if row.get("unusual")]
    data_dates = [row["data_through"] for row in signals if row.get("data_through")]
    week = generated_at.isocalendar()
    limits = config.get("signal_limits") or {}
    payload = {
        "schema_version": "situation_room.v1",
        "method_version": config.get("method_version", METHOD_VERSION),
        "snapshot_id": "situation-" + generated_at.strftime("%Y%m%dT%H%M%SZ"),
        "public_enabled": bool(config.get("public_enabled", False)),
        "generated_at": generated_at.isoformat(),
        "data_through": max(data_dates) if data_dates else None,
        "iso_week": f"{week.year}-W{week.week:02d}",
        "coverage": {"signal_count": len(signals), "country_count": len({s.get('country_code') for s in signals if s.get('country_code')}), "note_en": "Statistical signals cover GIDS-supported jurisdictions only.", "note_zh": "统计信号仅覆盖 GIDS 当前支持的国家和地区。"},
        "freshness": freshness,
        "increasing": signals[: int(limits.get("increasing", 12))],
        "respiratory": respiratory[: int(limits.get("respiratory", 9))],
        "emerging": events[: int(limits.get("emerging", 9))],
        "unusual": unusual[: int(limits.get("unusual", 9))],
        "event_sources": [{"id": key, "url": value} for key, value in EVENT_SOURCE_URLS.items()],
        "methodology": {"en": "Signals compare each source-native case series with its own seasonal baseline and EWMA control limit; they are not outbreak declarations.", "zh": "信号将每条来源原生病例序列与其自身季节基线和 EWMA 控制限比较；并非暴发宣布。"},
        "limitations": {"en": "WHO Disease Outbreak News and regional event feeds are authoritative but not exhaustive. Data cadence, reporting lag, and case definitions differ by source.", "zh": "WHO 疾病暴发新闻及区域事件源具有权威性但并非穷尽；各来源的报告频率、延迟和病例定义不同。"},
    }
    payload["input_hash"] = _text_hash(json.dumps({"signals": signals, "events": events}, sort_keys=True, default=str))
    return payload


async def persist_snapshot(payload: dict[str, Any], *, archive_week: bool = False, daily_retention_days: int = 90) -> SituationSnapshot:
    async with get_db() as db:
        if archive_week:
            existing = (await db.execute(select(SituationSnapshot).where(SituationSnapshot.snapshot_kind == "weekly", SituationSnapshot.iso_week == payload["iso_week"], SituationSnapshot.status == "published").order_by(SituationSnapshot.revision.desc()))).scalars().first()
            if existing is not None:
                return existing
        snapshot = SituationSnapshot(snapshot_id=payload["snapshot_id"], snapshot_kind="weekly" if archive_week else "daily", iso_week=payload["iso_week"], generated_at=payload["generated_at"], data_through=payload.get("data_through"), method_version=payload["method_version"], input_hash=payload["input_hash"], payload=payload)
        db.add(snapshot)
        if not archive_week:
            cutoff = utc_now() - timedelta(days=daily_retention_days)
            await db.execute(delete(SituationSnapshot).where(SituationSnapshot.snapshot_kind == "daily", SituationSnapshot.created_at < cutoff))
        return snapshot


async def refresh_situation(*, fetch_events: bool = True, now: datetime | None = None) -> dict[str, Any]:
    await init_database()
    config = load_config()
    frame = await fetch_series_frame()
    signals = analyze_frame(frame, config)
    freshness: dict[str, Any] = {}
    if fetch_events:
        event_rows, freshness = await fetch_external_events(config)
        await persist_events(event_rows)
    events = await published_events()
    payload = build_snapshot(signals, events, freshness, config, now)
    await persist_snapshot(payload, daily_retention_days=int(config.get("daily_retention_days", 90)))
    generated = pd.Timestamp(payload["generated_at"])
    archive_week = generated.weekday() == 0
    if archive_week:
        previous_week = (generated - pd.Timedelta(days=7)).isocalendar()
        archive_payload = dict(payload)
        archive_payload["iso_week"] = f"{previous_week.year}-W{previous_week.week:02d}"
        archive_payload["snapshot_id"] = f"{payload['snapshot_id']}-archive-{archive_payload['iso_week']}"
        await persist_snapshot(archive_payload, archive_week=True)
    return payload


async def latest_snapshot() -> dict[str, Any] | None:
    async with get_db() as db:
        row = (await db.execute(select(SituationSnapshot).where(SituationSnapshot.status == "published", SituationSnapshot.snapshot_kind == "daily").order_by(SituationSnapshot.created_at.desc()))).scalars().first()
    return dict(row.payload) if row else None


async def weekly_snapshots() -> list[dict[str, Any]]:
    async with get_db() as db:
        rows = (await db.execute(select(SituationSnapshot).where(SituationSnapshot.snapshot_kind == "weekly", SituationSnapshot.status == "published").order_by(SituationSnapshot.iso_week.desc()))).scalars().all()
    return [dict(row.payload) for row in rows]
