"""RIVM's complete annual Osiris-AIZ notification table and its footnotes."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from bs4 import BeautifulSoup

from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "rivm_notifiable_annual"
DEFAULT_SOURCE_NAME = "Netherlands RIVM Annual Notifiable Disease Notifications"
ONTOLOGY_SOURCE_ID = "SRC_NL_RIVM_NOTIFIABLE"
LANDING_URL = "https://www.rivm.nl/meldingsplicht-infectieziekten/overzicht-meldingen"
HISTORY_START_YEAR = 2016

# Every disease/category in the reviewed table, including aggregates that must
# remain distinct from individual patient notifications.
DISEASE_LABELS = (
    "COVID-19", "MERS-CoV", "Pokken", "Polio",
    "Severe Acute Respiratory Syndrome (SARS)", "Virale hemorragische koorts",
    "Difterie", "Humane infectie met dierlijk influenzavirus", "Mpox", "Pest",
    "Rabiës", "TBC", "Buiktyfus", "Cholera", "Clusters van voedselinfecties",
    "Hepatitis A", "Hepatitis B Acuut", "Hepatitis B Chronisch",
    "Hepatitis C Acuut", "Hepatitis C Chronisch", "iGAS", "Kinkhoest", "Mazelen",
    "Paratyfus A", "Paratyfus B", "Paratyfus C", "Rubella", "Shigellose", "STEC",
    "Antrax", "Bof", "Botulisme", "Brucellose", "Chikungunya", "CPE", "Dengue",
    "Gele Koorts", "Invasieve Haemophilus influenzae type b-infectie",
    "Invasieve pneumokokkenziekte", "Legionellose", "Leptospirose", "Listeriose",
    "Malaria", "Meningokokkenziekte", "MRSA -infectie (clusters buiten ziekenhuis)",
    "Orthohantavirusinfectie", "Psittacose/zoönotische chlamydia-infecties",
    "Q-koorts", "Tekenencefalitis", "Tetanus", "Trichinose", "Tularemie",
    "West-Nilevirusinfectie", "Ziekte van Creutzfeldt-Jakob (voorheen Klassiek)",
    "Ziekte van Creutzfeldt-Jakob - Variant", "Zika",
)
_CLUSTER_LABELS = frozenset({"Clusters van voedselinfecties", "MRSA -infectie (clusters buiten ziekenhuis)"})
_START_YEARS = {"COVID-19": 2020, "Mpox": 2022, "CPE": 2019, "Tekenencefalitis": 2023}


class NLNotifiableContractError(ValueError):
    """The table, its category coverage or reporting definitions changed."""


def _plain_cell(cell):
    clone = BeautifulSoup(str(cell), "html.parser")
    for node in clone.select("sup, .abbreviation-explanation"):
        node.decompose()
    return re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹]", "", clone.get_text(" ", strip=True)).strip()


def parse_notifiable_html(payload: bytes, *, start_year=HISTORY_START_YEAR, as_of=None):
    """Parse all reviewed categories; source-only geographical/metric identities survive."""
    as_of = as_of or date.today()
    soup = BeautifulSoup(payload, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find("thead")]
    if len(tables) != 1:
        raise NLNotifiableContractError("Exactly one annual notification table is required")
    table = tables[0]
    header = table.select_one("thead tr")
    try:
        years = [int(_plain_cell(cell)) for cell in header.find_all(["th", "td"], recursive=False)[1:]]
    except (AttributeError, ValueError) as exc:
        raise NLNotifiableContractError("Invalid annual table headers") from exc
    if not years or len(years) != len(set(years)) or sorted(years) != list(range(min(years), max(years) + 1)):
        raise NLNotifiableContractError("Annual years must be unique and contiguous")
    if min(years) < HISTORY_START_YEAR or max(years) >= as_of.year:
        raise NLNotifiableContractError("Annual table coverage changed or includes an unclosed year")
    # RIVM may advance a rolling annual window. A caller asking for older
    # history still receives every available year; earlier stored observations
    # remain intact because this source uses upserts, not window deletion.
    if not HISTORY_START_YEAR <= int(start_year) <= max(years):
        raise NLNotifiableContractError("Requested start year is outside published coverage")
    page_text = soup.get_text(" ", strip=True)
    date_match = re.search(r"selectiedatum\s+(\d{1,2})-(\d{1,2})-(\d{4})", page_text)
    required_notes = ("Caribisch Nederland", "1 juli 2019", "1 april 2021", "1 juli 2023", "22 mei 2022", "1 maart 2025", "vrijwillige meldingen", "eerste ziektedag", "ingetrokken")
    if not date_match or any(note not in page_text for note in required_notes):
        raise NLNotifiableContractError("Required geography/reporting/revision footnotes are absent or changed")
    day, month, year = map(int, date_match.groups())
    selected_at = date(year, month, day).isoformat()
    digest = hashlib.sha256(payload).hexdigest()
    rows, labels, group = [], set(), ""
    for tr in table.select("tbody tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) == 1 and cells[0].has_attr("colspan"):
            group = _plain_cell(cells[0])
            continue
        if len(cells) != len(years) + 1:
            raise NLNotifiableContractError("Annual disease row width does not match year headers")
        label = _plain_cell(cells[0])
        if label not in DISEASE_LABELS or label in labels:
            raise NLNotifiableContractError(f"Unreviewed or duplicate source disease category: {label}")
        labels.add(label)
        code = "osiris_" + hashlib.sha256(label.casefold().encode()).hexdigest()[:16]
        for reporting_year, cell in zip(years, cells[1:]):
            value = _plain_cell(cell).replace("\xa0", "").replace(" ", "")
            missing = value.casefold() in {"", "-", "—", "n/a", "na"}
            if not missing and not re.fullmatch(r"\d+", value):
                raise NLNotifiableContractError(f"Invalid {label}/{reporting_year} notification count: {value!r}")
            if reporting_year < int(start_year):
                continue
            # Explicit table zeroes outside documented reporting coverage are
            # scaffolding, not evidence of zero infections/notifications.
            start = _START_YEARS.get(label, HISTORY_START_YEAR)
            if reporting_year < start or (label == "COVID-19" and reporting_year > 2023):
                if value not in {"0", "", "-", "—", "n/a", "na"}:
                    raise NLNotifiableContractError("Nonzero value outside reviewed notification coverage")
                continue
            area = "BQ" if label in {"Dengue", "Chikungunya"} else "NL"
            cluster = label in _CLUSTER_LABELS
            metric, unit = ("outbreak_notifications", "events") if cluster else ("case_notifications", "count")
            basis = "mandatory_notification"
            coverage = "full_calendar_year"
            population = "source_reporting_population"
            definition = "nl_rivm_osiris_aiz_annual_v1"
            if label == "CPE" and reporting_year == 2019:
                coverage, definition = "2019-07-01/2019-12-31", "nl_cpe_from_2019_07_01"
            elif label == "COVID-19" and reporting_year in {2020, 2023}:
                coverage = "2020-01-28/2020-12-31" if reporting_year == 2020 else "2023-01-01/2023-06-30"
            elif label == "Mpox" and reporting_year == 2022:
                coverage = "2022-05-22/2022-12-31"
            elif label == "Tekenencefalitis":
                basis = "voluntary_notification" if reporting_year in {2023, 2024} else "mandatory_notification"
                definition = "nl_tbe_voluntary" if reporting_year in {2023, 2024} else "nl_tbe_mandatory_from_2025_03_01"
                if reporting_year == 2025:
                    coverage = "2025-03-01/2025-12-31"
            elif label == "Invasieve pneumokokkenziekte":
                population = "children_born_since_2006" if reporting_year <= 2020 else "children_born_since_2006_and_age_over_60_from_2021_04_01"
                definition = "nl_ipd_child_cohort" if reporting_year <= 2020 else "nl_ipd_child_cohort_and_over60_from_2021_04_01"
            dimensions = {"date_basis": "first_onset_else_diagnosis_else_notification", "population_scope": population, "reporting_basis": basis}
            rows.append({
                "Date": f"{reporting_year}-01-01", "Year": str(reporting_year),
                "RawDiseaseLabel": label, "SourceDiseaseCode": code,
                "Cases": "" if missing else str(int(value)),
                "MissingValueStatus": "source_missing" if missing else "",
                "Source": DEFAULT_SOURCE_NAME, "SourceURL": LANDING_URL,
                "GeographyKey": f"country:{area}:national", "Geography": "Caribbean Netherlands" if area == "BQ" else "Netherlands",
                "Frequency": "annual", "Measure": metric, "Unit": unit,
                "ReportingBasis": basis, "DefinitionVersion": definition,
                "ReportingCoverage": coverage, "PopulationScope": population,
                "NotificationGroup": group, "SourceSelectionDate": selected_at,
                "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
                "RawSHA256": digest, "PublicReleaseEnabled": "false",
                "CaseDefinition": "RIVM-approved Osiris-AIZ notifications; source footnotes apply. Listeriosis counts mother/child pregnancy-related reports as one infection.",
                "Dimensions": json.dumps(dimensions, sort_keys=True),
            })
    if labels != set(DISEASE_LABELS):
        raise NLNotifiableContractError(f"Annual table missing source categories: {sorted(set(DISEASE_LABELS) - labels)}")
    return sorted(rows, key=lambda row: (row["Date"], row["SourceDiseaseCode"]))


class NetherlandsNotifiableCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=60, delay=0.2)

    def fetch_rows(self, *, start_year=HISTORY_START_YEAR):
        return parse_notifiable_html(self.get(LANDING_URL).content, start_year=start_year)

    async def crawl(self, **kwargs):
        import asyncio
        rows = await asyncio.to_thread(self.fetch_rows, start_year=kwargs.get("start_year", HISTORY_START_YEAR))
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LANDING_URL, raw_data={"rows": rows})]

    def parse(self, response):
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LANDING_URL, raw_data={"rows": parse_notifiable_html(response.content)})]
