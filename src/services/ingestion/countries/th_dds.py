"""Public situation forms provide each disease's explicitly offered weekly report."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
import json
import re
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import hashlib

from bs4 import BeautifulSoup
import requests

from src.data.crawlers.th_dds import (
    BASE_URL, CALENDAR_INDEX_URL, DEFAULT_SCOPE, DDSContractError, DDSDisease, FORM_URL, NATION_URL,
    STORE_URL, ThailandDDSCrawler, closed_single_weeks, disease_groups, diseases,
    options, parse_calendar_workbook, extract_calendar_workbook, parse_weekly_pdf, sanitize_form,
)
from src.data.processors.th_dds import DDSFetchResult
from src.services.ingestion.artifacts import read_artifact


def normalize_source(source):
    if str(source).strip().casefold() not in {DEFAULT_SCOPE, "all", "th", "thailand", "dds_weekly", "dds"}:
        raise ValueError(f"Unsupported DDS weekly source: {source!r}")


async def capture_calendar(writer, crawler, *, year):
    """Discover a matching annual calendar; missing years are explicit failures."""
    response = await asyncio.to_thread(crawler.get, CALENDAR_INDEX_URL)
    await writer.write(sanitize_form(response.content), source_url=CALENDAR_INDEX_URL,
        kind="html_report", metadata={"endpoint": "calendar_index", "year": year})
    urls = set()
    for link in BeautifulSoup(response.content, "html.parser").select("a[href]"):
        url = urljoin(CALENDAR_INDEX_URL, link["href"])
        match = re.fullmatch(re.escape(CALENDAR_INDEX_URL) + r"download/rar/outbreak_week_y(\d{2})", url)
        if match and int(match[1]) + 2500 - 543 == year:
            urls.add(url)
    if len(urls) != 1:
        raise DDSContractError(f"Official DDS portal has no unique calendar for {year}; publication check stopped without inferring periods")
    url = urls.pop()
    response = await asyncio.to_thread(crawler.get, url)
    raw = response.content
    await writer.write(raw, source_url=url, kind="dds_calendar_rar",
        metadata={"endpoint": "calendar_rar", "year": year})
    workbook = await asyncio.to_thread(extract_calendar_workbook, raw)
    calendar = parse_calendar_workbook(workbook, year=year)
    await writer.write(workbook, source_url=url, kind="dds_calendar_xlsx",
        metadata={"endpoint": "calendar_workbook", "year": year,
                  "parent_raw_sha256": hashlib.sha256(raw).hexdigest()})
    return calendar


async def collect(writer, *, source=DEFAULT_SCOPE, basis="onset", weeks=None, year=None,
                  as_of=None, refresh_state=None, **options_):
    normalize_source(source)
    if basis not in {"onset", "gen"}:
        raise DDSContractError("DDS date/place basis must be onset or gen")
    today = as_of or datetime.now(ZoneInfo("Asia/Bangkok")).date()
    if isinstance(today, str):
        today = date.fromisoformat(today)
    year = int(year or today.year)
    crawler = ThailandDDSCrawler()
    errors, reports, catalog = [], [], {}
    retrieved_at = datetime.now(ZoneInfo("Asia/Bangkok")).isoformat()
    try:
        try:
            if refresh_state is None:
                calendars = {year: await capture_calendar(writer, crawler, year=year)}
                refresh = None
            else:
                from .th_refresh import RefreshPlan, calendars_for_refresh
                calendars = await calendars_for_refresh(writer, crawler, refresh_state,
                    as_of=today, capture_calendar=capture_calendar)
                refresh = RefreshPlan(calendars, refresh_state, as_of=today)
        except (requests.RequestException, DDSContractError) as exc:
            await writer.write(json.dumps({"status": "failed", "year": year,
                "as_of": today.isoformat(), "error": str(exc), "dates_inferred": False},
                ensure_ascii=False).encode(), source_url=CALENDAR_INDEX_URL,
                kind="archive_json", metadata={"endpoint": "calendar_failure_audit", "year": year})
            raise DDSContractError(f"DDS official calendar unavailable for {year}: {exc}") from exc
        response = await asyncio.to_thread(crawler.get, FORM_URL)
        form = BeautifulSoup(response.content, "html.parser")
        token_node = form.select_one('input[name="_token"]')
        if token_node is None or not token_node.get("value"):
            raise DDSContractError("Normal public form CSRF token is missing")
        token = token_node["value"]
        await writer.write(sanitize_form(response.content), source_url=FORM_URL,
            kind="html_report", metadata={"endpoint": "situation_form"})
        response = await asyncio.to_thread(crawler.get, NATION_URL)
        await writer.write(sanitize_form(response.content), source_url=NATION_URL,
            kind="html_report", metadata={"endpoint": "national_form"})
        groups = disease_groups(response.content)
        # All real groups must be enumerated. The form's value='all' endpoint
        # returns an empty placeholder and is not an all-disease inventory.
        for group, _ in groups:
            url = BASE_URL + f"situation/nation/dzg/{group}"
            response = await asyncio.to_thread(crawler.get, url)
            await writer.write(response.content, source_url=url, kind="html_report",
                metadata={"endpoint": "disease_group", "group": group})
            for disease in diseases(response.content, group):
                if disease.code in catalog:
                    raise DDSContractError(f"Duplicate disease code across DDS groups: {disease.code}")
                catalog[disease.code] = disease
        if not catalog:
            raise DDSContractError("DDS public disease inventory is empty")
        for code, disease in sorted(catalog.items()):
            try:
                url = BASE_URL + f"situation/nation/dz/{code}"
                response = await asyncio.to_thread(crawler.get, url)
                await writer.write(response.content, source_url=url, kind="html_report",
                    metadata={"endpoint": "disease_years", "disease_code": code})
                source_years = {int(value) for value, _ in options(response.content) if value.isdigit()}
                eligible = []
                for selected_year, calendar in calendars.items():
                    if selected_year not in source_years:
                        if refresh is None:
                            raise DDSContractError("Requested year is absent from this disease's public form")
                        continue
                    url = BASE_URL + f"situation/nation/dz/{code}/year/{selected_year}/type/{basis}"
                    response = await asyncio.to_thread(crawler.get, url)
                    await writer.write(response.content, source_url=url, kind="html_report",
                        metadata={"endpoint": "week_options", "disease_code": code, "year": selected_year, "basis": basis})
                    if refresh is None:
                        selected = closed_single_weeks(response.content, year=selected_year, as_of=today, requested=weeks, calendar=calendar)
                    else:
                        selected = []
                        # Validate every advertised interval; only explicit,
                        # calendar-verified closed single weeks are candidates.
                        for interval, _ in options(response.content):
                            match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", interval)
                            if not match:
                                raise DDSContractError("Unrecognized published DDS reporting interval")
                            first, last = map(int, match.groups())
                            if first == last:
                                if first not in calendar:
                                    raise DDSContractError("Published week lacks verified source calendar")
                                if calendar[first][1] < today:
                                    selected.append(first)
                    eligible.extend((selected_year, week) for week in selected)
                selected_periods = refresh.select(code, eligible) if refresh is not None else eligible
                if not selected_periods:
                    raise DDSContractError("No published, closed single-week option is available; cumulative intervals are not weekly data")
                for selected_year, week in selected_periods:
                    request = {"level": "nation", "f_dz_gr": disease.group, "f_dz": code,
                        "f_year": str(selected_year), "date_type": basis, "f_wk_start_end": f"{week}-{week}"}
                    reports.append({"disease_code": code, "year": selected_year, "week": week, "basis": basis})
                    try:
                        report = await asyncio.to_thread(crawler.post, STORE_URL, data={"_token": token, **request})
                        await writer.write(report.content, source_url=STORE_URL,
                            kind="dds_situation_pdf", metadata={"endpoint": "weekly_report", **reports[-1],
                                "request": request, "retrieved_at": retrieved_at})
                    except requests.RequestException as exc:
                        errors.append({"disease_code": code, "year": selected_year, "week": week, "error": str(exc)})
            except (requests.RequestException, DDSContractError) as exc:
                errors.append({"disease_code": code, "year": year, "error": str(exc)})
        for selected_year in calendars:
            year_reports = [report for report in reports if report["year"] == selected_year]
            year_errors = [error for error in errors if error["year"] == selected_year]
            if not year_reports and not year_errors:
                continue
            await writer.write(json.dumps({
                "as_of": today.isoformat(), "year": selected_year, "basis": basis,
                "group_codes": [group for group, _ in groups],
                "catalog": [{"code": disease.code, "label": disease.label, "group": disease.group} for disease in catalog.values()],
                "requested_reports": year_reports, "collection_errors": year_errors,
                "refresh_policy": "bounded_per_category_recovery_and_revision" if refresh is not None else "explicit_manual_request",
            }, ensure_ascii=False, sort_keys=True).encode(), source_url=FORM_URL,
                kind="archive_json", metadata={"endpoint": "collection_plan", "year": selected_year})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, basis="onset", weeks=None, **options_):
    from .th_refresh import merge_results
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "collection_plan"]
    years = [int(json.loads(read_artifact(a))["year"]) for a in plans]
    if len(years) != len(set(years)):
        raise DDSContractError("Duplicate captured DDS year plan")
    results = []
    for plan in plans:
        year = int(json.loads(read_artifact(plan))["year"])
        selected = [a for a in artifacts if a.metadata_.get("endpoint") != "collection_plan"
            and (a.metadata_.get("year") is None or int(a.metadata_["year"]) == year)]
        results.append(_organize_year([*selected, plan], updater=updater, source=source, basis=basis))
    return merge_results(results, updater)


def _organize_year(artifacts, *, updater, source=DEFAULT_SCOPE, basis="onset", weeks=None, **options_):
    normalize_source(source)
    plans = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "collection_plan"]
    if len(plans) != 1:
        raise DDSContractError("Exactly one captured DDS collection plan is required")
    plan = json.loads(read_artifact(plans[0]))
    if plan["basis"] != basis:
        raise DDSContractError("Captured DDS basis does not match requested basis")
    as_of = date.fromisoformat(plan["as_of"])
    calendar_artifacts = [artifact for artifact in artifacts
        if artifact.metadata_.get("endpoint") == "calendar_workbook"
        and int(artifact.metadata_["year"]) == int(plan["year"])]
    if len(calendar_artifacts) != 1:
        raise DDSContractError(f"Missing captured official DDS calendar for {plan['year']}; dates were not inferred")
    calendar_artifact = calendar_artifacts[0]
    parents = [artifact for artifact in artifacts
        if artifact.metadata_.get("endpoint") == "calendar_rar"
        and int(artifact.metadata_["year"]) == int(plan["year"])]
    if len(parents) != 1 or hashlib.sha256(read_artifact(parents[0])).hexdigest() != calendar_artifact.metadata_["parent_raw_sha256"]:
        raise DDSContractError("Official DDS calendar workbook lacks matching raw source RAR provenance")
    calendar = parse_calendar_workbook(read_artifact(calendar_artifact), year=plan["year"])
    catalogs = {}
    captured_groups = set()
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") == "disease_group":
            group = artifact.metadata_["group"]
            if group in captured_groups:
                raise DDSContractError("Duplicate DDS group artifact")
            captured_groups.add(group)
            for disease in diseases(read_artifact(artifact), group):
                if disease.code in catalogs:
                    raise DDSContractError("Duplicate source disease in captured catalog")
                catalogs[disease.code] = disease
    if captured_groups != set(plan["group_codes"]):
        raise DDSContractError("A public disease-group artifact is missing")
    if {item["code"]: DDSDisease(**item) for item in plan["catalog"]} != catalogs:
        raise DDSContractError("Captured full disease inventory changed")
    national_forms = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "national_form"]
    if len(national_forms) != 1 or {code for code, _ in disease_groups(read_artifact(national_forms[0]))} != captured_groups:
        raise DDSContractError("Captured national form and disease groups do not agree")
    offered_weeks = {}
    offered_years = {}
    for artifact in artifacts:
        metadata = artifact.metadata_
        if metadata.get("endpoint") == "disease_years":
            code = metadata["disease_code"]
            if code in offered_years:
                raise DDSContractError("Duplicate captured disease-year selector")
            offered_years[code] = {value for value, _ in options(read_artifact(artifact))}
        elif metadata.get("endpoint") == "week_options":
            key = (metadata["disease_code"], int(metadata["year"]), metadata["basis"])
            if key in offered_weeks:
                raise DDSContractError("Duplicate captured weekly selector")
            all_closed = []
            raw = read_artifact(artifact)
            for interval, _ in options(raw):
                match = re.fullmatch(r"(\d{1,2})-\1", interval)
                if match:
                    try:
                        all_closed.extend(closed_single_weeks(raw, year=key[1], as_of=as_of, requested=[int(match[1])], calendar=calendar))
                    except DDSContractError:
                        pass  # an advertised current/open week is ineligible
            offered_weeks[key] = set(all_closed)
    expected = {(item["disease_code"], int(item["year"]), int(item["week"]), item["basis"]) for item in plan["requested_reports"]}
    errors, rows, seen = list(plan["collection_errors"]), [], set()
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "weekly_report":
            continue
        metadata = artifact.metadata_
        key = (metadata["disease_code"], int(metadata["year"]), int(metadata["week"]), metadata["basis"])
        if key in seen or key not in expected:
            raise DDSContractError("Duplicate or unexpected DDS weekly report artifact")
        seen.add(key)
        try:
            if str(key[1]) not in offered_years.get(key[0], set()) or key[2] not in offered_weeks.get((key[0], key[1], key[3]), set()):
                raise DDSContractError("Report is not an offered closed single-week selection in the captured public form")
            row = parse_weekly_pdf(read_artifact(artifact), disease=catalogs[key[0]], year=key[1],
                week=key[2], basis=key[3], as_of=as_of, retrieved_at=metadata["retrieved_at"], calendar=calendar)
        except DDSContractError as exc:
            errors.append({"disease_code": key[0], "year": key[1], "week": key[2], "error": str(exc)})
            continue
        rows.append(row)
    for code, year, week, requested_basis in sorted(expected - seen):
        errors.append({"disease_code": code, "year": year, "week": week, "error": "Captured weekly PDF is missing"})
    represented = {row["SourceDiseaseCode"] for row in rows}
    expected_codes = ({item["disease_code"] for item in plan["requested_reports"]}
        if plan.get("refresh_policy") == "bounded_per_category_recovery_and_revision" else set(catalogs))
    for code in sorted(expected_codes - represented):
        if not any(error["disease_code"] == code for error in errors):
            errors.append({"disease_code": code, "error": "No validated weekly observation for catalog disease"})
    if not rows and plan.get("refresh_policy") != "bounded_per_category_recovery_and_revision":
        raise DDSContractError("No validated closed single-week DDS reports: " + json.dumps(errors, ensure_ascii=False))
    rows.sort(key=lambda row: (row["Date"], row["SourceDiseaseCode"]))
    audit = {
        "status": "partial" if errors else "validated", "source_scope": DEFAULT_SCOPE,
        "source_inventory_categories": len(catalogs), "validated_categories": len(represented),
        "source_disease_codes": sorted(catalogs), "validated_disease_codes": sorted(represented),
        "requested_reports": len(expected), "observations": len(rows),
        "explicit_no_case_reports": sum(row["ReportStatus"] == "explicit_no_cases" for row in rows),
        "failed_reports": errors, "basis": basis, "as_of": plan["as_of"],
        "public_release_enabled": False, "authoritative_replacement": False,
        "does_not_claim_complete_old_506_category_inventory": True,
        "calendar_year": int(plan["year"]), "calendar_verified_weeks": len(calendar),
        "calendar_source_url": calendar_artifact.source_url,
    }
    return DDSFetchResult(rows, max((date.fromisoformat(row["Date"]) for row in rows), default=None), updater.output_csv,
        [f"[DDS] {len(represented)}/{len(catalogs)} public disease categories; {len(rows)} closed single-week observations; basis={basis}"], audit)
