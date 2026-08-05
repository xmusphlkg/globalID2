from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from scripts import backfill_us_nndss_resident_series as backfill


def _write_source(path: Path, rows: list[str]) -> None:
    path.write_text(
        "Reporting Area,Current MMWR Year,MMWR WEEK,Label,Current week,"
        '"Current week, flag"\n' + "".join(rows),
        encoding="utf-8",
    )


def _observation(*, value: float = 2.0, area: str = "U.S. Residents") -> dict:
    return {
        "time": datetime(2025, 1, 4, tzinfo=timezone.utc),
        "series_code": "SER_US_HEPATITIS_A_CONFIRMED",
        "geography_key": backfill.NATIONAL_GEOGRAPHY,
        "dimension_key": "all",
        "dimensions": {},
        "value": value,
        "unit": "count",
        "suppressed": False,
        "suppression_reason": None,
        "quality_status": "provisional",
        "raw_data": {
            "ReportingArea": area,
            "PopulationScope": backfill.POPULATION_SCOPE,
        },
        "metadata": {
            "source_id": backfill.SOURCE_ID,
            "population_scope": backfill.POPULATION_SCOPE,
        },
    }


def test_plan_routes_all_registry_series_and_omits_missing_and_total(
    tmp_path: Path,
) -> None:
    source = tmp_path / "nndss.csv"
    _write_source(
        source,
        [
            'US RESIDENTS,2025,1,"Hepatitis A, Confirmed",3,-\n',
            "U.S. Residents,2025,2,Salmonella Paratyphi infection,2,-\n",
            'U.S. Residents,2025,3,"Carbapenemase-Producing Organisms '
            '(CPO), Total",0,-\n',
            'U.S. Residents,2025,4,"Hepatitis A, Confirmed",,-\n',
            'TOTAL,2025,1,"Hepatitis A, Confirmed",99,-\n',
            'California,2025,1,"Hepatitis A, Confirmed",88,-\n',
        ],
    )

    result = backfill.build_source_plan([source])

    assert result.summary["status"] == "ready"
    assert result.summary["observations"] == 3
    assert result.summary["counts"]["missing_values"] == 1
    assert result.summary["counts"]["zero"] == 1
    assert result.summary["resident_reporting_areas"] == {
        "U.S. Residents": 3,
        "US RESIDENTS": 1,
    }
    assert {row["series_code"] for row in result.observations} >= {
        "SER_US_HEPATITIS_A_CONFIRMED",
        "SER_US_PARATYPHI_WEEKLY",
        "SER_US_CPO_WEEKLY",
    }
    assert all(
        row["geography_key"] == backfill.NATIONAL_GEOGRAPHY
        for row in result.observations
    )
    assert all(
        row["metadata"]["population_scope"] == backfill.POPULATION_SCOPE
        for row in result.observations
    )
    assert {row["time"].date().isoformat() for row in result.observations} == {
        "2025-01-04",
        "2025-01-11",
        "2025-01-18",
    }


def test_same_value_overlap_deduplicates_but_different_value_blocks(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    row = 'US RESIDENTS,2025,1,"Hepatitis A, Confirmed",3,-\n'
    _write_source(first, [row])
    _write_source(
        second,
        ['U.S. Residents,2025,1,"Hepatitis A, Confirmed",3,-\n'],
    )

    deduplicated = backfill.build_source_plan([first, second])

    assert deduplicated.summary["status"] == "ready"
    assert deduplicated.summary["observations"] == 1
    assert deduplicated.summary["deduplicated_same_value"] == 1
    assert {
        deduplicated.summary["deduplicated_same_value_examples"][0][
            "first_reporting_area"
        ],
        deduplicated.summary["deduplicated_same_value_examples"][0][
            "second_reporting_area"
        ],
    } == {"US RESIDENTS", "U.S. Residents"}

    _write_source(
        second,
        ['U.S. Residents,2025,1,"Hepatitis A, Confirmed",4,-\n'],
    )
    conflicting = backfill.build_source_plan([first, second])

    assert conflicting.summary["status"] == "blocked"
    assert conflicting.summary["overlap_conflicts"] == 1
    assert any(
        error["code"] == "overlapping_extract_value_conflict"
        for error in conflicting.summary["errors"]
    )


def test_mmwr_week_53_ends_on_cdc_saturday() -> None:
    assert backfill._mmwr_week_end(2025, 53).isoformat() == "2026-01-03"
    with pytest.raises(ValueError, match="does not contain week"):
        backfill._mmwr_week_end(2024, 53)


def test_existing_exact_resident_is_noop_and_any_total_provenance_blocks() -> None:
    incoming = _observation()
    exact = backfill.classify_existing([incoming], [dict(incoming)])

    assert exact["status"] == "ready"
    assert len(exact["exact"]) == 1
    assert exact["absent"] == []

    total = _observation(area="TOTAL")
    total_result = backfill.classify_existing([incoming], [total])

    assert total_result["status"] == "blocked"
    assert total_result["nonresident"][0]["reporting_area"] == "TOTAL"
    assert total_result["conflicts"][0]["reason"] == ("existing_nonresident_provenance")


def test_existing_resident_with_different_content_blocks() -> None:
    incoming = _observation(value=2.0)
    existing = _observation(value=3.0)

    result = backfill.classify_existing([incoming], [existing])

    assert result["status"] == "blocked"
    assert result["conflicts"][0]["reason"] == "existing_content_differs"


def test_apply_requires_explicit_input_and_date_range() -> None:
    with pytest.raises(SystemExit):
        backfill.parse_args(["--apply"])
    with pytest.raises(SystemExit):
        backfill.parse_args(["--apply", "--input", "source.csv"])

    args = backfill.parse_args(
        [
            "--apply",
            "--input",
            "source.csv",
            "--from-date",
            "2025-01-04",
            "--to-date",
            "2025-12-27",
        ]
    )
    assert args.apply is True
    assert args.input == [Path("source.csv")]


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, *, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def mappings(self):
        return _Mappings(self._rows)

    def scalar(self):
        return self._scalar


class _InsertAuditDB:
    def __init__(self, inserted_row: dict):
        self.inserted_row = inserted_row
        self.calls: list[tuple[object, object]] = []

    async def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))
        call = len(self.calls)
        if call == 1:
            # Simulate a concurrent exact winner for the second candidate:
            # RETURNING contains only the fact this transaction really inserted.
            return _Result(rows=[self.inserted_row])
        if call == 2:
            return _Result(rows=[{"identity_key": "actual"}])
        if call == 3:
            return _Result(scalar=1)
        raise AssertionError(f"unexpected execute call {call}")


@pytest.mark.asyncio
async def test_apply_count_and_audit_follow_actual_returning_rows() -> None:
    first = _observation(value=2.0)
    second = _observation(value=3.0)
    second["time"] = datetime(2025, 1, 11, tzinfo=timezone.utc)
    inserted_identity = {
        key: first[key]
        for key in ("time", "series_code", "geography_key", "dimension_key")
    }
    db = _InsertAuditDB(inserted_identity)

    result = await backfill._apply_insertions(
        db,
        [first, second],
        migration_run_id="run-1",
        migration_key="migration-1",
    )

    assert result == {
        "inserted": 1,
        "audit_rows": 1,
        "verified_after_images": 1,
    }
    audit_sql = str(db.calls[1][0])
    assert "'{}'::jsonb" in audit_sql
    audit_payload = db.calls[1][1]
    assert audit_payload["operation"] == "series_resident_backfill_insert"
    assert len(__import__("json").loads(audit_payload["identities"])) == 1


def test_content_image_ignores_database_identity_and_timestamps() -> None:
    incoming = _observation()
    stored = {
        **incoming,
        "id": 99,
        "created_at": "2025-02-01T00:00:00",
        "updated_at": "2025-02-01T00:00:00",
    }

    assert backfill._content_image(incoming) == backfill._content_image(stored)


def test_database_preflight_uses_complete_explicit_date_scope() -> None:
    observation = _observation()
    build = backfill.SourceBuildResult(
        summary={},
        observations=[observation],
        registry_series_codes=(observation["series_code"],),
        requested_start_date=date(2024, 1, 1),
        requested_end_date=date(2025, 12, 31),
    )

    start_time, end_time = backfill._database_scope_bounds(build)

    assert start_time.isoformat() == "2024-01-01T00:00:00+00:00"
    assert end_time.isoformat() == "2025-12-31T23:59:59.999999+00:00"
