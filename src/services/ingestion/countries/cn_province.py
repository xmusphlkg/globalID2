"""Province collection with durable HTTP snapshots and strictly offline replay."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from threading import Event
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import requests

from src.data.crawlers.cn_provinces import (
    MonthlyReportLink, ProvinceDataCenterCrawler, ProvinceMonthlyReportCrawler,
    province_configs,
)
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.replay import request_key
from src.services.ingestion.stages import mark_dirty
from src.services.exceptions import TaskCancelledError

SCOPES = frozenset({"cn_province_datacenter", "cn_province_monthly_report"})


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
        response.raise_for_status()
        _check_cancelled(self.cancel_event)
        asyncio.run_coroutine_threadsafe(
            self.writer.write(
                response.content, source_url=request_key(url, kwargs.get("params")),
                kind="province_response",
                metadata={"response_url": response.url,
                          "media_type": response.headers.get("Content-Type", ""),
                          "request_method": method, "request_data": kwargs.get("data"),
                          "encoding": response.encoding},
            ), self.loop,
        ).result()
        return response


class _ReplaySession(requests.Session):
    def __init__(self, artifacts):
        super().__init__()
        self.artifacts = {request_key(a.source_url): a for a in artifacts if a.kind == "province_response" and a.metadata_.get("request_method", "GET") == "GET"}
        self.post_artifacts = [a for a in artifacts if a.kind == "province_response" and a.metadata_.get("request_method") == "POST"]

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
        response.status_code = 200
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
             cancel_event=None, today=None):
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    recent_months = max(1, min(12, int(recent_months)))
    _check_cancelled(cancel_event)
    plan = {"country_code": country_code, "source": source, "reports": [], "datacenter": []}
    if source in {"all", "cn_province_monthly_report"}:
        crawler = ProvinceMonthlyReportCrawler(session=session)
        links = crawler.discover(country_code)
        if start_year is not None:
            links = [link for link in links if link.report_date.year >= start_year]
        if not force:
            links = links[-recent_months:]
        if not links:
            raise RuntimeError(f"No official monthly reports discovered for {country_code}")
        for link in links:
            _check_cancelled(cancel_event)
            # Discovery and attachment enumeration need the existing parser;
            # formal rows are independently rebuilt from raw bytes in organize.
            crawler.fetch(link)
            plan["reports"].append({"date": link.report_date.isoformat(), "title": link.title, "url": link.url})
    if source in {"all", "cn_province_datacenter"}:
        crawler = ProvinceDataCenterCrawler(session=session)
        years = crawler.available_years()
        years = sorted(y for y in years if y < today.year or (y == today.year and today.month > 1))
        years = [y for y in years if y >= start_year] if start_year is not None else years[-1:]
        if not years:
            raise RuntimeError("No published Data Center years match the requested window")
        for year in years:
            last_month = today.month - 1 if year == today.year else 12
            months = range(1 if force else max(1, last_month - recent_months + 1), last_month + 1)
            for disease in crawler.config["disease_ids"]:
                for month in months:
                    _check_cancelled(cancel_event)
                    crawler._fetch_report(year, disease, month)
                    plan["datacenter"].append([year, disease, month])
    return plan


async def collect(writer, *, source, country_code, force=False, start_year=None, recent_months=3):
    validate_scope(country_code, source)
    cancel_event = Event()
    with _RecordingSession(writer, asyncio.get_running_loop(), cancel_event) as session:
        work = asyncio.create_task(asyncio.to_thread(
            _collect, session, country_code=country_code, source=source,
            force=force, start_year=start_year, recent_months=recent_months,
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
    await writer.write(json.dumps(plan).encode(), source_url=f"urn:globalid:province:{country_code}", kind="province_plan")
    return writer.artifacts


def organize(artifacts, *, updater, source, country_code, **options):
    validate_scope(country_code, source)
    plans = [a for a in artifacts if a.kind == "province_plan"]
    if len(plans) != 1:
        raise RuntimeError("Exactly one province collection plan is required")
    plan = json.loads(read_artifact(plans[0]))
    if plan["country_code"] != country_code or plan["source"] != source:
        raise ValueError("Province collection plan scope mismatch")
    rows = []
    with _ReplaySession(artifacts) as session:
        monthly = ProvinceMonthlyReportCrawler(session=session)
        for item in plan["reports"]:
            link = MonthlyReportLink(country_code, date.fromisoformat(item["date"]), item["title"], item["url"])
            rows.extend(monthly.fetch(link).rows)
        center = ProvinceDataCenterCrawler(session=session, request_interval=0)
        for year, disease, month in plan["datacenter"]:
            rows.extend(r for r in center._fetch_report(year, disease, month)
                        if r["GeographyKey"] == f"country:{country_code}:national")
    if not rows:
        raise RuntimeError(f"No usable observations for {country_code}; refusing empty store")
    if any(r["GeographyKey"] != f"country:{country_code}:national" for r in rows):
        raise ValueError("Cross-province observations rejected")
    return SimpleNamespace(rows=rows)


class ProvinceCommitStrategy:
    async def commit(self, db, *, updater, fetched, task_uuid, **kwargs):
        result = await updater.import_rows(db, fetched.rows)
        await mark_dirty(db, line="surveillance", scope_key=updater.country_code, task_uuid=task_uuid)
        return result
