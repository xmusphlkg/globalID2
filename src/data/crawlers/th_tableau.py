"""Anonymous DDS summary dashboard. Source labels and date/place bases stay separate."""
from __future__ import annotations

import hashlib
import json
import re

import requests
from tableauscraper import TableauScraper, api, dashboard, utils

DEFAULT_SCOPE = "th_dds_tableau"
DEFAULT_SOURCE_NAME = "Thailand DDS Tableau Weekly National Surveillance"
ONTOLOGY_SOURCE_ID = "SRC_TH_DDS_TABLEAU"
HOST = "https://dvis3.ddc.moph.go.th"
VIEW_URL = HOST + "/t/DDC_CENTER_DOE/views/DDS2/1_2"
VIZQL_ROOT = "/vizql/t/DDC_CENTER_DOE/w/DDS2/v/1_2"
TABLE = "ตารางลำดับผู้ป่วย รายพื้นที่"
BASIS_LABELS = {"report": "ตามวันและสถานที่รายงาน", "onset": "ตามวันและสถานที่เริ่มป่วย"}
SENTINEL = "เลือกทุกครั้งเพื่อแสดงอัตราป่วย"
REVIEWED_LABELS = ('Acute diarrhea', 'Acute hepatitis A Virus', 'Acute hepatitis B Virus', 'Acute hepatitis C Virus', 'Acute hepatitis D virus', 'Acute hepatitis E virus', 'Amoebic Dysentery, Amoebiasis', 'Anogenital Herpes', 'Anthrax', 'Botulism', 'Chancroid', 'Chikungunya fever', 'Condyloma Acuminata /Venereal Warts', 'Congenital rubella syndrome', 'Coronavirus disease 2019 (COVID-19),MISC from COVID-19', 'Dengue haemorrhagic fever (DHF)', 'Dengue shock syndrome (DSS)', 'Diphtheria', 'Encephalitis', 'Enterovirus Fever', 'Food poisoning', 'Gonorrhea', 'Hand foot and mouth disease', 'Japanese B encephalitis, J.E.', 'Legionnaires', 'Leishmaniasis', 'Leprosy', "Leptospirosis, Wei's disease", 'Liver fluke', 'Lymphatic Filariais', 'Lymphogranuloma Venereum/Granuloma Inguinale', 'Malaria', 'Melioidosis', 'Meningococcal meningitis', 'Mumps', 'Mushroom poisoning', 'Non-Gonococcal urethritis (NGU)', 'Paratyphoid fever', 'Pertussis', 'Pneumonia', 'Rabies, Hydrophobia', 'Respiratory Syncytial Virus (RSV)', 'Rubella,  Rubella without complication', 'Scarlet fever', 'Scrub Typhus', 'Shigellosis dysentery, Bacillary dysentery', 'Streptococcus suis infection', 'Tetanus', 'Trichinosis, Trichinellosis, Trichiniasis', 'Typhoid fever', 'Unspecified meningitis', 'Varicella, Chickenpox', 'Zika virus disease', 'Brucellosis ', 'Cholera   ', 'Dengue fever ', 'Fever of unknown origin (FUO)', 'Influenza, (Flu) ', 'Measles ', 'Measles with complication ', 'Neonatal Tetanus ', 'Poliomyelitis', 'Syphilis, Congenital syphilis ', 'Viral conjunctivitis')


class TableauContractError(ValueError):
    pass


def source_code(label):
    """Technical label identity, not a guessed numeric DDS code or disease mapping."""
    return "label_sha256_" + hashlib.sha256(label.encode()).hexdigest()[:20]


def decode_bootstrap(payload):
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    frames, offset = [], 0
    while offset < len(text):
        match = re.match(r"(\d+);", text[offset:])
        if not match:
            raise TableauContractError("Malformed Tableau bootstrap frame")
        start = offset + len(match[0])
        end = start + int(match[1])
        frames.append(json.loads(text[start:end]))
        offset = end
    if len(frames) != 2 or "sheetName" not in frames[0] or "secondaryInfo" not in frames[1]:
        raise TableauContractError("Unexpected Tableau bootstrap schema")
    return frames


def offline_workbook(payload):
    """Initialize the public summary model without network or live sessions."""
    scraper = TableauScraper()
    scraper.info, scraper.data = decode_bootstrap(payload)
    try:
        scraper.dataSegments = scraper.data["secondaryInfo"]["presModelMap"]["dataDictionary"][
            "presModelHolder"]["genDataDictionaryPresModel"]["dataSegments"]
    except KeyError as exc:
        raise TableauContractError("Tableau summary dictionary is missing") from exc
    scraper.parameters = utils.getParameterControlInput(scraper.info)
    scraper.dashboard = scraper.info["sheetName"]
    if scraper.dashboard != "รายงานสถานการณ์โรค1":
        raise TableauContractError("Unexpected national DDS dashboard")
    scraper.filters = utils.getFiltersForAllWorksheet(scraper.logger, scraper.data, scraper.info,
                                                     rootDashboard=scraper.dashboard)
    return scraper, scraper.getWorkbook()


def replay_response(scraper, workbook, payload):
    response = json.loads(payload)
    if "vqlCmdResponse" not in response or response.get("errors"):
        raise TableauContractError("Tableau command did not return summary models")
    pres_model = response["vqlCmdResponse"].get("layoutStatus", {}).get("applicationPresModel", {})
    if "workbookPresModel" not in pres_model:
        # Selecting an already active control may acknowledge without a visual
        # delta. Existing source values and selected tuples must still verify.
        return workbook
    workbook.updateFullData(response)
    return merge_workbooks(workbook, dashboard.getWorksheetsCmdResponse(scraper, response))


def merge_workbooks(previous, current):
    names = set(current.getWorksheetNames())
    current.worksheets = [*current.worksheets, *(sheet for sheet in previous.worksheets if sheet.name not in names)]
    return current


def filter_models(payload, *, worksheet=TABLE):
    """Read the source's selected tuples, rather than the library's domain list."""
    objects = decode_bootstrap(payload) if not payload.lstrip().startswith(b"{") else [json.loads(payload)]
    found = []
    def visit(node):
        if isinstance(node, dict):
            if node.get("worksheet") == worksheet:
                encoded = node.get("presModelHolder", {}).get("visual", {}).get("filtersJson")
                if encoded:
                    found.append({item["fieldCaption"]: item for item in json.loads(encoded)})
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(objects)
    if len(found) > 1 and any(item != found[0] for item in found[1:]):
        raise TableauContractError("Conflicting source filter models")
    return found[0] if found else {}


def card_filter_models(payload):
    return {name: filter_models(payload, worksheet=name) for name in ("ผู้ป่วย", "ผู้เสียชีวิต")}


def selected_values(model):
    if model.get("exclude"):
        raise TableauContractError("An exclusion filter cannot establish the requested category")
    if model.get("all") or model.get("rangeType") == "all":
        return "all"
    return [row["t"][0]["v"] for row in model.get("table", {}).get("tuples", []) if row.get("s") is True]


def inventory(workbook):
    parameters = {item["column"]: item for item in workbook.getParameters()}
    if "ปี" not in parameters:
        raise TableauContractError("Tableau year selector missing")
    years = [int(value) - 543 for value in parameters["ปี"]["values"]]
    filters = {item["column"]: item for item in workbook.getWorksheet(TABLE).getFilters()}
    if not {"โรค", "สัปดาห์", "วัน/สถานที่", "จังหวัด"} <= filters.keys():
        raise TableauContractError("Tableau national summary filter contract changed")
    labels = filters["โรค"]["values"]
    if labels.count(SENTINEL) != 1:
        raise TableauContractError("Expected the source's disease-selection sentinel")
    labels = [label for label in labels if label != SENTINEL]
    if len(labels) != len(set(labels)) or any(not isinstance(label, str) or not label.strip() or len(label) > 240 for label in labels):
        raise TableauContractError("Invalid or duplicate native disease selector label")
    if set(filters["วัน/สถานที่"]["values"]) != set(BASIS_LABELS.values()):
        raise TableauContractError("Tableau date/place basis inventory changed")
    provinces = filters["จังหวัด"]["values"]
    if len(provinces) != 77 or len(set(provinces)) != 77:
        raise TableauContractError("Unexpected source province inventory")
    weeks = [int(value) for value in filters["สัปดาห์"]["values"] if value != SENTINEL]
    if sorted(weeks) != list(range(1, 54)):
        raise TableauContractError("Unexpected Tableau weekly selector")
    return {"years": years, "labels": labels, "provinces": provinces, "weeks": weeks,
            "new_labels": sorted(set(labels) - set(REVIEWED_LABELS)),
            "missing_reviewed_labels": sorted(set(REVIEWED_LABELS) - set(labels))}


def table_records(workbook, name):
    if name not in workbook.getWorksheetNames():
        raise TableauContractError(f"Required source summary is absent: {name}")
    return workbook.getWorksheet(name).data.to_dict("records")


def scalar(workbook, name, column):
    records = table_records(workbook, name)
    if len(records) != 1 or set(records[0]) != {column}:
        raise TableauContractError(f"Expected one explicit source scalar: {name}")
    return records[0][column]


def count(value):
    text = str(value).replace(",", "").strip()
    if not re.fullmatch(r"\d+", text):
        raise TableauContractError("National notification count is missing or not an integer")
    return int(text)


def national_snapshot(workbook, *, year, basis, week, provinces, label, filters, card_filters):
    """Read explicit cards; province/unknown sums only check their consistency."""
    if basis not in BASIS_LABELS:
        raise TableauContractError("Unknown Tableau date/place basis")
    for field, value in (("โรค", label), ("สัปดาห์", str(week)), ("วัน/สถานที่", BASIS_LABELS[basis])):
        if field not in filters or selected_values(filters[field]) != [value]:
            raise TableauContractError(f"Source selected tuples do not establish {field}={value}")
        for card in ("ผู้ป่วย", "ผู้เสียชีวิต"):
            model = card_filters.get(card, {}).get(field, {})
            if (not filters[field].get("name") or model.get("name") != filters[field]["name"]
                or model.get("modified") is not True or card not in model.get("targetSheets", [])
                or card not in filters[field].get("targetSheets", [])):
                raise TableauContractError("Selected source filter is not linked to both national cards")
    for field in ("จังหวัด", "เขตสุขภาพ", "กลุ่มอายุ", "อำเภอ", "กลุ่มโรค", "สัญชาติ", "สัดส่วนแผนกการรักษา"):
        model = filters.get(field)
        if model is None or not (model.get("all") or model.get("allChecked") or model.get("rangeType") == "all"):
            raise TableauContractError(f"National source summary has a restricted dimension: {field}")
    if str(scalar(workbook, "ปี", "ปี-alias")) != str(year + 543):
        raise TableauContractError("Source year does not match requested year")
    cases = count(scalar(workbook, "ผู้ป่วย", "SUM(ปี พ.ศ.)-alias"))
    deaths = count(scalar(workbook, "ผู้เสียชีวิต", "SUM(ปี พ.ศ. ผู้เสียชีวิต)-alias"))
    time = table_records(workbook, "type time")
    if time and time != [{"วัน/สถานที่-value": BASIS_LABELS[basis], "วัน/สถานที่-alias": BASIS_LABELS[basis]}]:
        raise TableauContractError("Source date/place basis does not match selected basis")
    records = table_records(workbook, TABLE)
    sums, unknown = {}, {}
    for metric, target in (("จำนวนผู้ป่วย", cases), ("จำนวนผู้เสียชีวิต", deaths)):
        rows = [row for row in records if str(row["Measure Names-alias"]).strip() == metric]
        geos = [str(row["จังหวัด-value"]) for row in rows]
        if len(geos) != len(set(geos)) or set(geos) - set(provinces) - {"ไม่ระบุ"}:
            raise TableauContractError("Invalid or duplicate province/unknown summary")
        values = {}
        for row in rows:
            value = row["Measure Values-alias"]
            if target == 0 and (value is None or str(value).strip() == ""):
                continue  # card explicitly reports zero; an empty province cell stays unknown
            values[str(row["จังหวัด-value"])] = count(value)
        if (target != 0 and not rows) or sum(values.values()) != target:
            raise TableauContractError("Explicit national card differs from province plus unknown sum")
        sums[metric], unknown[metric] = sum(values.values()), values.get("ไม่ระบุ")
    return {"cases": cases, "deaths": deaths, "week": int(week), "unknown_cases": unknown["จำนวนผู้ป่วย"],
            "unknown_deaths": unknown["จำนวนผู้เสียชีวิต"], "national_evidence": "explicit_national_cards"}


class TimedSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (15, 120))
        response = super().request(method, url, **kwargs)
        response.raise_for_status()
        self.last_response = response
        return response


class ThailandDDSTableauCrawler:
    """Use the same anonymous session/bootstrap and summary controls as the public UI."""
    def __init__(self):
        self.session = TimedSession()
        self.scraper = None
        self.workbook = None
        self.entries = []
        self.source_filters = {}

    def get(self, url):
        return self.session.get(url)

    def save(self, payload, *, operation, **metadata):
        self.entries.append({"name": f"{len(self.entries):04d}_{operation}.json", "payload": payload,
                             "metadata": {"operation": operation, **metadata}})

    def load(self):
        response = self.session.post(HOST + VIZQL_ROOT + "/startSession/viewing",
                                     params={":embed": "y", ":showVizHome": "no"})
        config = response.json()
        if config.get("is_guest") is not True or config.get("allow_summary") is not True:
            raise TableauContractError("Expected publicly permitted guest summary access")
        # Session identifiers are transient controls, not source provenance.
        public_config = {key: config.get(key) for key in ("is_guest", "allow_summary", "allow_view_underlying",
            "visible_sheets", "repository_urls", "workbook_repo_url", "current_workbook_id", "current_view_id")}
        self.save(json.dumps(public_config, ensure_ascii=False).encode(), operation="public_config")
        response = self.session.post(HOST + VIZQL_ROOT + "/bootstrapSession/sessions/" + config["sessionid"],
            data={"sheet_id": config["sheetId"], "clientDimension": json.dumps({"w": 1920, "h": 1080})})
        self.save(response.content, operation="bootstrap")
        self.scraper, self.workbook = offline_workbook(response.content)
        self.source_filters = filter_models(response.content)
        self.scraper.host, self.scraper.session, self.scraper.tableauData = HOST, self.session, config
        return inventory(self.workbook)

    def select(self, field, value, *, parameter=False, **metadata):
        if parameter:
            parameters = [item for item in self.workbook.getParameters() if item["column"] == field]
            if len(parameters) != 1 or str(value) not in parameters[0]["values"]:
                raise TableauContractError("Requested year is not a public parameter value")
            api.setParameterValue(self.scraper, parameters[0]["parameterName"], str(value))
        else:
            controls = [item for item in self.workbook.getWorksheet(TABLE).getFilters() if item["column"] == field]
            if len(controls) != 1:
                raise TableauContractError("Requested source filter is unavailable")
            control = controls[0]
            values = value if isinstance(value, list) else [value]
            indices = [control["values"].index(item) for item in values]
            api.filter(self.scraper, worksheetName=TABLE, globalFieldName=control["globalFieldName"],
                       dashboard=self.scraper.dashboard, selection=indices)
        raw = self.session.last_response.content
        if "vqlCmdResponse" not in self.session.last_response.json():
            raise TableauContractError(f"Source did not acknowledge {field} selection")
        self.source_filters.update(filter_models(raw))
        self.workbook = replay_response(self.scraper, self.workbook, raw)
        self.save(self.session.last_response.content, operation="parameter" if parameter else "filter",
                  field=field, value=value, **metadata)
        if parameter:
            if str(scalar(self.workbook, "ปี", "ปี-alias")) != str(value):
                raise TableauContractError("Source did not acknowledge the requested year parameter")
        elif selected_values(self.source_filters.get(field, {})) != ([value] if not isinstance(value, list) else value):
            raise TableauContractError(f"Source did not acknowledge the exact {field} values")
        return self.workbook

    def close(self):
        self.session.close()


def build_row(snapshot, *, label, year, week, basis, calendar, as_of, raw_sha256, retrieved_at):
    if not isinstance(label, str) or not label.strip() or label == SENTINEL or basis not in BASIS_LABELS or snapshot.get("national_evidence") != "explicit_national_cards":
        raise TableauContractError("Unreviewed category, basis or national evidence")
    try:
        start, end = calendar[int(week)]
    except KeyError as exc:
        raise TableauContractError("Verified official source calendar is missing") from exc
    if start.year != year or end.year != year or end >= as_of:
        raise TableauContractError("Open/future weeks are not accepted as notifications")
    return {"Date": end.isoformat(), "PeriodStart": start.isoformat(), "PeriodEnd": end.isoformat(),
        "Year": str(year), "EpiWeek": str(week), "Frequency": "weekly", "RawDiseaseLabel": label,
        "SourceDiseaseCode": source_code(label), "Cases": str(snapshot["cases"]), "Deaths": str(snapshot["deaths"]),
        "Measure": "case_notifications", "Unit": "count", "MissingValueStatus": "reported",
        "GeographyKey": "country:TH:national", "Geography": "Thailand",
        "DefinitionVersion": f"th_dds_tableau_single_week_{basis}_v1", "ReportingBasis": f"dds_notification_{basis}_date_and_place",
        "TimeBasis": basis, "Source": DEFAULT_SOURCE_NAME, "SourceScope": DEFAULT_SCOPE, "SourceURL": VIEW_URL,
        "RetrievedAt": retrieved_at, "DatasetStatus": "revisable", "RevisionSemantics": "authoritative_revision",
        "AuthoritativeRevision": "true", "RawSHA256": raw_sha256, "PublicReleaseEnabled": "false",
        "NationalEvidence": "explicit_national_cards", "SourceDiseaseCodeEvidence": "derived_label_identity_no_numeric_code",
        "UnknownLocationCases": "" if snapshot["unknown_cases"] is None else str(snapshot["unknown_cases"]),
        "UnknownLocationDeaths": "" if snapshot["unknown_deaths"] is None else str(snapshot["unknown_deaths"]),
        "CaseDefinition": "Public DDS Tableau national notification summary; native combined categories and selected date/place basis retained.",
        "Dimensions": json.dumps({"date_place_basis": basis}, sort_keys=True)}
