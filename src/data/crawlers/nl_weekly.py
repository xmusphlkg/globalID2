"""Lossless RIVM weekly laboratory and respiratory source contracts."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from zoneinfo import ZoneInfo

from .base import BaseCrawler, CrawlerResult

VIRO_SCOPE = "rivm_virological_weekly"
RESP_SCOPE = "rivm_respiratory_weekly"
VIRO_SOURCE_ID = "SRC_NL_RIVM_VIROLOGICAL"
RESP_SOURCE_ID = "SRC_NL_RIVM_RESPIRATORY"
VIRO_NAME = "Netherlands RIVM Virological Weekly Surveillance"
RESP_NAME = "Netherlands RIVM Respiratory Weekly Surveillance"
VIRO_URL = "https://www.rivm.nl/sites/default/files/2019-01/RecenteVirUitslagen27w.csv"
VIRO_LANDING = "https://www.rivm.nl/virologische-weekstaten"
RESP_LANDING = "https://www.rivm.nl/luchtweginfecties/actueel"
RESP_API = "https://data.rivm.nl/opendata/api/3/action/package_show?id=res-ari-surveillance"


class NLWeeklyContractError(ValueError):
    """An upstream value, unit, identity or temporal contract is unsupported."""


def source_code(resource: str, label: str) -> str:
    return "rivm_" + hashlib.sha256(f"{resource}|{label}".encode()).hexdigest()[:24]


def number(value: str, *, integer=False, percentage=False) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise NLWeeklyContractError(f"Invalid numeric source value: {value!r}") from exc
    if not parsed.is_finite() or parsed < 0 or (integer and parsed != parsed.to_integral_value()):
        raise NLWeeklyContractError(f"Invalid nonnegative {'integer' if integer else 'number'}: {value!r}")
    if percentage and parsed > 100:
        raise NLWeeklyContractError(f"Percentage exceeds 100: {value!r}")
    return str(parsed)


def _row(*, label, resource, value, year, week, source_name, source_url,
         digest, metric, unit, population, definition, period, report_date="", **extra):
    dims = {"source_week": week, **period}
    return {
        # This is deliberately NOT a reconstructed weekly observation date.
        "Date": f"{year}-01-01", "Year": str(year), "Cases": "" if value is None else value,
        "MissingValueStatus": "source_missing" if value is None else "reported",
        "RawDiseaseLabel": label, "SourceDiseaseCode": source_code(resource, label),
        "Source": source_name, "SourceURL": source_url,
        "SourceResource": resource, "SourceWeek": str(week),
        "Frequency": "weekly", "Measure": metric, "Unit": unit,
        "GeographyKey": "country:NL:national", "Geography": "Netherlands",
        "ReportingBasis": "participating_surveillance_reporting",
        "PopulationScope": population, "DefinitionVersion": definition,
        "CalendarStatus": "unverified_week_boundary_calendar",
        "DateRole": "source_period_year_storage_anchor_only",
        "TimeBasis": "explicit_source_year_week_or_season_week",
        "PeriodStart": "", "PeriodEnd": "", "SourceReportDate": report_date,
        "RawSHA256": digest, "PublicReleaseEnabled": "false",
        "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
        "Dimensions": json.dumps(dims, sort_keys=True), **extra,
    }


def parse_virological_csv(payload: bytes, *, as_of: date | None = None):
    """Keep every source category and original year/week; do not ISO-guess dates."""
    as_of = as_of or datetime.now(ZoneInfo("Europe/Amsterdam")).date()
    records = list(csv.reader(io.StringIO(payload.decode("utf-8-sig")), delimiter=";"))
    if not records or records[0] != ["Virologische Weekstaten afgelopen 27 weken"]:
        raise NLWeeklyContractError("Missing virological weekly source title")
    reports = [r for r in records if r and r[0] == "Rapportage datum:"]
    if len(reports) != 1 or len(reports[0]) != 2:
        raise NLWeeklyContractError("Exactly one source report date is required")
    reported = date.fromisoformat(reports[0][1])
    if reported > as_of:
        raise NLWeeklyContractError("Source report date lies in the future")
    headers = [r for r in records if r and r[0] == "JaarWeek"]
    if len(headers) != 1 or len(headers[0]) != 28:
        raise NLWeeklyContractError("Exactly 27 explicit source year/week columns are required")
    header = headers[0]
    if len(set(header[1:])) != 27 or not all(re.fullmatch(r"\d{4}(0[1-9]|[1-4]\d|5[0-3])", w) for w in header[1:]):
        raise NLWeeklyContractError("Invalid or duplicate source year/week")
    periods = [(int(w[:4]), int(w[4:])) for w in header[1:]]
    for (year, week), (next_year, next_week) in pairwise(periods):
        # This validates source sequence only, without choosing a week calendar.
        consecutive = (year == next_year and next_week == week - 1) or (
            week == 1 and next_year == year - 1 and next_week in {52, 53})
        if not consecutive:
            raise NLWeeklyContractError("Source year/weeks must be ordered and consecutive")
    labs = [r for r in records if r and r[0] == "AantalLabs"]
    if len(labs) != 1 or len(labs[0]) != len(header):
        raise NLWeeklyContractError("Reporting laboratory denominators are absent or truncated")
    denominators = [number(v, integer=True) for v in labs[0][1:]]
    if any(v is None for v in denominators):
        raise NLWeeklyContractError("Reporting laboratory count cannot be blank")
    digest = hashlib.sha256(payload).hexdigest()
    rows, seen, missing = [], set(), 0
    started = False
    for record in records:
        if record == header:
            started = True
            continue
        if not started or not record or not any(record) or record[0] == "AantalLabs":
            continue
        if len(record) != len(header) or not record[0].strip() or record[0] in seen:
            raise NLWeeklyContractError("Truncated, anonymous or duplicate pathogen row")
        label = record[0]
        seen.add(label)
        for yw, cell, count in zip(header[1:], record[1:], denominators):
            year, week = int(yw[:4]), int(yw[4:])
            if year > reported.year or year < 2000:
                raise NLWeeklyContractError("Source year outside report coverage")
            value = number(cell, integer=True)
            if value is None:
                missing += 1
            if value is not None and Decimal(count) == 0 and Decimal(value) > 0:
                raise NLWeeklyContractError("Positive detections with zero reporting laboratories")
            rows.append(_row(label=label, resource="virological_weekly_returns", value=value,
                year=year, week=week, source_name=VIRO_NAME, source_url=VIRO_URL,
                digest=digest, metric="laboratory_detections", unit="count",
                population="participating_laboratories", definition="nl_rivm_virological_returns_v1",
                period={"source_year": year}, report_date=reported.isoformat(),
                SourceYear=str(year), SourceYearWeek=yw, ReportingLaboratories=count,
                CaseDefinition="Weekly detected infections reported by participating laboratories; not national notification incidence."))
    if not rows:
        raise NLWeeklyContractError("No virological source categories")
    return rows, {"source_categories": len(seen), "source_weeks": header[1:],
        "report_date": reported.isoformat(), "observations": len(rows),
        "source_blank_cells": missing, "dates_inferred": False,
        "calendar_status": "source_year_week_retained_boundaries_unverified"}


# Each resource is a separate measure/population contract, even for the same pathogen.
RESP_PROFILES = {
    "tab_ir_sympt_ari_season_week_website": ("Acute respiratory symptoms", "participant_symptom_percentage", "percent", "infectieradar_participants", "config_ir_sympt_ari_line"),
    "tab_ir_test_pos_season_week_website": ("SARS-CoV-2 positive self-test", "participant_positive_selftest_percentage", "percent", "infectieradar_participants", "config_ir_test_pos_season_week_line"),
    "tab_nice_sari_weekly_admissions_website": ("Acute respiratory infection ICU admissions", "icu_admissions", "count", "participating_NICE_ICUs_excluding_PICU", "config_sari_weekly_admissions_line"),
    "tab_nps_zorg_ari_inc_website": ("Acute respiratory infection primary-care consultations", "consultation_incidence", "per_100000", "nivel_registered_primary_care_population", "config_nps_zorg_ari_line"),
    "tab_nps_zorg_bronch_inc_website": ("Bronchiolitis primary-care consultations age 0–4", "consultation_incidence", "per_100000", "nivel_primary_care_age_0_to_4", "config_nps_zorg_bronch_line"),
    "tab_nps_zorg_pneu_inc_website": ("Pneumonia primary-care consultations", "consultation_incidence", "per_100000", "nivel_registered_primary_care_population", "config_nps_zorg_pneu_line"),
    "tab_rsv_picu_website": ("RSV pediatric ICU admissions", "pediatric_icu_admissions", "count", "participating_PICUs_UMC_Utrecht_collection", "config_picu_rsv_line"),
    "tab_rio_wk_5_seasons_website": ("SARS-CoV-2 wastewater concentration", "wastewater_viral_load", "100billion_particles_per_100000", "national_wastewater_surveillance", "config_rio_wk_line"),
    "tab_iles_virological_results_perc_sarscov_website": ("SARS-CoV-2 specimen positivity", "positive_specimen_percentage", "percent", "infectieradar_submitted_specimens", "config_ir_sarscov_line"),
}
for _suffix, _pathogen in (("influenza_a", "Influenza A"), ("influenza_b", "Influenza B"), ("rsv", "RSV"), ("sars_cov_2", "SARS-CoV-2")):
    for _kind, _measure, _unit in (("det", "laboratory_detections", "count"), ("perc", "positive_specimen_percentage", "percent")):
        RESP_PROFILES[f"tab_vw_season_{_kind}_{_suffix}_website"] = (
            _pathogen, _measure, _unit, "NWKV_participating_laboratory_specimens",
            f"config_vw_season_{_kind}_{_suffix}_line")


def parse_respiratory_resource(payload: bytes, *, resource: dict, config: dict,
                               source_url: str, metadata_modified: str):
    name = resource["name"]
    if name not in RESP_PROFILES:
        raise NLWeeklyContractError(f"Unsupported respiratory resource: {name}")
    label, metric, unit, population, _ = RESP_PROFILES[name]
    if not re.search(r"(?:\?|&)resource_id=" + re.escape(resource["id"]) + r"(?:&|$)", str(config.get("dataSrc", ""))):
        raise NLWeeklyContractError("Chart evidence does not reference the captured CSV resource")
    if config.get("categoryStart") != 40 or config.get("groupKey") != ["season"]:
        raise NLWeeklyContractError("Expected explicit source season/week chart contract")
    axis = json.dumps(config.get("yAxis", {}), ensure_ascii=False).casefold()
    expected_units = {"percent": "percentage", "count": "aantal",
        "per_100000": "per 100.000", "100billion_particles_per_100000": "100 miljard"}
    if expected_units[unit] not in axis:
        raise NLWeeklyContractError("Respiratory chart unit changed")
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), delimiter=";")
    if not reader.fieldnames or set(reader.fieldnames) != {"season", "week", "value"} or len(reader.fieldnames) != 3:
        raise NLWeeklyContractError("Seasonal CSV must contain exactly season, week and value")
    rows, seen, blank = [], set(), 0
    digest = hashlib.sha256(payload).hexdigest()
    for item in reader:
        if None in item or any(v is None for v in item.values()):
            raise NLWeeklyContractError("Truncated or oversized seasonal CSV row")
        season = item["season"]
        match = re.fullmatch(r"(20\d{2})/(20\d{2})", season)
        if not match or int(match[2]) != int(match[1]) + 1:
            raise NLWeeklyContractError(f"Invalid explicit source season: {season!r}")
        if not re.fullmatch(r"[1-9]|[1-4]\d|5[0-3]", item["week"]):
            raise NLWeeklyContractError("Invalid source season week")
        week = int(item["week"])
        if (season, week) in seen:
            raise NLWeeklyContractError("Duplicate season/week identity")
        seen.add((season, week))
        value = number(item["value"], integer=unit == "count", percentage=unit == "percent")
        if value is None:
            blank += 1
        # The first year is an anchor derived from the source season string;
        # no claim is made that it is the observation's calendar year.
        anchor_year = int(match[1])
        if anchor_year > date.fromisoformat(metadata_modified[:10]).year:
            raise NLWeeklyContractError("Observation belongs to a future source season")
        definition = "nl_rivm_resp_" + hashlib.sha256(name.encode()).hexdigest()[:16] + "_v1"
        if name == "tab_nps_zorg_ari_inc_website":
            definition += "_incident_method_20260902"
        rows.append(_row(label=label, resource=name, value=value, year=anchor_year,
            week=week, source_name=RESP_NAME, source_url=source_url, digest=digest,
            metric=metric, unit=unit, population=population, definition=definition,
            period={"source_season": season}, SourceSeason=season,
            SourceResourceID=resource["id"], SourceMetadataModified=metadata_modified,
            SourceChartTitle=json.dumps(config.get("title"), ensure_ascii=False),
            SourceYAxis=json.dumps(config.get("yAxis"), ensure_ascii=False),
            CaseDefinition=f"RIVM published {name}; distinct population and measure; source chart title/units retained.",
            DateRole="source_season_first_year_storage_anchor_only"))
    if not rows:
        raise NLWeeklyContractError(f"No explicit seasonal observations: {name}")
    return rows, {"resource": name, "observations": len(rows), "blank_cells": blank,
        "seasons": sorted({s for s, _ in seen}), "dates_inferred": False}


class NetherlandsWeeklyCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=60, delay=0.1)

    def parse(self, response):
        rows, audit = parse_virological_csv(response.content)
        return [CrawlerResult(title=VIRO_NAME, url=VIRO_URL,
            raw_data={"rows": rows, "source_audit": audit})]

    async def crawl(self, **kwargs):
        import asyncio
        if kwargs.get("source", VIRO_SCOPE) != VIRO_SCOPE:
            raise ValueError("Respiratory resources require the durable staged connector")
        return self.parse(await asyncio.to_thread(self.get, VIRO_URL))
