"""Official guest summary frames: exact native selectors, national cards, closed weeks."""
import hashlib
import io
import zipfile
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.th_dds import parse_calendar_workbook
from src.data.crawlers.th_tableau import (
    REVIEWED_LABELS,
    TABLE,
    TableauContractError,
    build_row,
    card_filter_models,
    count,
    filter_models,
    inventory,
    national_snapshot,
    offline_workbook,
    replay_response,
    source_code,
)
from src.data.processors.th_tableau import ThailandDDSTableauUpdater
from src.services.ingestion.countries import th_tableau

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/ingestion/TH"
RAW = (FIXTURE / "tableau/single_week_flu_report.zip").read_bytes()
CALENDAR = parse_calendar_workbook((FIXTURE / "dds/calendar_2026.xlsx").read_bytes(), year=2026)
FLU = "Influenza, (Flu) "


@pytest.fixture(scope="module")
def replayed():
    entries, plan = th_tableau.unpack_bundle(RAW)
    bootstrap = next(e for e in entries if e["metadata"]["operation"] == "bootstrap")
    scraper, workbook = offline_workbook(bootstrap["payload"])
    catalog = inventory(workbook)
    filters = filter_models(bootstrap["payload"])
    card_filters = card_filter_models(bootstrap["payload"])
    for entry in entries[2:]:
        workbook = replay_response(scraper, workbook, entry["payload"])
        filters.update(filter_models(entry["payload"]))
        for card, models in card_filter_models(entry["payload"]).items():
            card_filters[card].update(models)
    return workbook, filters, catalog, plan, card_filters


def snapshot(replayed, **changes):
    workbook, filters, catalog, _, card_filters = replayed
    return national_snapshot(workbook, year=2026, basis="report", week=46,
        provinces=catalog["provinces"], label=FLU, filters=changes.pop("filters", filters),
        card_filters=changes.pop("card_filters", card_filters), **changes)


def test_live_all_category_inventory_retains_combined_labels_and_current_year(replayed):
    _, _, catalog, _, _ = replayed
    assert len(catalog["labels"]) == 64
    assert catalog["years"] == [2026, 2025, 2024, 2023, 2022, 2021]
    assert "Fever of unknown origin (FUO)" in catalog["labels"]
    assert "Viral conjunctivitis" in catalog["labels"]
    assert "Coronavirus disease 2019 (COVID-19),MISC from COVID-19" in catalog["labels"]
    assert "Syphilis, Congenital syphilis " in catalog["labels"]
    assert len({source_code(label) for label in catalog["labels"]}) == 64


def test_reordered_and_new_native_selector_categories_continue_without_guessed_mapping():
    entries, _ = th_tableau.unpack_bundle(RAW)
    _, workbook = offline_workbook(entries[1]["payload"])
    controls = deepcopy(workbook.getWorksheet(TABLE).getFilters())
    disease = next(x for x in controls if x["column"] == "โรค")
    disease["values"] = list(reversed(disease["values"])) + ["New source combined category"]
    fake = SimpleNamespace(getParameters=workbook.getParameters,
        getWorksheet=lambda name: SimpleNamespace(getFilters=lambda: controls))
    catalog = inventory(fake)
    assert len(catalog["labels"]) == 65
    assert catalog["new_labels"] == ["New source combined category"]
    assert catalog["missing_reviewed_labels"] == []
    assert set(REVIEWED_LABELS) < set(catalog["labels"])


def test_single_week_explicit_national_cards_include_unknown_geography(replayed):
    report = snapshot(replayed)
    assert report == {"cases": 32529, "deaths": 4, "week": 46, "unknown_cases": 4,
                      "unknown_deaths": 0, "national_evidence": "explicit_national_cards"}
    row = build_row(report, label=FLU, year=2026, week=46, basis="report", calendar=CALENDAR,
                    as_of=date(2026, 11, 15), raw_sha256="raw", retrieved_at="captured")
    assert row["PeriodStart"] == "2026-11-08" and row["PeriodEnd"] == "2026-11-14"
    assert row["Cases"] == "32529" and row["UnknownLocationCases"] == "4"
    assert row["TimeBasis"] == "report" and row["PublicReleaseEnabled"] == "false"
    assert row["SourceScope"] == "th_dds_tableau"


@pytest.mark.parametrize("field", ["โรค", "สัปดาห์", "วัน/สถานที่"])
def test_domain_inventory_is_not_evidence_of_selected_filter(replayed, field):
    filters = deepcopy(replayed[1])
    filters[field]["rangeType"] = "all"
    with pytest.raises(TableauContractError, match="selected tuples"):
        snapshot(replayed, filters=filters)


def test_restricted_age_or_province_is_never_relabelled_national(replayed):
    for field in ["จังหวัด", "กลุ่มอายุ"]:
        filters = deepcopy(replayed[1])
        filters[field].update(all=False, allChecked=False, rangeType="selected")
        with pytest.raises(TableauContractError, match="restricted dimension"):
            snapshot(replayed, filters=filters)


@pytest.mark.parametrize("week", [47, 48, 53])
def test_unclosed_week_and_future_zero_placeholders_never_become_rows(replayed, week):
    with pytest.raises(TableauContractError, match="Open/future"):
        build_row(snapshot(replayed), label=FLU, year=2026, week=week, basis="report",
                  calendar=CALENDAR, as_of=date(2026, 11, 15), raw_sha256="raw", retrieved_at="captured")
    with pytest.raises(TableauContractError, match="closed source weeks"):
        th_tableau.closed_weeks(CALENDAR, as_of=date(2026, 11, 15), weeks=[week])


def test_dynamic_recent_closed_weeks_use_verified_calendar():
    assert th_tableau.closed_weeks(CALENDAR, as_of=date(2026, 11, 15)) == [46]
    assert th_tableau.closed_weeks(CALENDAR, as_of=date(2026, 11, 15), recent_weeks=3) == [44, 45, 46]
    assert th_tableau.closed_weeks(CALENDAR, as_of=date(2026, 11, 14)) == [45]


@pytest.mark.parametrize("invalid", ["", "-1", "*", "-", "1.5", "NaN", None])
def test_missing_or_invalid_counts_never_turn_into_zero(invalid):
    with pytest.raises(TableauContractError, match="not an integer"):
        count(invalid)


def test_explicit_zero_keeps_reported_status(replayed):
    report = {**snapshot(replayed), "cases": 0, "deaths": 0, "unknown_cases": 0}
    row = build_row(report, label="Anthrax", year=2026, week=46, basis="report", calendar=CALENDAR,
                    as_of=date(2026, 11, 15), raw_sha256="raw", retrieved_at="captured")
    assert row["Cases"] == "0" and row["MissingValueStatus"] == "reported"


def test_basis_and_year_are_checked_in_source_values(replayed):
    workbook, filters, catalog, _, card_filters = replayed
    with pytest.raises(TableauContractError, match="Source year"):
        national_snapshot(workbook, year=2025, basis="report", week=46,
                          provinces=catalog["provinces"], label=FLU, filters=filters, card_filters=card_filters)
    with pytest.raises(TableauContractError, match="selected tuples"):
        national_snapshot(workbook, year=2026, basis="onset", week=46,
                          provinces=catalog["provinces"], label=FLU, filters=filters, card_filters=card_filters)


def test_source_filter_must_target_both_national_cards(replayed):
    links = deepcopy(replayed[4])
    links["ผู้เสียชีวิต"]["วัน/สถานที่"]["targetSheets"] = [TABLE]
    with pytest.raises(TableauContractError, match="linked to both national cards"):
        snapshot(replayed, card_filters=links)


def test_real_sparse_provinces_zero_cards_and_absent_basis_display():
    raw = (FIXTURE / "tableau/sparse_and_zero_report.zip").read_bytes()
    entries, _ = th_tableau.unpack_bundle(raw)
    scraper, workbook = offline_workbook(entries[1]["payload"])
    catalog, filters = inventory(workbook), filter_models(entries[1]["payload"])
    cards = card_filter_models(entries[1]["payload"])
    actual = {}
    for entry in entries[2:]:
        workbook = replay_response(scraper, workbook, entry["payload"])
        filters.update(filter_models(entry["payload"]))
        for card, models in card_filter_models(entry["payload"]).items():
            cards[card].update(models)
        if entry["metadata"].get("report"):
            label = entry["metadata"]["label"]
            actual[label] = national_snapshot(workbook, year=2026, basis="report", week=46,
                provinces=catalog["provinces"], label=label, filters=filters, card_filters=cards)
            if label == "Acute hepatitis A Virus":
                assert len(workbook.getWorksheet(TABLE).data) == 115  # only 23 province/metric groups
            if label in {"Anthrax", "Acute hepatitis E virus"}:
                assert workbook.getWorksheet("type time").data.empty
    assert actual["Acute hepatitis A Virus"]["cases"] == 14
    assert actual["Acute hepatitis A Virus"]["unknown_cases"] is None
    assert actual["Acute hepatitis E virus"]["cases"] == 3
    assert actual["Anthrax"]["cases"] == actual["Anthrax"]["deaths"] == 0


def test_raw_bundle_member_tampering_rejected():
    with zipfile.ZipFile(io.BytesIO(RAW)) as source:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as target:
            for name in source.namelist():
                target.writestr(name, source.read(name) + (b" " if name.endswith("filter.json") else b""))
    with pytest.raises(TableauContractError, match="hash mismatch"):
        th_tableau.unpack_bundle(stream.getvalue())


def test_organize_replays_raw_controls_without_network(monkeypatch):
    def artifact(payload, **metadata):
        return SimpleNamespace(payload=payload, metadata_=metadata, source_url="official")
    raw_rar = (FIXTURE / "dds/calendar_2026.rar").read_bytes()
    artifacts = [artifact(RAW, endpoint="summary_bundle", bundle_sha256=hashlib.sha256(RAW).hexdigest()),
        artifact((FIXTURE / "dds/calendar_2026.xlsx").read_bytes(), endpoint="calendar_workbook", year=2026,
                 parent_raw_sha256=hashlib.sha256(raw_rar).hexdigest()),
        artifact(raw_rar, endpoint="calendar_rar", year=2026)]
    monkeypatch.setattr(th_tableau, "read_artifact", lambda item: item.payload)
    monkeypatch.setattr("requests.Session.request", lambda *args, **kwargs: pytest.fail("offline organizer made a network request"))
    result = th_tableau.organize(artifacts, updater=ThailandDDSTableauUpdater())
    assert len(result.rows) == 1 and result.rows[0]["Cases"] == "32529"
    assert result.source_audit["status"] == "validated"
    assert result.source_audit["source_inventory_categories"] == 64
    assert result.source_audit["national_evidence"] == "explicit_national_cards"
    assert ThailandDDSTableauUpdater.mapping_notifications_enabled is False
