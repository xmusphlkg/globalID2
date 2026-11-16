from datetime import date
from pathlib import Path

import pytest

from src.data.crawlers.cr_notifications import (
    BULLETINS_URL, CostaRicaContractError, discover, parse_report_pdf,
    parse_snapshot_text, publication_date,
)
from src.data.processors.cr_notifications import CostaRicaNotificationsUpdater


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/CR"


@pytest.fixture(scope="module")
def annual():
    return parse_report_pdf((FIXTURES / "morbidity_2015.pdf").read_bytes(), {
        "url": "https://www.ministeriodesalud.go.cr/2015/file", "kind": "annual_individual", "year": 2015})


@pytest.fixture(scope="module")
def snapshots():
    return parse_report_pdf((FIXTURES / "bulletin_28_2026.pdf").read_bytes(), {
        "url": "https://www.ministeriodesalud.go.cr/2026/file", "kind": "bulletin", "year": 2026})


def test_real_annual_counts_and_explicit_zeroes(annual):
    rows, audit = annual
    assert len(rows) == 64
    labels = {r["RawDiseaseLabel"]: r for r in rows}
    assert labels["Dengue Clásico"]["Cases"] == "14886"
    assert labels["Cólera"]["Cases"] == "0"
    assert labels["Cólera"]["MissingValueStatus"] == "reported"
    assert labels["VIH*"]["IsProvisional"] == "true"
    assert labels["Sífilis"]["Cases"] == "2338"
    assert labels["Sífilis"]["SourceReportedRate"] == "48,38"
    assert all(r["GeographyKey"] == "country:CR:national" for r in rows)
    assert len(audit["rejected_cells"]) == 1
    assert "1b2io0logicas" in audit["rejected_cells"][0]["text"]


def test_annual_finalization_and_count_revision_preserve_source_identity(annual):
    import re

    import pdfplumber

    from src.data.crawlers.cr_notifications import parse_annual_text

    report = {"url": "https://www.ministeriodesalud.go.cr/2015/file", "year": 2015}
    with pdfplumber.open(FIXTURES / "morbidity_2015.pdf") as pdf:
        original_text = pdf.pages[9].extract_text()
    final_text = original_text.replace("*", "")
    final_rows, _ = parse_annual_text(final_text, report, 10)
    final_by_label = {row["RawDiseaseLabel"]: row for row in final_rows}
    preliminary_rows = [row for row in annual[0] if "*" in row["RawDiseaseLabel"]]
    assert len(preliminary_rows) == 5
    for preliminary in preliminary_rows:
        final = final_by_label[preliminary["RawDiseaseLabel"].replace("*", "")]
        assert preliminary["IsProvisional"] == "true"
        assert final["IsProvisional"] == "false"
        assert preliminary["SourceDiseaseCode"] == final["SourceDiseaseCode"]
        assert preliminary["Dimensions"] == final["Dimensions"]
        assert {key: value for key, value in preliminary.items()
                if key not in {"RawDiseaseLabel", "IsProvisional"}} == {
                    key: value for key, value in final.items()
                    if key not in {"RawDiseaseLabel", "IsProvisional"}}

    revised_text, replacements = re.subn(
        r"(?m)^(VIH\s+)\d+(\s+\d+(?:[,.]\d+)?)$",
        lambda match: match[1] + "999" + match[2], final_text,
    )
    assert replacements == 1
    revised_rows, _ = parse_annual_text(revised_text, report, 10)
    revised = next(row for row in revised_rows if row["RawDiseaseLabel"] == "VIH")
    final = final_by_label["VIH"]
    assert revised["Cases"] != final["Cases"]
    assert revised["SourceDiseaseCode"] == final["SourceDiseaseCode"]
    assert revised["Dimensions"] == final["Dimensions"]


def test_snapshot_is_cumulative_publication_date_not_issue_or_iso_week(snapshots):
    rows, audit = snapshots
    assert len(rows) == 3
    assert {r["Cases"] for r in rows} == {"2425", "113", "71"}
    assert all(r["Date"] == "2026-08-07" for r in rows)
    assert all(r["SourceReportedEpidemiologicalWeek"] == 29 for r in rows)
    assert all(r["PeriodType"] == "cumulative" for r in rows)
    assert all("PeriodEnd" not in r for r in rows)
    assert len({r["SourceDiseaseCode"] for r in rows}) == 3
    assert audit["status"] == "parsed_partial"


def test_counts_require_reconciliation_of_all_regions():
    import pdfplumber
    with pdfplumber.open(FIXTURES / "bulletin_28_2026.pdf") as pdf:
        original = pdf.pages[2].extract_text()
    report = {"url": "https://www.ministeriodesalud.go.cr/test", "year": 2026}
    with pytest.raises(CostaRicaContractError, match="nine health regions"):
        parse_snapshot_text(original.replace("Total 2425", "Total 2426"), report, 3, date(2026, 8, 7))


def test_wrong_annual_geography_or_year_rejected():
    from src.data.crawlers.cr_notifications import parse_annual_text
    text = "Cuadro N°1\nCasos registrados de Enfermedades de Declaración Obligatoria\nCosta Rica 2014\nCausas Nº Tasa"
    with pytest.raises(CostaRicaContractError, match="year"):
        parse_annual_text(text, {"year": 2015}, 1)


def test_source_discovery_pagination_year_window_and_external_links():
    page = (f'<a href="{BULLETINS_URL}/boletines-epidemiologicos-2026">2026</a>'
            f'<a href="{BULLETINS_URL}/boletines-epidemiologicos-2019">2019</a>'
            f'<a href="{BULLETINS_URL}?limit=20&amp;limitstart=20">Next</a>'
            f'<a href="{BULLETINS_URL}/boletines-epidemiologicos-2026/123/file">pdf test</a>'
            '<a href="https://example.org/boletines-epidemiologicos-2026/123/file">pdf fake</a>')
    dirs, pages, reports = discover(page.encode(), BULLETINS_URL, start_year=2025, end_year=2026)
    assert list(dirs.values()) == [2026]
    assert len(pages) == 1
    assert len(reports) == 1 and reports[0]["year"] == 2026


def test_publication_date_requires_expected_year():
    assert publication_date("7 de agosto de 2026", 2026) == date(2026, 8, 7)
    with pytest.raises(CostaRicaContractError):
        publication_date("7 de agosto de 2025", 2026)


def test_updater_has_separate_primary_source_and_coverage(annual):
    updater = CostaRicaNotificationsUpdater()
    result = updater.prepare_rows(annual[0], source="cr_moh_notifications", source_audit={"coverage_complete": False})
    assert updater.ontology_source_id == "SRC_CR_MOH_NOTIFICATIONS"
    assert updater.public_release_enabled is False
    assert result.source_audit["observations"] == 64
    assert result.source_audit["coverage_complete"] is False
    with pytest.raises(ValueError, match="No verified"):
        updater.prepare_rows([], source="cr_moh_notifications")


def test_incomplete_pdf_rejected():
    with pytest.raises(CostaRicaContractError, match="Incomplete"):
        parse_report_pdf(b"%PDF-bad", {"kind": "bulletin", "year": 2026})


def test_organization_replays_captured_plan_and_rejects_corruption(tmp_path, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace
    from src.data.crawlers.cr_notifications import INDIVIDUAL_URL
    from src.services.ingestion.countries.cr_notifications import organize

    original = (FIXTURES / "morbidity_2015.pdf").read_bytes()
    monkeypatch.chdir(tmp_path)
    directory = Path("data/raw/test_cr")
    directory.mkdir(parents=True)
    url = INDIVIDUAL_URL + "/1673-boletin-de-morbilidad-enfermedades-de-declaracion-obligatoria-2015/file"
    title = "pdf Boletín de Morbilidad Enfermedades de Declaración Obligatoria 2015"
    listing = f'<a href="{url}">{title}</a>'.encode()
    _, _, reports = discover(listing, INDIVIDUAL_URL)
    report = reports[0]

    def artifact(name, data, source_url, metadata):
        path = directory / name
        path.write_bytes(data)
        return SimpleNamespace(relative_path=str(path), source_url=source_url, metadata_=metadata,
                               sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))

    plan = {"reports": reports, "failures": [], "start_year": 2014, "end_year": 2026,
            "directories": [INDIVIDUAL_URL], "captured_at": "2026-11-16T00:00:00+00:00"}
    artifacts = [artifact("directory.html", listing, INDIVIDUAL_URL, {"endpoint": "directory"}),
                 artifact("original.pdf", original, url, {"endpoint": "report", "report": report}),
                 artifact("plan.json", json.dumps(plan).encode(), BULLETINS_URL, {"endpoint": "plan"})]
    monkeypatch.setattr("src.data.crawlers.cr_notifications.CostaRicaNotificationsCrawler.get",
                        lambda *a, **k: pytest.fail("Offline organization performed a network request"))
    result = organize(artifacts, updater=CostaRicaNotificationsUpdater())
    assert len(result.rows) == 64
    assert result.source_audit["coverage_complete"] is False
    with pytest.raises(CostaRicaContractError, match="duplicated"):
        organize(artifacts + [artifacts[1]], updater=CostaRicaNotificationsUpdater())
    (directory / "original.pdf").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        organize(artifacts, updater=CostaRicaNotificationsUpdater())
