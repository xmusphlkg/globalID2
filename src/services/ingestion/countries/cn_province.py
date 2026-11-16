"""Province collection with durable HTTP snapshots and strictly offline replay."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import select, tuple_

from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
from src.data.crawlers.cn_provinces import (
    MonthlyReportLink, ProvinceDataCenterCrawler, ProvinceMonthlyReportCrawler,
    province_configs,
)
from src.data.processors.cn_provinces import expand_province_metric_rows
from src.domain import DiseaseSeriesObservation, RunStage, Task, TaskWorkbook
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.replay import request_key
from src.services.ingestion.stages import mark_dirty
from src.services.exceptions import TaskCancelledError
from src.services.cn_province_month_quality import detect_previous_month_copy

SCOPES = frozenset({"cn_province_datacenter", "cn_province_monthly_report"})


def select_monthly_links(links, *, recent_months=3, force=False, start_year=None,
                         year=None, month=None, today=None, fill_missing=False,
                         known_case_months=(), recently_attempted_months=(),
                         historical_limit=3):
    """Select complete report months, retaining every revision within a month.

    The revision window counts distinct months, not URLs. An explicit historical
    window selects all discovered links in that window; discovery still owns
    pagination and cannot infer unpublished months.
    """
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    selected = sorted(
        (link for link in links
         if link.report_date < today.replace(day=1)
         and (start_year is None or link.report_date.year >= start_year)
         and (year is None or link.report_date.year == year)
         and (month is None or link.report_date.month == month)),
        key=lambda link: (link.report_date, link.url),
    )
    if not force and start_year is None and year is None and month is None:
        months = sorted({link.report_date for link in selected})
        recent = set(months[-max(1, min(24, int(recent_months))):])
        chosen = set(recent)
        if fill_missing:
            known = set(known_case_months)
            attempted = set(recently_attempted_months)
            eligible = [value for value in months if value.year >= 2022 and value not in recent
                        and value.strftime("%Y-%m") not in known | attempted]
            chosen.update(eligible[:max(0, min(3, int(historical_limit)))])
        selected = [link for link in selected if link.report_date in chosen]
    return selected


def collection_status(rows, errors):
    """Keep sparse evidence and partial failures visible even when rows exist."""
    if not rows:
        return "failed_closed" if errors else "no_parsed_rows"
    if errors:
        return "partial"
    if all(("Cases" in row and row["Cases"] is None) or row.get("Measure") == "deaths" or str(row.get("SourceDiseaseCode") or "").endswith("__deaths")
           for row in rows):
        return "death_only" if any(row.get("Cases") is not None for row in rows) else "partial"
    if any("Cases" in row and row["Cases"] is None for row in rows):
        return "partial"
    narrative_rows = sum(isinstance(row.get("Raw"), dict)
                         and "explicit_narrative_value" in row["Raw"] for row in rows)
    if narrative_rows == len(rows):
        return "narrative_sparse"
    if narrative_rows:
        return "partial"
    return "parsed"


def diagnostic_errors(diagnostics, stage):
    return [{**item, "stage": stage} for item in diagnostics
            if item.get("status") in {"failed", "attachment_parse_failed", "attachment_fetch_failed", "invalid_response",
                                      "image_capture_failed", "image_document_limit"}]


def merge_report_rows(batches):
    """Reject conflicting revisions of one month without losing other months.

    Each batch is (report metadata, rows). Equal facts keep deterministic first
    provenance; a conflicting disease value quarantines the entire source/month.
    """
    values, conflicts, errors = {}, set(), []
    for report, rows in batches:
        for row in rows:
            period = (row["SourceID"], row["JurisdictionCode"], row["Date"])
            if period in conflicts:
                continue
            key = (*period, row["SourceDiseaseCode"], row.get("DefinitionVersion"))
            previous = values.get(key)
            conflicting = previous is not None and (
                (previous["Cases"] is not None and row["Cases"] is not None and previous["Cases"] != row["Cases"])
                or (previous.get("Deaths") is not None and row.get("Deaths") is not None
                    and previous["Deaths"] != row["Deaths"]))
            if conflicting:
                conflicts.add(period)
                errors.append({**report, "stage": "reconcile", "error":
                               f"Conflicting province source rows for {period}: {row['SourceDiseaseCode']}"})
            else:
                values.setdefault(key, row)
                if previous is not None and (previous["Cases"] is None and row["Cases"] is not None
                                             or previous.get("Deaths") is None and row.get("Deaths") is not None):
                    values[key] = row
    return [row for key, row in values.items() if key[:3] not in conflicts], errors


def validate_scope(country_code, source):
    if country_code not in province_configs():
        raise ValueError(f"Unknown Chinese province: {country_code}")
    if source not in SCOPES | {"all"}:
        raise ValueError(f"Unsupported Chinese province source: {source}")


class _RecordingSession(requests.Session):
    def __init__(self, writer, loop, cancel_event):
        super().__init__()
        self.writer, self.loop = writer, loop
        self.cancel_event = cancel_event

    def get(self, url, **kwargs):
        _check_cancelled(self.cancel_event)
        response = super().get(url, **kwargs)
        return self._record(response, url, kwargs)

    def post(self, url, **kwargs):
        _check_cancelled(self.cancel_event)
        response = super().post(url, **kwargs)
        return self._record(response, url, kwargs, method="POST")

    def _record(self, response, url, kwargs, method="GET"):
        _check_cancelled(self.cancel_event)
        # The crawler must see status/cookies before deciding whether to retry.
        # Failed response bytes remain evidence, but replay excludes them below.
        asyncio.run_coroutine_threadsafe(
            self.writer.write(
                response.content, source_url=request_key(url, kwargs.get("params")),
                kind="province_response",
                metadata={"response_url": response.url,
                          "media_type": response.headers.get("Content-Type", ""),
                          "request_method": method, "request_data": kwargs.get("data"),
                          "encoding": response.encoding,
                          "status_code": response.status_code},
            ), self.loop,
        ).result()
        return response


class _ReplaySession(requests.Session):
    def __init__(self, artifacts):
        super().__init__()
        successful = [a for a in artifacts if a.kind == "province_response"
                      and 200 <= int(a.metadata_.get("status_code", 200)) < 300]
        self.artifacts = {request_key(a.source_url): a for a in successful if a.metadata_.get("request_method", "GET") == "GET"}
        self.post_artifacts = [a for a in successful if a.metadata_.get("request_method") == "POST"]

    def get(self, url, **kwargs):
        key = request_key(url, kwargs.get("params"))
        if key not in self.artifacts:
            raise RuntimeError(f"No captured province response: {key}")
        artifact = self.artifacts[key]
        return self._response(artifact)

    def post(self, url, **kwargs):
        key = request_key(url, kwargs.get("params"))
        candidates = [a for a in self.post_artifacts if request_key(a.source_url) == key and a.metadata_.get("request_data") == kwargs.get("data")]
        if not candidates:
            raise RuntimeError(f"No captured province POST response: {key}")
        return self._response(candidates[-1])

    @staticmethod
    def _response(artifact):
        response = requests.Response()
        response.status_code = int(artifact.metadata_.get("status_code", 200))
        response.url = artifact.metadata_.get("response_url") or artifact.source_url
        response.headers["Content-Type"] = artifact.metadata_.get("media_type", "")
        response.encoding = artifact.metadata_.get("encoding")
        response._content = read_artifact(artifact)
        response._content_consumed = True
        return response

    def send(self, *args, **kwargs):
        raise RuntimeError("Province organization cannot access the network")


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise TaskCancelledError("Province collection cancelled")


def _collect(session, *, country_code, source, force, start_year, recent_months,
             fill_missing=False, known_case_months=(), recently_attempted_months=(),
             cancel_event=None, today=None):
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    recent_months = max(1, min(24, int(recent_months)))
    _check_cancelled(cancel_event)
    plan = {"country_code": country_code, "source": source, "reports": [], "datacenter": [],
            "errors": [], "discovery_diagnostics": [], "datacenter_diagnostics": [],
            "latest_discovered_month": None,
            "history_backfill": {"enabled": bool(fill_missing and not force and start_year is None),
                                 "selected_months": [], "limit": 3, "cooldown_days": 180}}
    if source in {"all", "cn_province_monthly_report"}:
        crawler = ProvinceMonthlyReportCrawler(session=session)
        try:
            discovered = crawler.discover(country_code)
            discovered_months = [item.report_date for item in discovered
                                 if item.report_date < today.replace(day=1)]
            plan["latest_discovered_month"] = (max(discovered_months).strftime("%Y-%m")
                                                if discovered_months else None)
            recent = select_monthly_links(discovered, recent_months=recent_months, today=today)
            links = select_monthly_links(discovered, recent_months=recent_months,
                force=force, start_year=start_year, today=today, fill_missing=fill_missing,
                known_case_months=known_case_months,
                recently_attempted_months=recently_attempted_months)
            if plan["history_backfill"]["enabled"]:
                recent_dates = {item.report_date for item in recent}
                plan["history_backfill"]["selected_months"] = sorted({
                    item.report_date.strftime("%Y-%m") for item in links
                    if item.report_date not in recent_dates})
                plan["history_backfill"]["discovered_candidates"] = len({
                    item.report_date for item in discovered if item.report_date.year >= 2022
                    and item.report_date < today.replace(day=1)
                    and item.report_date.strftime("%Y-%m") not in set(known_case_months)})
            if not links:
                raise RuntimeError(f"No official monthly reports discovered for {country_code}")
        except TaskCancelledError:
            raise
        except Exception as exc:
            links = []
            plan["errors"].append({"source": "cn_province_monthly_report", "stage": "discover", "error": str(exc)})
        plan["discovery_diagnostics"] = list(getattr(crawler, "discovery_diagnostics", []))
        plan["errors"].extend(diagnostic_errors(plan["discovery_diagnostics"], "discover"))
        for link in links:
            _check_cancelled(cancel_event)
            # Discovery and attachment enumeration need the existing parser;
            # formal rows are independently rebuilt from raw bytes in organize.
            report = {"date": link.report_date.isoformat(), "title": link.title, "url": link.url}
            try:
                fetched = crawler.fetch(link)
                if not fetched.rows and not getattr(fetched, "image_documents", []):
                    raise ValueError(f"No explicit disease values found in {link.url}")
            except TaskCancelledError:
                raise
            except Exception as exc:
                diagnostics = list(getattr(crawler, "report_diagnostics", []))
                plan["errors"].append({**report, "source": "cn_province_monthly_report", "stage": "fetch",
                                       "error": str(exc), "diagnostics": diagnostics})
                plan["errors"].extend(diagnostic_errors(diagnostics, "fetch"))
            else:
                report["diagnostics"] = list(getattr(crawler, "report_diagnostics", []))
                report["status"] = ("image_pending_review" if getattr(fetched, "image_documents", [])
                                    else collection_status(fetched.rows, diagnostic_errors(report["diagnostics"], "fetch")))
                plan["errors"].extend(diagnostic_errors(report["diagnostics"], "fetch"))
                plan["reports"].append(report)
    if source in {"all", "cn_province_datacenter"}:
        crawler = ProvinceDataCenterCrawler(session=session)
        try:
            years = crawler.available_years()
            years = sorted(y for y in years if y < today.year or (y == today.year and today.month > 1))
            years = [y for y in years if y >= start_year] if start_year is not None else years[-1:]
            if not years:
                raise RuntimeError("No published Data Center years match the requested window")
            for year in years:
                last_month = today.month - 1 if year == today.year else 12
                # A newly available annual release must not lose January–September
                # merely because this task runs once a year. Monthly revisions use
                # their own source/task; this source refreshes all closed months.
                for disease in crawler.config["disease_ids"]:
                    for month in range(1, last_month + 1):
                        _check_cancelled(cancel_event)
                        crawler._fetch_report(year, disease, month)
                        plan["datacenter"].append([year, disease, month])
        except TaskCancelledError:
            raise
        except Exception as exc:
            plan["errors"].append({"source": "cn_province_datacenter", "stage": "fetch", "error": str(exc)})
        plan["datacenter_diagnostics"] = list(getattr(crawler, "response_diagnostics", []))
    return plan


async def collect(writer, *, source, country_code, force=False, start_year=None, recent_months=3,
                  fill_missing=False, known_case_months=(), recently_attempted_months=()):
    validate_scope(country_code, source)
    cancel_event = Event()
    with _RecordingSession(writer, asyncio.get_running_loop(), cancel_event) as session:
        work = asyncio.create_task(asyncio.to_thread(
            _collect, session, country_code=country_code, source=source,
            force=force, start_year=start_year, recent_months=recent_months,
            fill_missing=fill_missing, known_case_months=known_case_months,
            recently_attempted_months=recently_attempted_months,
            cancel_event=cancel_event,
        ))
        try:
            plan = await asyncio.shield(work)
        except asyncio.CancelledError:
            # to_thread cannot terminate a running request. Stop subsequent
            # requests/writes, then drain the worker before closing its session
            # or allowing the runner to mark collection terminal.
            cancel_event.set()
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if work.done() and not work.cancelled():
                work.exception()
            raise
    await writer.write(json.dumps(plan).encode(), source_url=f"urn:globalid:province:{country_code}",
                       kind="province_plan", metadata={"history_backfill": plan["history_backfill"]})
    return writer.artifacts


def organize(artifacts, *, updater, source, country_code, **options):
    validate_scope(country_code, source)
    plans = [a for a in artifacts if a.kind == "province_plan"]
    if len(plans) != 1:
        raise RuntimeError("Exactly one province collection plan is required")
    plan = json.loads(read_artifact(plans[0]))
    if plan["country_code"] != country_code or plan["source"] != source:
        raise ValueError("Province collection plan scope mismatch")
    rows, batches, pending = [], [], []
    errors = list(plan.get("errors", []))
    with _ReplaySession(artifacts) as session:
        monthly = ProvinceMonthlyReportCrawler(session=session)
        for item in plan["reports"]:
            link = MonthlyReportLink(country_code, date.fromisoformat(item["date"]), item["title"], item["url"])
            try:
                fetched = monthly.fetch(link)
                known_diagnostics = {(error.get("status"), error.get("url")) for error in errors}
                errors.extend(error for error in diagnostic_errors(getattr(monthly, "report_diagnostics", []), "organize")
                              if (error.get("status"), error.get("url")) not in known_diagnostics)
                batches.append((item, fetched.rows))
                if getattr(fetched, "image_documents", []):
                    pending.append({"report": item, "link": link,
                                    "documents": fetched.image_documents,
                                    "article_text": fetched.article_text,
                                    "diagnostics": list(getattr(monthly, "report_diagnostics", []))})
            except Exception as exc:
                errors.append({**item, "source": "cn_province_monthly_report", "stage": "organize", "error": str(exc)})
        monthly_rows, conflicts = merge_report_rows(batches)
        rows.extend(monthly_rows)
        errors.extend(conflicts)
        center = ProvinceDataCenterCrawler(session=session, request_interval=0)
        for year, disease, month in plan["datacenter"]:
            try:
                rows.extend(r for r in center._fetch_report(year, disease, month)
                            if r["GeographyKey"] == f"country:{country_code}:national")
            except Exception as exc:
                errors.append({"source": "cn_province_datacenter", "stage": "organize",
                               "year": year, "month": month, "disease": disease, "error": str(exc)})
    if not rows and not (pending and options.get("_allow_image_pending")):
        raise RuntimeError(f"No usable observations for {country_code}; refusing empty store: {json.dumps(errors, ensure_ascii=False)}")
    if any(r["GeographyKey"] != f"country:{country_code}:national" for r in rows):
        raise ValueError("Cross-province observations rejected")
    return SimpleNamespace(rows=rows, image_pending=pending, report_batches=batches, source_audit={
        "status": "needs_review" if pending else collection_status(rows, errors), "errors": errors,
        "report_count": len(plan["reports"]), "rows": len(rows),
        "latest_report_month": max((str(row["Date"])[:7] for row in rows
                                    if row.get("SourceID") == "SRC_CN_PROV_MONTHLY_REPORT"
                                    and row.get("Cases") is not None
                                    and row.get("Measure") != "deaths"
                                    and not str(row.get("SourceDiseaseCode") or "").endswith("__deaths")),
                                   default=None),
        "latest_discovered_month": plan.get("latest_discovered_month"),
        "narrative_rows": sum("explicit_narrative_value" in (row.get("Raw") or {}) for row in rows),
        "reports": plan["reports"],
        "discovery_diagnostics": plan.get("discovery_diagnostics", []),
        "history_backfill": plan.get("history_backfill", {}),
        "datacenter_diagnostics": plan.get("datacenter_diagnostics", []),
    })


def _verified_image_rows(review, bundle, crawler):
    """Reuse the ordinary monthly disease/definition/row contracts."""
    import pandas as pd

    if review.get("status") != "validated" or not review.get("rows"):
        raise ValueError("Unverified image candidates cannot become observations")
    table = pd.DataFrame([{"病名": item["label"], "发病数": item["cases"],
                           "死亡数": item.get("deaths")} for item in review["rows"]])
    document = bundle["documents"][0]
    rows = crawler._normalize_tables([table], link=bundle["link"],
                                    source_url=document["source_url"], artifact_sha256=document["sha256"])
    from src.data.crawlers.cn_provinces import _resolve_report_disease
    evidence = {_resolve_report_disease(item["label"])["code"]: item for item in review["rows"]}
    document_by_hash = {doc["sha256"]: doc for doc in bundle["documents"]}
    from src.ai.runtime.context import current_parent
    parent = current_parent()
    receipt = {key: review["audit"].get(key) for key in (
        "protocol", "status", "review_mode", "panel_size", "quorum", "winner_votes", "winner_fingerprint",
    )}
    receipt.update({"task_uuid": parent.task_uuid if parent else None,
                    "audit_sha256": hashlib.sha256(json.dumps(review["audit"], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                    "readings": [{key: reading.get(key) for key in ("role", "model", "input_mode", "status", "reason")}
                                 for reading in review["audit"].get("readings", [])]})
    for row in rows:
        visual = evidence[row["SourceDiseaseCode"]]
        original = document_by_hash.get(visual.get("document_sha256"), document)
        row["SourceURL"] = original["source_url"]
        row["QualityStatus"] = "validated"
        row["Raw"].update({"extraction_method": "ai_expert_image_vote",
                          "artifact_sha256": original["sha256"],
                          "image_review": receipt,
                          "visual_row": visual,
                          "source_documents": [{key: doc[key] for key in ("source_url", "sha256", "media_type")}
                                               for doc in bundle["documents"]]})
    return rows


async def organize_async(artifacts, *, updater, source, country_code, **options):
    """Visual verification runs as durable model work, outside store locks."""
    from src.services.cn_province_image_review import review_image_report

    fetched = await asyncio.to_thread(organize, artifacts, updater=updater, source=source,
                                     country_code=country_code, **{**options, "_allow_image_pending": True})
    batches = list(fetched.report_batches)
    reviews = []
    for bundle in fetched.image_pending:
        report = bundle["report"]
        try:
            if any(item.get("status") in {"image_capture_failed", "image_document_limit", "attachment_fetch_failed"} for item in bundle["diagnostics"]):
                raise ValueError("Incomplete report originals; visual review deferred")
            review = await review_image_report(documents=bundle["documents"],
                province_code=country_code, report_month=bundle["link"].report_date.strftime("%Y-%m"),
                article_text=bundle["article_text"], panel_size=options.get("image_panel_size", 3))
            if review["status"] == "validated":
                verified = _verified_image_rows(review, bundle, ProvinceMonthlyReportCrawler())
                batches.append((report, verified))
        except Exception as exc:
            review = {"status": "needs_review", "rows": [], "audit": {"reason": str(exc)}}
        reviews.append({"report": report, "status": review["status"], "audit": review["audit"]})
    monthly, conflicts = merge_report_rows(batches)
    # Datacenter observations remain a distinct source.
    fetched.rows = [row for row in fetched.rows if row["SourceID"] != "SRC_CN_PROV_MONTHLY_REPORT"] + monthly
    fetched.source_audit["errors"].extend(conflicts)
    fetched.source_audit.update({"image_reviews": reviews, "rows": len(fetched.rows),
        "latest_report_month": max((str(row["Date"])[:7] for row in fetched.rows
                                    if row.get("SourceID") == "SRC_CN_PROV_MONTHLY_REPORT"
                                    and row.get("Cases") is not None
                                    and row.get("Measure") != "deaths"
                                    and not str(row.get("SourceDiseaseCode") or "").endswith("__deaths")),
                                   default=None)})
    unresolved = (any(item["status"] != "validated" for item in reviews)
                  or any(item.get("status") in {"image_capture_failed", "image_document_limit"}
                         for item in fetched.source_audit["errors"]))
    fetched.source_audit["status"] = ("needs_review" if unresolved or conflicts else
                                       collection_status(fetched.rows, fetched.source_audit["errors"]))
    # Original bytes stay in immutable response artifacts, never task JSON.
    del fetched.image_pending
    del fetched.report_batches
    return fetched


class ProvinceCommitStrategy:
    async def commit(self, db, *, updater, fetched, task_uuid, **kwargs):
        # Match the store's CN mutation partition before comparing any facts.
        # The transaction retains this lock through candidate recording/import.
        await acquire_country_data_mutation_lock(db, "CN")
        observations = []
        by_source = {}
        for row in expand_province_metric_rows(fetched.rows):
            by_source.setdefault(str(row.get("SourceID") or ""), []).append(row)
        for source_id, rows in sorted(by_source.items()):
            built = updater.store.build_observations(rows, "CN", source_id=source_id)
            if built.skipped_invalid or built.skipped_ambiguous or built.skipped_unmatched:
                raise ValueError("Province scheduled ingestion mapping preflight failed")
            observations.extend(built.observations)

        identities = [_observation_identity(row) for row in observations]
        monthly_cases = {}
        for observation in observations:
            raw = observation.get("raw_data") or {}
            if (raw.get("SourceID") == "SRC_CN_PROV_MONTHLY_REPORT"
                    and observation["series_code"].startswith("SER_CN_PROV_REPORT_")
                    and not observation["series_code"].startswith("SER_CN_PROV_REPORT_DEATHS_")):
                key = (observation["geography_key"], observation["time"])
                monthly_cases.setdefault(key, []).append(observation)
        lookup_identities = list(identities)
        for group in monthly_cases.values():
            if len(group) < 4:
                continue
            previous_time = (group[0]["time"] - timedelta(days=1)).replace(day=1)
            lookup_identities.extend((previous_time, row["series_code"], row["geography_key"],
                                      row["dimension_key"]) for row in group)
        lookup_identities = list(dict.fromkeys(lookup_identities))
        existing = {}
        for offset in range(0, len(lookup_identities), 500):
            result = await db.execute(select(DiseaseSeriesObservation).where(tuple_(
                DiseaseSeriesObservation.time, DiseaseSeriesObservation.series_code,
                DiseaseSeriesObservation.geography_key, DiseaseSeriesObservation.dimension_key,
            ).in_(lookup_identities[offset:offset + 500])).with_for_update())
            for current in result.scalars().all():
                existing[(current.time, current.series_code, current.geography_key,
                          current.dimension_key)] = current

        incoming_by_identity = {_observation_identity(row): row for row in observations}
        copied_months = {}
        for key, group in monthly_cases.items():
            if len(group) < 4:
                continue
            previous_time = (key[1] - timedelta(days=1)).replace(day=1)
            previous_rows = []
            stored_rows = []
            for row in group:
                previous_key = (previous_time, row["series_code"], row["geography_key"], row["dimension_key"])
                previous = existing.get(previous_key) or incoming_by_identity.get(previous_key)
                if previous is not None:
                    previous_rows.append({"time": previous_time, "series_code": row["series_code"],
                                          "value": previous.value if hasattr(previous, "value") else previous["value"]})
                stored = existing.get(_observation_identity(row))
                if stored is not None:
                    stored_rows.append({"time": row["time"], "series_code": row["series_code"],
                                        "value": stored.value})
            finding = (detect_previous_month_copy(group, previous_rows)
                       or detect_previous_month_copy(stored_rows, previous_rows))
            if finding:
                copied_months[key] = finding

        candidates, accepted = [], []
        for observation in observations:
            current = existing.get(_observation_identity(observation))
            finding = (copied_months.get((observation["geography_key"], observation["time"]))
                       if (observation.get("raw_data") or {}).get("SourceID") == "SRC_CN_PROV_MONTHLY_REPORT"
                       else None)
            candidate = (_month_copy_candidate(observation, current, finding) if finding
                         else _protected_revision_candidate(observation, current))
            if candidate is None:
                accepted.append(observation["raw_data"])
            else:
                candidates.append(candidate)
        # Metric rows are already expanded. Quarantine each identity separately,
        # so a protected case count does not suppress a new death count/month.
        result = await updater.import_rows(db, accepted, expand_metrics=False)
        audit = dict(getattr(fetched, "source_audit", None) or {})
        if copied_months:
            audit["suspected_previous_month_copies"] = list(copied_months.values())
        audit["revision_candidate_count"] = len(candidates)
        if candidates:
            audit["collection_status"] = audit.get("status", "parsed")
            audit["status"] = "needs_review"
        fetched.source_audit = audit
        await _record_commit_audit(db, task_uuid, audit, candidates)
        if result.inserted_or_updated:
            await mark_dirty(db, line="surveillance", scope_key=updater.country_code, task_uuid=task_uuid)
        return result


def _observation_identity(row):
    return tuple(row[field] for field in ("time", "series_code", "geography_key", "dimension_key"))


def _month_copy_candidate(incoming, current, finding):
    raw = incoming["raw_data"]
    return {
        "identity": {"time": incoming["time"].isoformat(),
                     **{key: incoming[key] for key in ("series_code", "geography_key", "dimension_key")}},
        "old_value": current.value if current is not None else None,
        "new_value": incoming["value"],
        "old_quality_status": current.quality_status if current is not None else None,
        "new_quality_status": incoming["quality_status"],
        "reason": "suspected_previous_month_copy",
        "previous_month": finding["previous_month"],
        "source_url": raw.get("SourceURL"),
        "artifact_sha256": (raw.get("Raw") or {}).get("artifact_sha256"),
        "raw_candidate": raw,
        "current_raw_data": current.raw_data if current is not None else None,
    }


def _protected_revision_candidate(incoming, current):
    """Describe a changed revised/final fact rejected by normal store policy."""
    if current is None or current.quality_status not in {"revised", "final"}:
        return None
    rank = {"raw": 0, "provisional": 1, "validated": 2, "revised": 3, "final": 4}
    metadata = incoming.get("metadata") or {}
    old_rank, new_rank = rank[current.quality_status], rank.get(incoming["quality_status"], 0)
    if (metadata.get("authoritative_revision") is True or new_rank > old_rank
            or (new_rank == old_rank and metadata.get("allow_equal_quality_overwrite") is True)):
        return None
    if all(getattr(current, field) == incoming[field]
           for field in ("value", "unit", "suppressed", "suppression_reason")):
        return None
    raw = incoming["raw_data"]
    return {
        "identity": {"time": incoming["time"].isoformat(),
                     **{key: incoming[key] for key in ("series_code", "geography_key", "dimension_key")}},
        "old_value": current.value, "new_value": incoming["value"],
        "old_quality_status": current.quality_status,
        "new_quality_status": incoming["quality_status"],
        "reason": "protected_observation_requires_review",
        "source_url": raw.get("SourceURL"),
        "artifact_sha256": (raw.get("Raw") or {}).get("artifact_sha256"),
        "raw_candidate": raw,
        "current_raw_data": current.raw_data,
    }


async def _record_commit_audit(db, task_uuid, audit, candidates):
    task = (await db.execute(select(Task).where(Task.task_uuid == task_uuid).with_for_update())).scalar_one()
    task.metadata_ = {**(task.metadata_ or {}), "province_ingestion": {
        "status": audit.get("status", "parsed"), "source_audit": audit,
        "revision_candidates": candidates,
    }}
    # Organize finished before commit. Update its persisted audit in this same
    # transaction so stage inspection agrees with task/automation health.
    stage = (await db.execute(select(RunStage).where(
        RunStage.task_uuid == task_uuid, RunStage.stage == "organize",
    ).order_by(RunStage.attempt.desc()).limit(1).with_for_update())).scalar_one_or_none()
    if stage is not None:
        stage.metrics = {**(stage.metrics or {}), "source_audit": audit}
    if candidates:
        db.add(TaskWorkbook(
            task_id=task.id, entry_type="log", title="Province observations need review",
            content=f"Held {len(candidates)} observations for review; candidate evidence is retained in task metadata.",
            content_type="text", success=True,
            metadata_={"level": "warning", "status": "needs_review", "revision_candidate_count": len(candidates)},
        ))
