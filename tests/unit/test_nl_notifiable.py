"""Complete RIVM notification tables retain geography and reporting definitions."""
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest
import requests

from src.data.crawlers.nl_notifiable import (
    DISEASE_LABELS, NLNotifiableContractError, parse_notifiable_html,
)
from src.data.processors.nl_notifiable import NLNotifiableAnnualUpdater
from src.services.ingestion.countries.nl_notifiable import organize

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/ingestion/NL/rivm_notifiable_annual.html"


def payload():
    return FIXTURE.read_bytes()


def rows():
    return parse_notifiable_html(payload(), as_of=date(2026, 11, 15))


def one(label, year):
    return next(row for row in rows() if row["RawDiseaseLabel"] == label and row["Year"] == str(year))


def test_full_official_table_retains_every_disease_and_fixed_known_counts():
    actual = rows()
    assert len(actual) == 538
    assert {row["RawDiseaseLabel"] for row in actual} == set(DISEASE_LABELS)
    assert len(DISEASE_LABELS) == 56
    assert one("Kinkhoest", 2024)["Cases"] == "18213"
    assert one("Mazelen", 2025)["Cases"] == "539"
    assert one("Hepatitis B Acuut", 2025)["Cases"] == "100"
    assert one("Hepatitis B Chronisch", 2025)["Cases"] == "771"
    assert one("Pokken", 2025)["Cases"] == "0"
    assert {row["SourceSelectionDate"] for row in actual} == {"2026-03-10"}
    assert len({(row["Date"], row["SourceDiseaseCode"], row["GeographyKey"], row["DefinitionVersion"]) for row in actual}) == 538


def test_caribbean_only_notifications_are_never_netherlands_national_counts():
    for label in ("Dengue", "Chikungunya"):
        selected = [row for row in rows() if row["RawDiseaseLabel"] == label]
        assert len(selected) == 10
        assert {row["GeographyKey"] for row in selected} == {"country:BQ:national"}
    assert one("Dengue", 2025)["Cases"] == "309"
    assert one("Malaria", 2025)["GeographyKey"] == "country:NL:national"
    updater = NLNotifiableAnnualUpdater()
    assert updater.series_geography_from_rows is True
    assert not getattr(updater, "series_geography_key", None)


def test_outbreaks_and_listeriosis_keep_source_measure_semantics():
    for label in ("Clusters van voedselinfecties", "MRSA -infectie (clusters buiten ziekenhuis)"):
        row = one(label, 2025)
        assert (row["Measure"], row["Unit"]) == ("outbreak_notifications", "events")
    row = one("Listeriose", 2025)
    assert "one infection" in row["CaseDefinition"]
    assert (row["Measure"], row["Unit"]) == ("case_notifications", "count")


def test_nonreportable_zero_scaffolding_is_excluded_and_partial_years_are_explicit():
    by_label = lambda label: {row["Year"] for row in rows() if row["RawDiseaseLabel"] == label}
    assert by_label("COVID-19") == {"2020", "2021", "2022", "2023"}
    assert by_label("Mpox") == {"2022", "2023", "2024", "2025"}
    assert min(by_label("CPE")) == "2019"
    assert by_label("Tekenencefalitis") == {"2023", "2024", "2025"}
    assert one("COVID-19", 2023)["ReportingCoverage"] == "2023-01-01/2023-06-30"
    assert one("CPE", 2019)["ReportingCoverage"] == "2019-07-01/2019-12-31"
    assert one("Mpox", 2022)["ReportingCoverage"] == "2022-05-22/2022-12-31"


def test_tbe_voluntary_and_mandatory_and_pneumococcal_age_contracts_are_distinct():
    before, after = one("Tekenencefalitis", 2024), one("Tekenencefalitis", 2025)
    assert before["ReportingBasis"] == "voluntary_notification"
    assert after["ReportingBasis"] == "mandatory_notification"
    assert before["DefinitionVersion"] != after["DefinitionVersion"]
    before, after = one("Invasieve pneumokokkenziekte", 2020), one("Invasieve pneumokokkenziekte", 2021)
    assert before["DefinitionVersion"] != after["DefinitionVersion"]
    assert "over_60" in json.loads(after["Dimensions"])["population_scope"]


def test_missing_counts_remain_unknown_instead_of_zero():
    soup = BeautifulSoup(payload(), "html.parser")
    for tr in soup.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if cells and cells[0].get_text(strip=True) == "Mazelen":
            cells[1].string = "—"
            break
    actual = parse_notifiable_html(str(soup).encode(), as_of=date(2026, 11, 15))
    missing = next(row for row in actual if row["RawDiseaseLabel"] == "Mazelen" and row["Year"] == "2025")
    assert missing["Cases"] == ""
    assert missing["MissingValueStatus"] == "source_missing"


@pytest.mark.parametrize("mutation", ["category", "width", "footnote", "count", "duplicate"])
def test_structural_drift_and_conflicting_duplicate_categories_fail_closed(mutation):
    soup = BeautifulSoup(payload(), "html.parser")
    tr = soup.select("tbody tr")[1]
    if mutation == "category":
        tr.find("td").string = "Unreviewed disease"
    elif mutation == "width":
        tr.find_all("td")[-1].decompose()
    elif mutation == "footnote":
        soup = BeautifulSoup(str(soup).replace("1 juli 2023", "2 juli 2023"), "html.parser")
    elif mutation == "count":
        tr.find_all("td")[1].string = "<5"
    else:
        tr.insert_after(BeautifulSoup(str(tr), "html.parser"))
    with pytest.raises(NLNotifiableContractError):
        parse_notifiable_html(str(soup).encode(), as_of=date(2026, 11, 15))


def test_unclosed_year_and_unpublished_start_year_are_rejected():
    with pytest.raises(NLNotifiableContractError, match="unclosed"):
        parse_notifiable_html(payload(), as_of=date(2025, 12, 31))
    with pytest.raises(NLNotifiableContractError, match="outside published"):
        parse_notifiable_html(payload(), start_year=2026, as_of=date(2026, 11, 15))


def test_next_closed_year_can_be_collected_without_changing_start_year():
    soup = BeautifulSoup(payload(), "html.parser")
    header = soup.select_one("thead tr")
    new_year = soup.new_tag("th")
    new_year.string = "2026"
    header.find_all(["th", "td"], recursive=False)[0].insert_after(new_year)
    for tr in soup.select("tbody tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) > 1:
            # Preserve reviewed source semantics: no new COVID notifications
            # after its reporting obligation ended, and all other values are
            # copied solely to exercise advancing annual table structure.
            cells[0].insert_after(BeautifulSoup(str(cells[1]), "html.parser"))
    actual = parse_notifiable_html(str(soup).encode(), as_of=date(2027, 3, 1))
    assert max(row["Year"] for row in actual) == "2026"
    assert any(row["Year"] == "2016" for row in actual)
    assert not any(row["Year"] == "2026" and row["RawDiseaseLabel"] == "COVID-19" for row in actual)


def test_advancing_source_window_does_not_require_older_years_to_remain_online():
    soup = BeautifulSoup(payload(), "html.parser")
    soup.select_one("thead tr").find_all(["th", "td"], recursive=False)[-1].decompose()
    for tr in soup.select("tbody tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) > 1:
            cells[-1].decompose()
    actual = parse_notifiable_html(str(soup).encode(), start_year=2016, as_of=date(2026, 11, 15))
    assert min(row["Year"] for row in actual) == "2017"
    assert max(row["Year"] for row in actual) == "2025"


def test_staged_organization_replays_fixed_artifact_without_network(tmp_path, monkeypatch):
    raw = payload()
    monkeypatch.chdir(tmp_path)
    path = Path("data/raw/nl/annual.html")
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    artifact = SimpleNamespace(kind="rivm_notifiable_html", relative_path=str(path),
        byte_size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        metadata_={"as_of": "2026-11-15", "start_year": 2024})
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: pytest.fail("organize attempted source HTTP"))
    fetched = organize([artifact], updater=NLNotifiableAnnualUpdater())
    assert {row["Year"] for row in fetched.rows} == {"2024", "2025"}
    assert len({row["RawDiseaseLabel"] for row in fetched.rows}) == 55
    assert fetched.source_latest_date == date(2025, 1, 1)
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        organize([artifact], updater=NLNotifiableAnnualUpdater())
