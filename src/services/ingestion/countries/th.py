"""Archive official Report 506 bytes, then validate each report offline.

One unavailable or invalid disease is visible in the audit and does not erase
the independently valid reports. These partial runs never replace an
authoritative country-wide window or enable public release.
"""
from __future__ import annotations

import asyncio
from datetime import date
import json

import requests

from src.data.crawlers.th import (
    DEFAULT_SCOPE, DEFAULT_START_YEAR, DEFAULT_END_YEAR, DEFAULT_MAX_REPORTS, INDEX_URL,
    Thailand506ContractError, ThailandDOE506Crawler, discover_diseases,
    discover_monthly_report, parse_monthly_rtf, selected_contracts,
    validate_year_window, plan_history_reports,
)
from src.data.processors.th import Thailand506FetchResult
from src.services.ingestion.artifacts import read_artifact


def normalize_source(source):
    if str(source).strip().lower() not in {"all", "th", "thailand", DEFAULT_SCOPE}:
        raise ValueError(f"Unsupported Thailand source: {source!r}")


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=DEFAULT_START_YEAR,
                  end_year=DEFAULT_END_YEAR, disease_codes=None, mode=None,
                  backfill_history=False, report_offset=0, max_reports=DEFAULT_MAX_REPORTS,
                  resume_plan_hash=None, recent_years=3, **options):
    normalize_source(source)
    contracts = selected_contracts(disease_codes)
    effective_mode = mode or ("history" if backfill_history else "incremental")
    crawler = ThailandDOE506Crawler()
    errors, histories = [], {}
    try:
        response = await asyncio.to_thread(crawler.get, INDEX_URL)
        await writer.write(response.content, source_url=INDEX_URL, kind="html_report",
                           metadata={"endpoint": "index"})
        catalog = discover_diseases(response.content)
        for contract in contracts:
            if contract.code not in catalog:
                errors.append({"disease_code": contract.code, "year": 0, "stage": "history",
                               "error": "Reviewed disease is absent from the live official index"})
                continue
            history_url = catalog[contract.code] + "&dcontent=old"
            try:
                response = await asyncio.to_thread(crawler.get, history_url)
            except requests.RequestException as exc:
                errors.append({"disease_code": contract.code, "year": 0, "stage": "history",
                               "error": str(exc), "url": history_url})
                continue
            await writer.write(response.content, source_url=history_url, kind="html_report",
                               metadata={"endpoint": "history", "disease_code": contract.code})
            histories[contract.code] = response.content
        request = dict(start_year=start_year, end_year=end_year, disease_codes=disease_codes,
                       mode=effective_mode, report_offset=report_offset, max_reports=max_reports,
                       resume_plan_hash=resume_plan_hash, recent_years=recent_years,
                       as_of=date.today().isoformat())
        plan = plan_history_reports(histories, **request)
        if not plan["selected_reports"]:
            raise Thailand506ContractError("No monthly reports in this discovered batch; inspect the year bounds or resume cursor")
        for target in plan["selected_reports"]:
            code, year, report_url = target["disease_code"], target["year"], target["url"]
            try:
                report = await asyncio.to_thread(crawler.get, report_url)
            except requests.RequestException as exc:
                errors.append({"disease_code": code, "year": year, "error": str(exc), "url": report_url})
                continue
            await writer.write(report.content, source_url=report_url, kind="monthly_rtf",
                               metadata={"endpoint": "monthly", "disease_code": code, "year": year})
        await writer.write(json.dumps({
            "expected": [[target["disease_code"], target["year"]] for target in plan["selected_reports"]],
            "catalog_category_count": len(catalog), "collection_errors": errors,
            "plan": plan, "request": request,
        }, ensure_ascii=False, sort_keys=True).encode(), source_url=INDEX_URL,
            kind="archive_json", metadata={"endpoint": "collection_audit"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, start_year=DEFAULT_START_YEAR,
             end_year=DEFAULT_END_YEAR, disease_codes=None, mode=None, backfill_history=False,
             report_offset=0, max_reports=DEFAULT_MAX_REPORTS, resume_plan_hash=None, recent_years=3, artifact_reader=None, **options):
    normalize_source(source)
    read = artifact_reader or read_artifact
    contracts = selected_contracts(disease_codes)
    index = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "index"]
    audit_artifacts = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "collection_audit"]
    if len(index) != 1 or len(audit_artifacts) != 1:
        raise Thailand506ContractError("One captured official index and collection audit are required")
    catalog = discover_diseases(read(index[0]))
    collection_audit = json.loads(read(audit_artifacts[0]))
    errors = list(collection_audit["collection_errors"])
    histories = {}
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "history":
            continue
        code = artifact.metadata_["disease_code"]
        if code in histories:
            raise Thailand506ContractError("Duplicate captured disease history")
        histories[code] = artifact
    plan = collection_audit.get("plan")
    if plan is not None:
        recalculated = plan_history_reports(
            {code: read(artifact) for code, artifact in histories.items()},
            start_year=start_year, end_year=end_year, disease_codes=disease_codes,
            mode=mode or ("history" if backfill_history else "incremental"),
            report_offset=report_offset, max_reports=max_reports,
            resume_plan_hash=resume_plan_hash, recent_years=recent_years,
            as_of=collection_audit["request"]["as_of"],
        )
        if recalculated != plan:
            raise Thailand506ContractError("Captured history plan does not match the requested discovery snapshot")
        expected = {(target["disease_code"], target["year"]) for target in plan["selected_reports"]}
    else:
        # Replay compatibility for the first verified 2023 source archive.
        captured_years = {int(year) for _, year in collection_audit["expected"]}
        years = validate_year_window(
            min(captured_years) if start_year is None else start_year,
            max(captured_years) if end_year is None else end_year,
        )
        expected = {(contract.code, year) for contract in contracts for year in years}
    if set(map(tuple, collection_audit["expected"])) != expected:
        raise Thailand506ContractError("Captured request does not match the requested report window")
    rows, valid, seen = [], set(), set()
    for artifact in artifacts:
        if artifact.kind != "monthly_rtf":
            continue
        code, year = artifact.metadata_["disease_code"], int(artifact.metadata_["year"])
        key = (code, year)
        if key not in expected or key in seen:
            raise Thailand506ContractError("Duplicate or unexpected monthly source artifact")
        seen.add(key)
        try:
            if code not in catalog:
                raise Thailand506ContractError("Source category not present in captured index")
            if code not in histories or artifact.source_url != discover_monthly_report(
                read(histories[code]), disease_code=code, year=year
            ):
                raise Thailand506ContractError("Monthly report is not linked from the captured official history")
            report_rows = parse_monthly_rtf(read(artifact), disease_code=code,
                                            year=year, source_url=artifact.source_url)
        except (Thailand506ContractError, UnicodeError) as exc:
            errors.append({"disease_code": code, "year": year, "error": str(exc), "url": artifact.source_url})
            continue
        rows.extend(report_rows)
        valid.add(key)
    recorded_errors = {(entry["disease_code"], int(entry["year"])) for entry in errors}
    for code, year in sorted(expected - valid - recorded_errors):
        errors.append({"disease_code": code, "year": year, "error": "Expected monthly report artifact is missing"})
    if not rows and (plan is None or plan["mode"] != "history"):
        raise Thailand506ContractError("No independently validated Thailand monthly reports: " + json.dumps(errors, ensure_ascii=False))
    rows.sort(key=lambda row: (row["Date"], row["SourceDiseaseCode"]))
    return Thailand506FetchResult(
        rows=rows,
        source_latest_date=max((date.fromisoformat(row["Date"]) for row in rows), default=None),
        source_csv=updater.output_csv,
        script_logs=[f"[506] {len(valid)}/{len(expected)} validated disease/year reports; {len(rows)} national month rows; public release disabled"],
        source_audit={
            "status": "partial" if errors else "validated",
            "catalog_category_count": len(catalog), "expected_reports": len(expected),
            "valid_reports": len(valid), "validated_disease_codes": sorted({code for code, _ in valid}),
            "failed_reports": errors, "public_release_enabled": False,
            "license_review_status": "unverified", "authoritative_replacement": False,
            "overlapping_source_categories_are_separate": True,
            "validated_years": sorted({year for _, year in valid}),
            "latest_valid_year": max((year for _, year in valid), default=None),
            "latest_valid_year_by_category": {
                code: max(year for candidate, year in valid if candidate == code)
                for code in sorted({candidate for candidate, _ in valid})
            },
            **({key: value for key, value in plan.items() if key != "selected_reports"} if plan else {}),
        },
    )
