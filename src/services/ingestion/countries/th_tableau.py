"""Durable raw summary-model replay for the official anonymous DDS Tableau."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from src.data.crawlers.th_dds import parse_calendar_workbook
from src.data.crawlers.th_tableau import (
    BASIS_LABELS,
    DEFAULT_SCOPE,
    VIEW_URL,
    TableauContractError,
    ThailandDDSTableauCrawler,
    build_row,
    card_filter_models,
    filter_models,
    inventory,
    national_snapshot,
    offline_workbook,
    replay_response,
    source_code,
)
from src.data.processors.th_dds import DDSFetchResult
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.countries.th_dds import capture_calendar


def normalize_source(source):
    if str(source).strip().casefold() not in {DEFAULT_SCOPE, "all", "th", "thailand", "dds_tableau"}:
        raise TableauContractError(f"Unsupported DDS Tableau source: {source!r}")


def closed_weeks(calendar, *, as_of, weeks=None, recent_weeks=1):
    offered = sorted(week for week, (_, end) in calendar.items() if end < as_of)
    if weeks is None:
        if not 1 <= int(recent_weeks) <= 53:
            raise TableauContractError("recent_weeks must be within 1..53")
        return offered[-int(recent_weeks):]
    selected = list(weeks)
    if not selected or len(set(selected)) != len(selected) or any(week not in offered for week in selected):
        raise TableauContractError("Only verified closed source weeks can be requested")
    return sorted(selected)


def bundle(entries, plan):
    manifest = []
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            raw = entry["payload"]
            archive.writestr(entry["name"], raw)
            manifest.append({"name": entry["name"], "sha256": hashlib.sha256(raw).hexdigest(),
                             "byte_size": len(raw), "source_url": VIEW_URL, "metadata": entry["metadata"]})
        archive.writestr("manifest.json", json.dumps({"version": 1, "members": manifest, "plan": plan},
                                                    ensure_ascii=False, sort_keys=True).encode())
    return stream.getvalue()


def unpack_bundle(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = [member["name"] for member in manifest["members"]]
        if manifest["version"] != 1 or len(names) != len(set(names)) or set(archive.namelist()) != {*names, "manifest.json"}:
            raise TableauContractError("Raw Tableau bundle inventory mismatch")
        entries = []
        for member in manifest["members"]:
            raw = archive.read(member["name"])
            if len(raw) != member["byte_size"] or hashlib.sha256(raw).hexdigest() != member["sha256"] or member["source_url"] != VIEW_URL:
                raise TableauContractError("Raw Tableau member provenance/hash mismatch")
            entries.append({**member, "payload": raw})
    return entries, manifest["plan"]


async def collect(writer, *, source=DEFAULT_SCOPE, basis="report", year=None, weeks=None,
                  recent_weeks=1, as_of=None, labels=None, refresh_state=None, **options):
    from .th_refresh import RefreshPlan, calendars_for_refresh
    normalize_source(source)
    if basis not in BASIS_LABELS:
        raise TableauContractError("Tableau basis must be report or onset")
    if refresh_state is None:
        return await _collect_year(writer, source=source, basis=basis, year=year, weeks=weeks,
            recent_weeks=recent_weeks, as_of=as_of, labels=labels)
    today = as_of or datetime.now(ZoneInfo("Asia/Bangkok")).date()
    if isinstance(today, str):
        today = date.fromisoformat(today)
    crawler = ThailandDDSTableauCrawler()
    try:
        calendars = await calendars_for_refresh(writer, crawler, refresh_state,
            as_of=today, capture_calendar=capture_calendar)
    finally:
        crawler.close()
    plan = RefreshPlan(calendars, refresh_state, as_of=today)
    for selected_year, calendar in calendars.items():
        await _collect_year(writer, source=source, basis=basis, year=selected_year,
            as_of=today, labels=labels, _calendar=calendar, _refresh_plan=plan)
    return writer.artifacts


async def _collect_year(writer, *, source=DEFAULT_SCOPE, basis="report", year=None, weeks=None,
                  recent_weeks=1, as_of=None, labels=None, _calendar=None, _refresh_plan=None):
    normalize_source(source)
    if basis not in BASIS_LABELS:
        raise TableauContractError("Tableau basis must be report or onset")
    today = as_of or datetime.now(ZoneInfo("Asia/Bangkok")).date()
    if isinstance(today, str):
        today = date.fromisoformat(today)
    year = int(year or today.year)
    selected_labels = None if labels is None else list(labels)
    if selected_labels is not None and (not selected_labels or len(set(selected_labels)) != len(selected_labels) or any(not isinstance(label, str) or not label.strip() for label in selected_labels)):
        raise TableauContractError("Unknown or duplicate source-native Tableau category")
    crawler = ThailandDDSTableauCrawler()
    try:
        calendar = _calendar if _calendar is not None else await capture_calendar(writer, crawler, year=year)
        selected_weeks = closed_weeks(calendar, as_of=today, weeks=weeks, recent_weeks=recent_weeks)
        if not selected_weeks and _refresh_plan is None:
            raise TableauContractError("No verified closed source week in the selected year")
        retrieved_at = datetime.now(ZoneInfo("Asia/Bangkok")).isoformat()
        def capture():
            catalog = crawler.load()
            active_labels = list(catalog["labels"] if selected_labels is None else selected_labels)
            if not set(active_labels) <= set(catalog["labels"]):
                raise TableauContractError("Requested labels are absent from the live disease selector")
            if year not in catalog["years"]:
                if _refresh_plan is not None:
                    return None, 0
                raise TableauContractError("Requested year is not in the live source year selector")
            requests_by_label = None
            if _refresh_plan is not None:
                eligible = [(y, week) for y, cal in _refresh_plan.calendars.items()
                    if y in catalog["years"] for week, (_, end) in cal.items() if end < today]
                requests_by_label = {label: {week for y, week in _refresh_plan.select(source_code(label), eligible) if y == year}
                    for label in active_labels}
                selected_weeks[:] = sorted({week for selected in requests_by_label.values() for week in selected})
                if not selected_weeks:
                    return None, 0
            crawler.select("ปี", year + 543, parameter=True)
            crawler.select("วัน/สถานที่", BASIS_LABELS[basis])
            errors = []
            for week in selected_weeks:
                crawler.select("สัปดาห์", str(week))
                for label in active_labels:
                    if requests_by_label is not None and week not in requests_by_label[label]:
                        continue
                    try:
                        crawler.select("โรค", label, report=True, year=year, basis=basis, week=week, label=label)
                    except (requests.RequestException, TableauContractError, KeyError, ValueError) as exc:
                        errors.append({"label": label, "year": year, "week": week, "error": str(exc)})
            plan = {"as_of": today.isoformat(), "year": year, "basis": basis, "weeks": selected_weeks,
                "labels": active_labels, "catalog": catalog, "collection_errors": errors,
                "retrieved_at": retrieved_at, "default_covers_entire_inventory": labels is None}
            if requests_by_label is not None:
                plan["requested_reports"] = [{"label": label, "week": week}
                    for label, selected in requests_by_label.items() for week in sorted(selected)]
                plan["refresh_policy"] = "bounded_per_category_recovery_and_revision"
            return bundle(crawler.entries, plan), len(active_labels)
        raw, category_count = await asyncio.to_thread(capture)
        if raw is None:
            return writer.artifacts
        await writer.write(raw, source_url=VIEW_URL, kind="th_tableau_summary_bundle",
            metadata={"endpoint": "summary_bundle", "year": year, "basis": basis, "weeks": selected_weeks,
                      "source_categories": category_count, "bundle_sha256": hashlib.sha256(raw).hexdigest()})
        return writer.artifacts
    finally:
        crawler.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, basis="report", **options):
    from .th_refresh import merge_results
    bundles = [a for a in artifacts if a.metadata_.get("endpoint") == "summary_bundle"]
    results = [_organize_year([*[
        a for a in artifacts if a.metadata_.get("endpoint") != "summary_bundle"], bundle],
        updater=updater, source=source, basis=basis) for bundle in bundles]
    years = [int(result.source_audit["year"]) for result in results]
    if len(years) != len(set(years)):
        raise TableauContractError("Duplicate captured Tableau year plan")
    return merge_results(results, updater)


def _organize_year(artifacts, *, updater, source=DEFAULT_SCOPE, basis="report", **options):
    normalize_source(source)
    bundles = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "summary_bundle"]
    if len(bundles) != 1:
        raise TableauContractError("Exactly one captured Tableau summary bundle required")
    raw_bundle = read_artifact(bundles[0])
    if hashlib.sha256(raw_bundle).hexdigest() != bundles[0].metadata_["bundle_sha256"]:
        raise TableauContractError("Tableau outer bundle hash mismatch")
    entries, plan = unpack_bundle(raw_bundle)
    if plan["basis"] != basis or basis not in BASIS_LABELS:
        raise TableauContractError("Captured and requested Tableau bases differ")
    year, today = int(plan["year"]), date.fromisoformat(plan["as_of"])
    calendars = [a for a in artifacts if a.metadata_.get("endpoint") == "calendar_workbook" and int(a.metadata_["year"]) == year]
    parents = [a for a in artifacts if a.metadata_.get("endpoint") == "calendar_rar" and int(a.metadata_["year"]) == year]
    if len(calendars) != 1 or len(parents) != 1 or hashlib.sha256(read_artifact(parents[0])).hexdigest() != calendars[0].metadata_["parent_raw_sha256"]:
        raise TableauContractError("Official source calendar lacks raw archive provenance")
    calendar = parse_calendar_workbook(read_artifact(calendars[0]), year=year)
    if closed_weeks(calendar, as_of=today, weeks=plan["weeks"]) != plan["weeks"]:
        raise TableauContractError("Captured week plan is not closed")
    bootstraps = [entry for entry in entries if entry["metadata"]["operation"] == "bootstrap"]
    configs = [entry for entry in entries if entry["metadata"]["operation"] == "public_config"]
    if len(bootstraps) != 1 or len(configs) != 1 or entries[:2] != [configs[0], bootstraps[0]]:
        raise TableauContractError("Unexpected Tableau initial capture sequence")
    config = json.loads(configs[0]["payload"])
    if config.get("is_guest") is not True or config.get("allow_summary") is not True:
        raise TableauContractError("Source was not an anonymous permitted summary session")
    scraper, workbook = offline_workbook(bootstraps[0]["payload"])
    catalog = inventory(workbook)
    if catalog != plan["catalog"] or year not in catalog["years"]:
        raise TableauContractError("Captured source inventory or year plan changed")
    expected = ({(item["label"], int(item["week"])) for item in plan["requested_reports"]}
        if "requested_reports" in plan else {(label, week) for label in plan["labels"] for week in plan["weeks"]})
    if any(label not in plan["labels"] or week not in plan["weeks"] for label, week in expected):
        raise TableauContractError("Requested reports differ from captured category/week plan")
    if not expected or not set(plan["labels"]) <= set(catalog["labels"]):
        raise TableauContractError("Invalid captured category plan")
    if plan["default_covers_entire_inventory"] and set(plan["labels"]) != set(catalog["labels"]):
        raise TableauContractError("Default refresh must cover every published category")
    filters = filter_models(bootstraps[0]["payload"])
    card_filters = card_filter_models(bootstraps[0]["payload"])
    rows, seen, errors = [], set(), list(plan["collection_errors"])
    state = {}
    for entry in entries[2:]:
        metadata, payload = entry["metadata"], entry["payload"]
        operation, field, value = metadata["operation"], metadata.get("field"), metadata.get("value")
        allowed = {"ปี": year + 543, "วัน/สถานที่": BASIS_LABELS[basis]}
        if operation == "parameter":
            if field != "ปี" or value != allowed[field]:
                raise TableauContractError("Unexpected parameter mutation in captured source session")
        elif operation == "filter":
            if field not in {"วัน/สถานที่", "สัปดาห์", "โรค"}:
                raise TableauContractError("Unexpected dimension mutation in captured national source session")
            if field == "วัน/สถานที่" and value != allowed[field] or field == "สัปดาห์" and int(value) not in plan["weeks"] or field == "โรค" and value not in plan["labels"]:
                raise TableauContractError("Source filter mutation differs from captured plan")
        else:
            raise TableauContractError("Unexpected raw source operation")
        workbook = replay_response(scraper, workbook, payload)
        filters.update(filter_models(payload))
        for card, models in card_filter_models(payload).items():
            card_filters[card].update(models)
        state[field] = value
        if not metadata.get("report"):
            continue
        key = (metadata["label"], int(metadata["week"]))
        if key not in expected or key in seen or metadata["year"] != year or metadata["basis"] != basis or state != {"ปี": year + 543, "วัน/สถานที่": BASIS_LABELS[basis], "สัปดาห์": str(key[1]), "โรค": key[0]}:
            raise TableauContractError("Duplicate/unplanned report or source session selection mismatch")
        seen.add(key)
        try:
            snapshot = national_snapshot(workbook, year=year, basis=basis, week=key[1],
                                         provinces=catalog["provinces"], label=key[0], filters=filters,
                                         card_filters=card_filters)
            rows.append(build_row(snapshot, label=key[0], year=year, week=key[1], basis=basis,
                calendar=calendar, as_of=today, raw_sha256=entry["sha256"], retrieved_at=plan["retrieved_at"]))
        except TableauContractError as exc:
            errors.append({"label": key[0], "week": key[1], "error": str(exc)})
    for label, week in sorted(expected - seen):
        errors.append({"label": label, "week": week, "error": "Captured single-week national response missing"})
    if not rows and plan.get("refresh_policy") != "bounded_per_category_recovery_and_revision":
        raise TableauContractError("No valid closed-week national source summaries: " + json.dumps(errors, ensure_ascii=False))
    represented = {row["RawDiseaseLabel"] for row in rows}
    audit = {"status": "partial" if errors else "validated", "source_scope": DEFAULT_SCOPE,
        "source_inventory_categories": len(catalog["labels"]), "validated_categories": len(represented),
        "requested_reports": len(expected), "observations": len(rows), "failed_reports": errors,
        "source_labels": catalog["labels"], "validated_labels": sorted(represented), "basis": basis,
        "new_labels": catalog["new_labels"], "missing_reviewed_labels": catalog["missing_reviewed_labels"],
        "year": year, "weeks": plan["weeks"], "as_of": plan["as_of"], "national_evidence": "explicit_national_cards",
        "province_sum_is_validation_only": True, "unknown_location_included_in_national": True,
        "public_release_enabled": False, "authoritative_replacement": False}
    return DDSFetchResult(rows, max((date.fromisoformat(row["Date"]) for row in rows), default=None), updater.output_csv,
        [f"[DDS Tableau] {len(represented)}/{len(catalog['labels'])} source categories; {len(rows)} closed-week national summaries; basis={basis}"], audit)
