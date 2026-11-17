"""PHAC national weekly respiratory detections, tests and positivity, separately."""
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import io
import json

from .base import BaseCrawler

DEFAULT_SCOPE = "ca_phac_respiratory_weekly"
ONTOLOGY_SOURCE_ID = "SRC_CA_PHAC_RESPIRATORY"
LANDING_URL = "https://health-infobase.canada.ca/respiratory-virus-surveillance/"
SCRIPT_URL = "https://health-infobase.canada.ca/src/js/respiratory-virus-surveillance/rvdss-figure1.js"
DATA_URL = "https://health-infobase.canada.ca/src/data/respiratory-virus-detections/RVD_WeeklyData.csv"
VIRUSES = {"SARS-CoV-2": "sars_cov_2", "Influenza": "influenza", "Influenza A": "influenza_a",
           "Influenza B": "influenza_b", "RSV": "rsv", "ADV": "adenovirus",
           "EV/RV": "enterovirus_rhinovirus", "HCoV": "human_coronavirus",
           "HMPV": "human_metapneumovirus", "HPIV": "human_parainfluenza"}
FIELDS = {"region", "province", "week", "weekorder", "year", "date", "virus",
          "tests", "detections", "percentpositive"}


def parse_value(value, *, percent=False):
    if value in {"", "N/A", "NA"}:
        return "", "source_missing"
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Unreviewed PHAC numerical notation") from None
    if not number.is_finite() or number < 0 or (percent and number > 100) or (not percent and number != int(number)):
        raise ValueError("Invalid PHAC detection/test count or positivity")
    return str(number) if percent else str(int(number)), "reported"


def parse_csv(payload, *, provenance=None, as_of=None):
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    if set(reader.fieldnames or ()) != FIELDS:
        raise ValueError("PHAC weekly CSV schema changed")
    today = as_of or datetime.now(timezone.utc).date()
    rows, seen = [], set()
    audit = {"source_records": 0, "excluded_subnational_records": 0, "withheld_open_week_records": 0,
             "coverage_complete": False, "coverage_definition": "all explicit Canada rows in current rolling CSV; historical archive not implied"}
    for index, raw in enumerate(reader):
        audit["source_records"] += 1
        if raw["province"] != "Canada":
            audit["excluded_subnational_records"] += 1
            continue
        if raw["region"] != "Canada" or raw["virus"] not in VIRUSES:
            raise ValueError("PHAC national geography/virus contract changed")
        end = date.fromisoformat(raw["date"])
        year, week = int(raw["year"]), int(raw["week"])
        # Preserve the explicit PHAC epidemiological week; do not call it ISO.
        if end.weekday() != 5 or year != end.year or not 1 <= week <= 53:
            raise ValueError("PHAC source year/week or Saturday ending date invalid")
        identity = (end, raw["virus"])
        if identity in seen:
            raise ValueError("Duplicate PHAC Canada week/virus")
        seen.add(identity)
        if end >= today:
            audit["withheld_open_week_records"] += 1
            continue
        for field, measure, unit in (("detections", "laboratory_detections", "count"),
                                     ("tests", "laboratory_tests", "count"),
                                     ("percentpositive", "laboratory_positivity", "percent")):
            value, status = parse_value(raw[field], percent=field == "percentpositive")
            rows.append({
                **(provenance or {}),
                "Date": end.isoformat(), "Year": str(end.year), "Month": str(end.month),
                "PeriodStart": (end - timedelta(days=6)).isoformat(), "PeriodEnd": end.isoformat(),
                "SourceYear": str(year), "SourceWeek": str(week), "SourceWeekOrder": raw["weekorder"],
                "Calendar": "PHAC_source_epiweek", "TimeBasis": "source_week_ending_saturday",
                "SourceDiseaseCode": VIRUSES[raw["virus"]] + "__" + field,
                "DiseaseCode": "__source_native__", "RawDiseaseLabel": raw["virus"] + " " + field,
                "Cases": value, "MissingValueStatus": status, "SourceReportedValue": raw[field],
                "Measure": measure, "Unit": unit, "Frequency": "weekly", "PeriodType": "weekly",
                "ReportingBasis": "phac_reporting_laboratory_network", "PopulationScope": "reporting_laboratory_network_not_population_cases",
                "DefinitionVersion": "ca_phac_respiratory_weekly_v1", "Geography": "Canada reporting laboratory network",
                "GeographyKey": "country:CA:national", "Source": "Canada PHAC Weekly Respiratory Virus Detections",
                "SourceURL": LANDING_URL, "SourceRecordIndex": index,
                "SourceTests": raw["tests"], "SourceDetections": raw["detections"],
                "SourcePercentPositive": raw["percentpositive"], "SourceVirus": raw["virus"],
                "Dimensions": json.dumps({"virus": raw["virus"], "metric": field}, sort_keys=True),
                "DatasetStatus": "reported_revisable", "IsProvisional": "true",
                "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
                "PublicReleaseEnabled": "true", "AggregationPolicy": "non_additive",
            })
    if not rows:
        raise ValueError("PHAC CSV has no closed-week Canada observations")
    audit.update(observations=len(rows), numeric_observations=sum(r["MissingValueStatus"] == "reported" for r in rows))
    return rows, audit


class CARespiratoryWeeklyCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=90, delay=0.1)

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use staged PHAC artifact capture")

    def parse(self, response):
        raise NotImplementedError("PHAC parsing requires captured source provenance")
