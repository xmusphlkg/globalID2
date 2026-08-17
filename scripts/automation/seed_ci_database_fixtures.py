#!/usr/bin/env python3
"""Seed minimal deterministic database rows for clean-checkout CI tests."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from sqlalchemy.dialects.postgresql import insert as pg_insert


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import get_db, init_database  # noqa: E402
from src.domain import (  # noqa: E402
    Country,
    DiseaseSeriesObservation,
    DiseaseSurveillanceSeries,
    SituationSnapshot,
    StandardDisease,
)


DEFAULT_SITUATION_FIXTURE = ROOT / "tests" / "fixtures" / "situation" / "public_report_v3.json"
CI_DISEASE_ID = "D038"
CI_SERIES_CODE = "CI_US_D038_WEEKLY_CASES"
CI_SOURCE_SYSTEM = "SRC_FIXTURE"
CI_AS_OF = "2026-08-17T02:00:00Z"
CI_DATA_THROUGH = "2026-08-09"


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _legacy_history_signal() -> dict[str, Any]:
    return {
        "id": "ci-situation-signal-1",
        "kind": "statistical_signal",
        "disease_id": CI_DISEASE_ID,
        "disease_name": "Influenza",
        "disease_slug": "influenza",
        "country_code": "US",
        "country_name": "United States",
        "geography_key": "country:US:national",
        "geographies": [{"code": "US", "name": "United States"}],
        "series_code": CI_SERIES_CODE,
        "source_system": CI_SOURCE_SYSTEM,
        "source_label": "CI fixture surveillance",
        "source_url": "https://example.invalid/source",
        "metric_type": "case_notifications",
        "metric_label": "cases",
        "unit": "count",
        "cadence": "weekly",
        "data_through": CI_DATA_THROUGH,
        "evidence_links": [
            {
                "title": "CI fixture source",
                "url": "https://example.invalid/source",
                "source": "fixture",
            }
        ],
        "window": {
            "label": "Last 4 weeks",
            "periods": 4,
            "aggregation": "sum",
            "current": 240,
            "previous": 100,
            "change_pct": 140,
        },
        "statistics": {
            "z_score": 3.2,
            "robust_z": 3.7,
            "ewma_residual": 1.4,
            "bayesian_change_probability": 0.91,
            "detector_votes": 3,
            "detectors": {
                "seasonal_band": True,
                "z_score": True,
                "ewma": False,
                "bayesian_change": True,
            },
        },
        "risk": {
            "score": 52.0,
            "level": "high",
            "confidence": "low",
            "dimensions": {
                "trend": 52.0,
                "severity": None,
                "geographic_spread": None,
                "official_concern": None,
            },
            "missing_dimensions": [
                "severity",
                "geographic_spread",
                "official_concern",
            ],
        },
    }


def situation_snapshot_values(fixture: dict[str, Any]) -> dict[str, Any]:
    report = fixture.get("report") or {}
    method = fixture.get("method") or {}
    quality_gate = fixture.get("quality_gate") or {}
    data_currency = fixture.get("data_currency") or {}
    signal = _legacy_history_signal()
    payload = dict(fixture)
    payload.update(
        {
            "increasing": [signal],
            "respiratory": [],
            "emerging": [],
            "unusual": [],
            "freshness": {
                CI_SOURCE_SYSTEM: {
                    "status": "fresh",
                    "checked_at": CI_AS_OF,
                    "item_count": 1,
                }
            },
        }
    )
    snapshot_id = str(report.get("report_id") or "ci-situation-v3-daily-r1")
    return {
        "snapshot_id": snapshot_id,
        "snapshot_kind": str(report.get("kind") or "daily"),
        "period_key": str(report.get("period_key") or "2026-08-17"),
        "iso_week": None,
        "generated_at": str(report.get("as_of") or CI_AS_OF),
        "checked_at": str(report.get("as_of") or CI_AS_OF),
        "content_updated_at": str(report.get("as_of") or CI_AS_OF),
        "data_through": str(data_currency.get("latest_data_through") or CI_DATA_THROUGH),
        "method_version": str(method.get("version") or "ci_fixture"),
        "input_hash": str(method.get("config_hash") or "ci-fixture"),
        "status": str(report.get("status") or "published"),
        "quality_gate_status": str(quality_gate.get("status") or "passed"),
        "quality_gate": quality_gate,
        "revision": int(report.get("revision") or 1),
        "supersedes_snapshot_id": report.get("supersedes_report_id"),
        "payload": payload,
    }


def series_observation_values() -> list[dict[str, Any]]:
    latest = _utc(CI_DATA_THROUGH + "T00:00:00Z")
    start = latest - timedelta(weeks=207)
    rows: list[dict[str, Any]] = []
    for index in range(208):
        observed_at = start + timedelta(weeks=index)
        if index >= 204:
            value = 60
        elif index >= 200:
            value = 25
        else:
            value = 24 + (index % 5)
        rows.append(
            {
                "time": observed_at,
                "series_code": CI_SERIES_CODE,
                "geography_key": "country:US:national",
                "dimension_key": "all",
                "dimensions": {},
                "value": float(value),
                "unit": "count",
                "suppressed": False,
                "suppression_reason": None,
                "quality_status": "validated",
                "raw_data": {"source": "ci_fixture", "index": index},
                "metadata": {"fixture": "ci_database"},
            }
        )
    return rows


async def seed_database(fixture_path: Path = DEFAULT_SITUATION_FIXTURE) -> dict[str, Any]:
    await init_database()
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(fixture, dict):
        raise ValueError("situation_fixture_object_required")

    async with get_db() as db:
        await db.execute(
            pg_insert(Country.__table__)
            .values(
                code="US",
                name="United States",
                name_en="United States",
                language="en",
                timezone="UTC",
                crawler_config={},
                parser_config={},
                disease_mapping_rules={},
                report_config={},
                metadata={},
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )
        await db.execute(
            pg_insert(StandardDisease.__table__)
            .values(
                disease_id=CI_DISEASE_ID,
                standard_name_en="Influenza",
                standard_name_zh="Influenza",
                category="Viral",
                icd_10="J09-J11",
                icd_11="1E30",
                description="Seasonal influenza fixture concept for CI.",
                source="CI fixture",
                metadata={"fixture": "ci_database"},
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=["disease_id"])
        )
        await db.execute(
            pg_insert(DiseaseSurveillanceSeries.__table__)
            .values(
                series_code=CI_SERIES_CODE,
                disease_id=CI_DISEASE_ID,
                target_group_code=None,
                country_code="US",
                scope_code=None,
                source_system=CI_SOURCE_SYSTEM,
                source_series_code="FIXTURE_WEEKLY_CASES",
                source_label="CI fixture weekly influenza cases",
                definition_version="1",
                case_definition="Synthetic CI fixture case notifications.",
                case_definition_uri=None,
                metric_type="case_notifications",
                reporting_basis="notification",
                temporal_granularity="weekly",
                unit="count",
                mapping_relation="exact",
                comparability="direct",
                aggregation_policy="non_additive",
                availability_status="active",
                missing_value_policy="missing_is_unknown",
                valid_from=_utc("2022-08-21T00:00:00Z").date(),
                valid_to=None,
                is_active=True,
                metadata={"source_url": "https://example.invalid/source"},
            )
            .on_conflict_do_update(
                index_elements=["series_code"],
                set_={
                    "disease_id": CI_DISEASE_ID,
                    "country_code": "US",
                    "is_active": True,
                    "metadata": {"source_url": "https://example.invalid/source"},
                },
            )
        )
        await db.execute(
            pg_insert(DiseaseSeriesObservation.__table__)
            .values(series_observation_values())
            .on_conflict_do_nothing(
                index_elements=[
                    "time",
                    "series_code",
                    "geography_key",
                    "dimension_key",
                ]
            )
        )
        snapshot = situation_snapshot_values(fixture)
        await db.execute(
            pg_insert(SituationSnapshot.__table__)
            .values(snapshot)
            .on_conflict_do_update(
                index_elements=["snapshot_id"],
                set_={
                    "checked_at": snapshot["checked_at"],
                    "content_updated_at": snapshot["content_updated_at"],
                    "status": snapshot["status"],
                    "quality_gate_status": snapshot["quality_gate_status"],
                    "quality_gate": snapshot["quality_gate"],
                    "payload": snapshot["payload"],
                },
            )
        )

    return {
        "status": "seeded",
        "series_code": CI_SERIES_CODE,
        "observations": len(series_observation_values()),
        "snapshot_id": situation_snapshot_values(fixture)["snapshot_id"],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--situation-fixture", type=Path, default=DEFAULT_SITUATION_FIXTURE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = asyncio.run(seed_database(args.situation_fixture))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
