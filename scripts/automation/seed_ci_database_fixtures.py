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
    SituationNotice,
    SituationNoticeEntity,
    StandardDisease,
)


CI_DISEASE_ID = "D038"
CI_SERIES_CODE = "CI_US_D038_WEEKLY_CASES"
CI_SOURCE_SYSTEM = "SRC_FIXTURE"
CI_AS_OF = "2026-08-17T02:00:00Z"
CI_DATA_THROUGH = "2026-08-09"


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


CI_NOTICE_ID = "who_don:ci-fixture-1"


def situation_notice_values() -> dict[str, Any]:
    """One deterministic authority notice for the notice-based Situation module."""

    now = _utc(CI_AS_OF)
    return {
        "notice_id": CI_NOTICE_ID,
        "source_id": "who_don",
        "native_id": "ci-fixture-1",
        "record_type": "outbreak_notice",
        "title_en": "Influenza - United States of America",
        "summary_en": None,
        "url": "https://www.who.int/emergencies/disease-outbreak-news/item/ci-fixture-1",
        "language": "en",
        "redistribution": "metadata_and_extract",
        "published_date": now.date(),
        "updated_at_source": now.date().isoformat(),
        "iso_week_key": f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}",
        "content_hash": "ci-fixture",
        "first_seen_at": now,
        "last_seen_at": now,
        "ai_status": "pending",
        "ai_attempts": 0,
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


async def seed_database() -> dict[str, Any]:
    await init_database()

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
        await db.execute(
            pg_insert(SituationNotice.__table__)
            .values(situation_notice_values())
            .on_conflict_do_nothing(index_elements=["notice_id"])
        )
        for entity_type, entity_id, name in (("disease", CI_DISEASE_ID, "Influenza"), ("country", "US", "United States")):
            await db.execute(
                pg_insert(SituationNoticeEntity.__table__)
                .values(
                    notice_id=CI_NOTICE_ID,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    name_en=name,
                    method="rule",
                    active=True,
                )
                .on_conflict_do_nothing(constraint="uq_situation_notice_entity")
            )

    return {
        "status": "seeded",
        "series_code": CI_SERIES_CODE,
        "observations": len(series_observation_values()),
        "notice_id": CI_NOTICE_ID,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = asyncio.run(seed_database())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
