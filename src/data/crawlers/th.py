"""Reviewed Thailand Report 506 monthly history, independent of DDS dashboards.

Only explicit national Total rows from linked monthly RTF tables are accepted.
The source is historical notification surveillance, mostly suspected cases;
neither geographic reporting completeness nor confirmed infections is implied.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from .base import BaseCrawler

DEFAULT_SCOPE = "th_doe506_monthly"
DEFAULT_SOURCE_NAME = "Thailand DOE Report 506 Monthly History"
ONTOLOGY_SOURCE_ID = "SRC_TH_DOE506"
INDEX_URL = "http://doe1.moph.go.th/surdata/index.php"
CONTRACT_VERSION = "th-doe506-national-monthly-rtf-v1"
HISTORY_FIRST_YEAR = 2003
DEFAULT_START_YEAR = None
DEFAULT_END_YEAR = None
DEFAULT_MAX_REPORTS = 216


class Thailand506ContractError(ValueError):
    """The source cannot safely be interpreted under the reviewed contract."""


@dataclass(frozen=True)
class DiseaseContract:
    code: str
    label: str
    report_label: str


# Full official index reviewed on 2026-11-15. Aggregate and component
# categories keep separate native identities; they must never be summed.
DISEASE_CONTRACTS = (
    DiseaseContract('262766', 'DHF Total', "D.H.F,Total(26,27,66)"),
    DiseaseContract('2122', 'Measles Total', "Measles,total (21-22)"),
    DiseaseContract('2829', 'Encephalitis Total', "Encephalitis,total(28,29)"),
    DiseaseContract('323334', 'TB Total', "Tuberculosis,total(32-34)"),
    DiseaseContract('37383940417980', 'STI Total', "S.T.I.(total)"),
    DiseaseContract('40', 'Chancroid', ""),
    DiseaseContract('80', 'Condyloma Acuminata', ""),
    DiseaseContract('79', 'Genital Herpes Simplex', ""),
    DiseaseContract('38', 'Gonorrhoea', ""),
    DiseaseContract('41', 'L.G.V.&other&unsp.V.D', ""),
    DiseaseContract('39', 'N.S.U./V', ""),
    DiseaseContract('81', 'Other STI', ""),
    DiseaseContract('37', 'Syphilis', ""),
    DiseaseContract('45', 'Anthrax', ""),
    DiseaseContract('91', 'Avian Influenza', ""),
    DiseaseContract('85', 'Botulism', ""),
    DiseaseContract('83', 'Brucellosis', ""),
    DiseaseContract('17', 'Chickenpox', ""),
    DiseaseContract('84', 'Chikungunya', ""),
    DiseaseContract('01', 'Cholera', ""),
    DiseaseContract('26', 'D.H.F', ""),
    DiseaseContract('27', 'D.H.F.shock syndrome', ""),
    DiseaseContract('66', 'Dengue fever', ""),
    DiseaseContract('23', 'Diphtheria', ""),
    DiseaseContract('06', 'Dysentery,Amoebic', ""),
    DiseaseContract('05', 'Dysentery,Bacillary', ""),
    DiseaseContract('28', 'Encephalitis uns', ""),
    DiseaseContract('90', 'Enterovirus Fever', ""),
    DiseaseContract('55', 'Eosinophilic Meningitis', ""),
    DiseaseContract('76', 'Filariasis', ""),
    DiseaseContract('03', 'Food_Poisoning', ""),
    DiseaseContract('89', 'Genital Molluscum Contagiosum', ""),
    DiseaseContract('11', 'HepatitisA', ""),
    DiseaseContract('12', 'HepatitisB', ""),
    DiseaseContract('13', 'HepatitisC', ""),
    DiseaseContract('69', 'HepatitisD', ""),
    DiseaseContract('70', 'HepatitisE', ""),
    DiseaseContract('10', 'Hepatitis_uns', ""),
    DiseaseContract('71', 'HFM', ""),
    DiseaseContract('15', 'Influenza', ""),
    DiseaseContract('29', 'Japanese B encephalitis', ""),
    DiseaseContract('68', 'Kala azar', ""),
    DiseaseContract('35', 'Leprosy', ""),
    DiseaseContract('43', 'Leptospirosis', ""),
    DiseaseContract('75', 'Liver fluke', ""),
    DiseaseContract('30', 'Malaria', ""),
    DiseaseContract('21', 'Measles', ""),
    DiseaseContract('22', 'Measles c Complication', ""),
    DiseaseContract('72', 'Melioidosis', ""),
    DiseaseContract('54', 'Meningitis,uns', ""),
    DiseaseContract('19', 'Meningococcal Meningitis', ""),
    DiseaseContract('52', 'Mumps', ""),
    DiseaseContract('58', 'Mushroom poisoning', ""),
    DiseaseContract('09', 'Paratyphoid', ""),
    DiseaseContract('88', 'Pediculosis Pubis', ""),
    DiseaseContract('24', 'Pertussis', ""),
    DiseaseContract('31', 'Pneumonia', ""),
    DiseaseContract('20', 'Poliomyelitis', ""),
    DiseaseContract('32', 'Pulmonary T.B', ""),
    DiseaseContract('42', 'Rabies', ""),
    DiseaseContract('16', 'Rubella', ""),
    DiseaseContract('74', 'Scarlet fever', ""),
    DiseaseContract('44', 'Scrub Typhus', ""),
    DiseaseContract('82', 'Streptococcus suis', ""),
    DiseaseContract('34', 'T.B. other organs', ""),
    DiseaseContract('33', 'T.B.meningitis', ""),
    DiseaseContract('25', 'Tetanus exc.Neo', ""),
    DiseaseContract('53', 'Tetanus neonatorum', ""),
    DiseaseContract('46', 'Trichinosis', ""),
    DiseaseContract('08', 'Typhoid', ""),
    DiseaseContract('86', 'Vaginal trichomoniasis', ""),
    DiseaseContract('87', 'Zika virus', ""),
)
CONTRACTS_BY_CODE = {item.code: item for item in DISEASE_CONTRACTS}


def selected_contracts(disease_codes=None):
    codes = list(CONTRACTS_BY_CODE) if disease_codes is None else list(disease_codes)
    if not codes or len(set(codes)) != len(codes) or any(code not in CONTRACTS_BY_CODE for code in codes):
        raise Thailand506ContractError("Expected unique reviewed Report 506 disease codes")
    return [CONTRACTS_BY_CODE[code] for code in codes]


def validate_year_window(start_year, end_year):
    start_year, end_year = int(start_year), int(end_year)
    if not HISTORY_FIRST_YEAR <= start_year <= end_year < date.today().year:
        raise Thailand506ContractError("Request closed calendar years, starting no earlier than 2003")
    return list(range(start_year, end_year + 1))


def discover_diseases(payload: bytes):
    found = {}
    soup = BeautifulSoup(payload, "html.parser")
    for anchor in soup.select("a[href]"):
        url = urljoin(INDEX_URL, anchor["href"])
        parsed = urlsplit(url)
        if parsed.netloc != "doe1.moph.go.th" or parsed.path != "/surdata/disease.php":
            continue
        code = parse_qs(parsed.query).get("ds", [""])[0]
        if re.fullmatch(r"(?:\d{2})+", code):
            if code not in CONTRACTS_BY_CODE:
                raise Thailand506ContractError(f"Unreviewed Report 506 source category {code}")
            if code in found:
                raise Thailand506ContractError(f"Duplicate source category {code}")
            label = " ".join(anchor.get_text(" ", strip=True).split())
            if label != CONTRACTS_BY_CODE[code].label:
                raise Thailand506ContractError(f"Source label changed for {code}: {label}")
            found[code] = url
    return found


def discover_monthly_reports(payload: bytes, *, disease_code: str):
    """Discover actual monthly-history links; a listed file may still be absent."""
    candidates = {}
    pattern = re.compile(r"/surdata/y(\d{2})/t?mcd_[A-Za-z0-9_,.]+_(\d{2})\.rtf$", re.I)
    for anchor in BeautifulSoup(payload, "html.parser").select("a[href]"):
        url = urljoin(INDEX_URL, anchor["href"])
        parsed = urlsplit(url)
        match = pattern.fullmatch(parsed.path)
        if parsed.netloc != "doe1.moph.go.th" or parsed.query or not match or match[1] != match[2]:
            continue
        year = 2500 + int(match[1]) - 543
        if not HISTORY_FIRST_YEAR <= year <= 2056:
            continue
        if year in candidates and candidates[year] != url:
            raise Thailand506ContractError(f"Ambiguous linked monthly count table for {disease_code}/{year}")
        candidates[year] = url
    return candidates


def discover_monthly_report(payload: bytes, *, disease_code: str, year: int):
    candidates = discover_monthly_reports(payload, disease_code=disease_code)
    if year not in candidates:
        raise Thailand506ContractError(f"Expected one linked monthly count table for {disease_code}/{year}; found 0")
    return candidates[year]


def plan_history_reports(histories, *, disease_codes=None, start_year=None, end_year=None,
                         mode="incremental", report_offset=0, max_reports=DEFAULT_MAX_REPORTS,
                         resume_plan_hash=None, recent_years=3, as_of=None):
    """Bound requests to a deterministic, hash-identified discovery snapshot.

    Incremental runs recheck the most recent advertised closed years, including
    unavailable or preclosure files, so a newly corrected source is detected.
    Backfills traverse all linked history; callers advance the cursor only
    after successfully storing a batch.
    """
    contracts = selected_contracts(disease_codes)
    as_of = date.today() if as_of is None else date.fromisoformat(str(as_of))
    first = HISTORY_FIRST_YEAR if start_year is None else int(start_year)
    last = as_of.year - 1 if end_year is None else int(end_year)
    validate_year_window(first, last)
    if mode not in {"incremental", "history"}:
        raise Thailand506ContractError("Thailand collection mode must be incremental or history")
    if type(report_offset) is not int or report_offset < 0 or type(max_reports) is not int or not 1 <= max_reports <= 360:
        raise Thailand506ContractError("Expected a nonnegative report_offset and max_reports between 1 and 360")
    if type(recent_years) is not int or not 1 <= recent_years <= 10:
        raise Thailand506ContractError("recent_years must be between 1 and 10")
    links = {
        contract.code: discover_monthly_reports(histories[contract.code], disease_code=contract.code)
        for contract in contracts if contract.code in histories
    }
    advertised = sorted({year for mapping in links.values() for year in mapping if first <= year <= last}, reverse=True)
    selected_years = advertised[:recent_years] if mode == "incremental" else advertised
    plan = [
        {"disease_code": contract.code, "year": year, "url": links[contract.code][year]}
        for year in selected_years for contract in contracts
        if contract.code in links and year in links[contract.code]
    ]
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if resume_plan_hash is not None and resume_plan_hash != digest:
        raise Thailand506ContractError("History discovery changed; resume_plan_hash no longer identifies this report plan")
    if report_offset > len(plan):
        raise Thailand506ContractError("report_offset exceeds the discovered history plan")
    batch = plan[report_offset:report_offset + max_reports]
    end_offset = report_offset + len(batch)
    return {
        "mode": mode, "as_of": as_of.isoformat(), "plan_hash": digest, "report_offset": report_offset,
        "next_report_offset": end_offset if end_offset < len(plan) else None,
        "total_discovered_reports": len(plan), "remaining_reports": len(plan) - end_offset,
        "advertised_years": advertised, "selected_years": selected_years,
        "selected_reports": batch,
    }


def _rtf_lines(payload: bytes):
    # This parser intentionally accepts the source's tab-delimited RTF export,
    # rather than arbitrary RTF documents. Preserve empty tabs for unknowns.
    if not payload.startswith(b"{\\rtf1") or b"\\ansicpg874" not in payload[:100]:
        raise Thailand506ContractError("Expected Report 506 Windows-874 RTF bytes")
    if re.search(rb"\\(?:bin\d|object\b)", payload):
        raise Thailand506ContractError("Unsupported RTF text or binary control in the reviewed export")
    text = payload.decode("cp874", errors="strict")
    # Individual disease tables contain the Thai word รหัส (code) as RTF
    # Unicode escapes, each followed by one encoded fallback character. Decode
    # the Unicode and consume that fallback, rather than retaining '?' or
    # deleting the Thai text. The reviewed export uses the RTF default uc1.
    if any(int(value) != 1 for value in re.findall(r"\\uc(-?\d+)", text)):
        raise Thailand506ContractError("Unsupported RTF Unicode fallback length")

    def unicode_character(match):
        value = int(match[1])
        if not -32768 <= value <= 65535 or 0xD800 <= (value % 65536) <= 0xDFFF:
            raise Thailand506ContractError("Unsupported RTF Unicode code unit")
        return chr(value % 65536)

    text = re.sub(r"\\u(-?\d+)(?!\d)(?:\\'[0-9a-fA-F]{2}|[^\\{}])", unicode_character, text)
    if re.search(r"\\u-?\d", text):
        raise Thailand506ContractError("Unsupported RTF Unicode fallback encoding")
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda match: bytes.fromhex(match[1]).decode("cp874"), text)
    text = re.sub(r"\\tab\b ?", "\t", text)
    text = re.sub(r"\\par\b ?", "\n", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = text.translate(str.maketrans("", "", "{}"))
    return [[cell.strip() for cell in line.split("\t")[1:]] for line in text.splitlines() if "\t" in line]


def _count(value: str):
    if value in {"", "-", "*", "NA", "N/A"}:
        return None
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", value):
        raise Thailand506ContractError(f"Invalid monthly notification count: {value!r}")
    return int(value.replace(",", ""))


def parse_monthly_rtf(payload: bytes, *, disease_code: str, year: int, source_url: str):
    contract = CONTRACTS_BY_CODE[disease_code]
    lines = _rtf_lines(payload)
    heading_prefix = "Reported cases  and deaths by Province and by Month  Thailand"
    headings = []
    for cells in lines:
        if not cells or not cells[0].startswith("Reported cases"):
            continue
        if cells[0] != heading_prefix:
            # The 2003–2020 exporter keeps the Buddhist/Gregorian year inside
            # the first title cell. The later exporter places it in its own
            # tab cell. Both must still identify Thailand and the exact year.
            match = re.fullmatch(re.escape(heading_prefix) + r"\s+(\d{4})\s*\((\d{4})\)", cells[0])
            if not match:
                raise Thailand506ContractError("Disease, national geography, or report title changed")
            cells = [heading_prefix, f"{match[1]}({match[2]})", *cells[1:]]
        headings.append(cells)
    if not headings or any(len(cells) < 3 or cells[1] != f"{year + 543}({year})" for cells in headings):
        raise Thailand506ContractError(f"Disease, national geography, or Buddhist/Gregorian report year changed for {disease_code}/{year}")
    for cells in headings:
        if len(disease_code) == 2:
            actual_codes = [int(cells[-1])] if cells[-1].isdigit() else []
        expected_codes = [int(disease_code[i:i + 2]) for i in range(0, len(disease_code), 2)]
        if len(disease_code) > 2:
            # The official grouped headers use heterogeneous range notation;
            # STI's header contains no codes at all. Its exact reviewed label
            # and captured catalog/history link preserve this aggregate's own
            # identity without inventing a sum of its components.
            actual_codes = expected_codes if cells[2] == contract.report_label else []
        if actual_codes != expected_codes or cells[2] != headings[0][2]:
            raise Thailand506ContractError(f"Source disease code changed for {disease_code}/{year}")
    columns = ["Reporting areas", "Total", "Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec"]
    column_headers = [cells for cells in lines if cells and cells[0] == "Reporting areas"]
    if not column_headers or any(cells != columns for cells in column_headers):
        raise Thailand506ContractError("Report 506 monthly columns changed")
    measure_headers = [cells for cells in lines if cells and cells[0] == "cases"]
    if not measure_headers or any(
        len(cells) != 26 or cells[0] != "cases" or cells[1] not in {"death", "deaths"}
        or any(cells[i] != "case" or cells[i + 1] not in {"deat", "death"} for i in range(2, 26, 2))
        for cells in measure_headers
    ):
        raise Thailand506ContractError("Report 506 case/death metric order changed")
    totals = [cells for cells in lines if cells and cells[0] == "Total"]
    if len(totals) != 1 or len(totals[0]) != 27:
        raise Thailand506ContractError("Expected exactly one national Total row with 13 case/death pairs")
    footers = [cells for cells in lines if cells and cells[0].startswith("Center of Epidemiological Information,")]
    if len(footers) != 1 or len(footers[0]) != 2:
        raise Thailand506ContractError("Report update date is missing or ambiguous")
    try:
        day, month, buddhist_year = map(int, footers[0][1].split("/"))
        updated = date(buddhist_year - 543, month, day)
    except (ValueError, TypeError) as exc:
        raise Thailand506ContractError("Invalid Buddhist-calendar update date") from exc
    if updated <= date(year, 12, 31):
        raise Thailand506ContractError("Annual file was generated before the reporting year closed")
    values = [_count(value) for value in totals[0][1:]]
    for offset in (0, 1):
        months = values[2 + offset::2]
        if values[offset] is not None and all(value is not None for value in months) and sum(months) != values[offset]:
            raise Thailand506ContractError("Monthly counts do not reconcile to the published annual total")
    rows = []
    for month in range(1, 13):
        cases, deaths = values[2 * month:2 * month + 2]
        # Unknowns remain blank, explicit zero remains a reported observation.
        rows.append({
            "Date": date(year, month, 1).isoformat(), "Year": str(year), "Month": str(month),
            "RawDiseaseLabel": headings[0][2], "SourceDiseaseCode": disease_code,
            "DiseaseCode": "__source_native__", "Cases": "" if cases is None else str(cases),
            "Deaths": "" if deaths is None else str(deaths),
            "MissingValueStatus": "source_missing" if cases is None else "reported",
            "AnnualTotal": "" if values[0] is None else str(values[0]),
            "Frequency": "monthly", "Measure": "case_notifications", "Unit": "count",
            "ReportingArea": "TH", "GeographyKey": "country:TH:national",
            "ReportingBasis": "report_506_mostly_suspected_notifications",
            "TimeBasis": "source_report_month", "DatasetStatus": "closed_revisable",
            "IsProvisional": "false", "SourcePublishedAt": updated.isoformat(),
            "DefinitionVersion": CONTRACT_VERSION, "RevisionSemantics": "authoritative_revision",
            "AuthoritativeRevision": "true",
            "MissingValuePolicy": "missing_is_unknown", "Source": DEFAULT_SOURCE_NAME,
            "SourceScope": DEFAULT_SCOPE, "SourceURL": source_url,
            "SourceContract": CONTRACT_VERSION, "RawSHA256": hashlib.sha256(payload).hexdigest(),
            "PublicReleaseEnabled": "false", "LicenseReviewStatus": "unverified",
            "Dimensions": "{}", "SourceDimensions": json.dumps({"catalog_label": contract.label, "report_506_codes": [disease_code[i:i + 2] for i in range(0, len(disease_code), 2)], "national_aggregate_is_all_jurisdiction_complete": False}, sort_keys=True),
        })
    return rows


class ThailandDOE506Crawler(BaseCrawler):
    """HTTP transport used only by the durable collection stage."""

    def __init__(self):
        super().__init__(timeout=40, delay=0.25)

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use ingestion.countries.th.collect to archive source bytes")

    def parse(self, response):
        raise NotImplementedError("Use parse_monthly_rtf with explicit disease and year")
