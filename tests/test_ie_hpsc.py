from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from src.core.country_library import get_country_bootstrap_config
from src.data.crawlers.ie import (
    DEFAULT_SOURCE_NAME,
    IEContractError,
    IERawProvenance,
    IEServiceContract,
    IEWeek,
    IrelandHPSCWeeklyCrawler,
    NATIONAL_FILTERS,
    REQUIRED_FIELDS,
    iter_iso_weeks,
    recent_source_weeks,
    stable_disease_code,
    validate_disease_catalog,
    validate_feature_page,
    validate_service_metadata,
)
from src.data.processors.ie import (
    IESourceSeriesCollisionError,
    IEWeeklyUpdater,
    build_legacy_projection,
)


def _metadata(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "IDHUB_AllCasesTS_L",
        "type": "Table",
        "maxRecordCount": 2000,
        "fields": [{"name": field} for field in sorted(REQUIRED_FIELDS)],
        "editingInfo": {"dataLastEditDate": 1785932755555},
    }
    payload.update(overrides)
    return payload


def _attributes(
    disease: str,
    *,
    year: int = 2026,
    week: int = 30,
    value: object = 0,
    object_id: int = 1,
) -> dict[str, object]:
    return {
        "Disease": disease,
        "year": str(year),
        "week": str(week),
        "YearWeek": f"{year:04d} W{week:02d}",
        "Value": value,
        "Unique_ID": f"{year}-{week}-{object_id}",
        "ObjectId": object_id,
        **NATIONAL_FILTERS,
    }


def _provenance() -> IERawProvenance:
    return IERawProvenance(
        request_url="https://example.test/query?where=national",
        retrieved_at="2026-08-10T00:00:00+00:00",
        response_sha256="a" * 64,
    )


def test_service_contract_fails_closed_when_required_field_disappears() -> None:
    contract = validate_service_metadata(_metadata())
    assert contract.max_record_count == 2000
    assert contract.source_updated_at == "2026-08-05T12:25:55.555000+00:00"

    payload = _metadata()
    payload["fields"] = [
        field for field in payload["fields"] if field["name"] != "Value"
    ]
    with pytest.raises(IEContractError, match="Value"):
        validate_service_metadata(payload)


def test_catalog_codes_are_stable_and_collisions_fail_closed() -> None:
    payload = {
        "features": [
            {"attributes": {"Disease": "COVID-19"}},
            {"attributes": {"Disease": "Hepatitis B (acute and chronic)"}},
        ]
    }
    assert validate_disease_catalog(payload) == {
        "covid_19": "COVID-19",
        "hepatitis_b_acute_and_chronic": "Hepatitis B (acute and chronic)",
    }
    assert stable_disease_code("  Q fever ") == "q_fever"

    with pytest.raises(IEContractError, match="collide"):
        validate_disease_catalog(
            {
                "features": [
                    {"attributes": {"Disease": "COVID-19"}},
                    {"attributes": {"Disease": "COVID 19"}},
                ]
            }
        )


def test_iso_week_revision_window_crosses_year_boundary() -> None:
    latest = IEWeek.from_parts(2026, 2)
    periods = recent_source_weeks(latest, 4)
    assert [(period.year, period.week) for period in periods] == [
        (2025, 51),
        (2025, 52),
        (2026, 1),
        (2026, 2),
    ]
    assert periods[-1].monday == date(2026, 1, 5)


def test_feature_page_preserves_zero_and_missing_separately() -> None:
    catalog = {
        "covid_19": "COVID-19",
        "measles": "Measles",
    }
    rows = validate_feature_page(
        {
            "features": [
                {"attributes": _attributes("COVID-19", value=0, object_id=1)},
                {"attributes": _attributes("Measles", value=None, object_id=2)},
            ]
        },
        catalog=catalog,
        requested_weeks={(2026, 30)},
        provenance=_provenance(),
        source_updated_at="2026-08-05T12:25:55.555000+00:00",
    )
    assert [(row["Cases"], row["ValueStatus"]) for row in rows] == [
        ("0", "zero"),
        ("", "missing"),
    ]
    assert {row["PublicReleaseEnabled"] for row in rows} == {"false"}
    assert {row["LicenseReviewStatus"] for row in rows} == {
        "written_permission_required"
    }


def test_feature_page_rejects_rows_outside_exact_national_filter() -> None:
    attributes = _attributes("Measles")
    attributes["location"] = "Dublin"
    with pytest.raises(IEContractError, match="escaped the national filter"):
        validate_feature_page(
            {"features": [{"attributes": attributes}]},
            catalog={"measles": "Measles"},
            requested_weeks={(2026, 30)},
            provenance=_provenance(),
            source_updated_at=None,
        )


def test_week_fetch_chunks_full_history_queries_to_avoid_oversized_urls() -> None:
    periods = iter_iso_weeks(IEWeek.from_parts(2025, 30), IEWeek.from_parts(2026, 2))
    assert len(periods) == 25
    payloads = []
    for start in range(0, len(periods), 12):
        batch = periods[start : start + 12]
        payloads.append(
            {
                "features": [
                    {
                        "attributes": _attributes(
                            "Measles",
                            year=period.year,
                            week=period.week,
                            object_id=index + 1,
                        )
                    }
                    for index, period in enumerate(batch)
                ]
            }
        )

    class StubCrawler(IrelandHPSCWeeklyCrawler):
        def __init__(self) -> None:
            super().__init__(delay=0)
            self.calls = 0

        def _request_json(self, *args: object, **kwargs: object):
            del args, kwargs
            payload = payloads[self.calls]
            self.calls += 1
            return payload, _provenance()

    crawler = StubCrawler()
    try:
        rows = crawler.fetch_week_rows(
            periods,
            catalog={"measles": "Measles"},
            contract=IEServiceContract(1000, None, "b" * 64),
        )
    finally:
        crawler.session.close()

    assert crawler.calls == 3
    assert len(rows) == 25
    assert {(int(row["Year"]), int(row["Week"])) for row in rows} == {
        (period.year, period.week) for period in periods
    }


def test_legacy_projection_never_sums_distinct_source_series() -> None:
    first = validate_feature_page(
        {"features": [{"attributes": _attributes("Typhoid", value=2)}]},
        catalog={"typhoid": "Typhoid"},
        requested_weeks={(2026, 30)},
        provenance=_provenance(),
        source_updated_at=None,
    )[0]
    second = validate_feature_page(
        {
            "features": [
                {"attributes": _attributes("Paratyphoid", value=3, object_id=2)}
            ]
        },
        catalog={"paratyphoid": "Paratyphoid"},
        requested_weeks={(2026, 30)},
        provenance=_provenance(),
        source_updated_at=None,
    )[0]
    mapping = {"typhoid": 26, "paratyphoid": 26}
    with pytest.raises(IESourceSeriesCollisionError):
        build_legacy_projection([first, second], mapping, country_id=1)


def test_ie_registry_mapping_and_permission_gate_are_complete() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "configs/mapping/ie.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        mapping_rows = list(csv.DictReader(handle))
    ontology = json.loads(
        (root / "configs/disease_ontology.json").read_text(encoding="utf-8")
    )
    source_series = [
        item
        for item in ontology["source_series"]
        if item["source_id"] == "SRC_IE_HPSC_NDH"
    ]
    availability = [
        item
        for item in ontology["availability"]
        if item["source_id"] == "SRC_IE_HPSC_NDH"
    ]

    assert len(mapping_rows) == 56
    assert len(source_series) == 65
    assert len(availability) == 65
    assert len({row["series_id"] for row in mapping_rows}) == len(mapping_rows)
    assert {row["source_id"] for row in mapping_rows} == {"SRC_IE_HPSC_NDH"}
    assert {row["series_id"] for row in mapping_rows} <= {
        item["id"] for item in source_series
    }
    assert sum(item.get("mapping_relation") == "unmapped" for item in source_series) == 9

    bootstrap = get_country_bootstrap_config("IE")
    assert bootstrap["public_release_enabled"] is False
    assert bootstrap["crawler_config"]["reuse_status"] == (
        "written_permission_required"
    )
    assert IEWeeklyUpdater.public_release_enabled is False


@pytest.mark.network
def test_live_hpsc_small_two_week_fetch(tmp_path: Path) -> None:
    crawler = IrelandHPSCWeeklyCrawler(
        save_raw=True,
        raw_dir=tmp_path / "raw",
        delay=0,
    )
    output = tmp_path / "ireland.csv"
    try:
        _, latest = crawler.fetch_source_bounds()
        previous_date = latest.monday.fromordinal(latest.monday.toordinal() - 7)
        previous_iso = previous_date.isocalendar()
        periods = [(previous_iso.year, previous_iso.week), (latest.year, latest.week)]
        summary = crawler.crawl_weekly_national(output, weeks=periods)
    finally:
        crawler.session.close()

    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert summary.weeks_fetched == 2
    assert summary.diseases_catalogued == 65
    assert summary.row_count == 130
    assert {row["Source"] for row in rows} == {DEFAULT_SOURCE_NAME}
    assert {row["PublicReleaseEnabled"] for row in rows} == {"false"}
    assert list((tmp_path / "raw").rglob("*.json"))
