"""Queue country reports when a fresh, complete national data period exists."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text

from src.control_plane.schedule_state import schedule_state_repository
from src.core import get_config, get_database, get_logger
from src.core.country_library import get_country_bootstrap_config
from src.core.report_scope import REPORT_PILOT_COUNTRY
from src.core.task_manager import task_manager
from src.domain import Country, Report, Task
from src.domain.report import ReportStatus, ReportType
from src.domain.task import TaskPriority, TaskStatus, TaskType

logger = get_logger(__name__)

_ACTIVE = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)
_FREQUENCY_ORDER = ("DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "ANNUAL")
_MAX_AGE_DAYS = {"DAILY": 4, "WEEKLY": 21, "MONTHLY": 75, "QUARTERLY": 200, "ANNUAL": 548}
_REPORT_TYPE = {frequency: ReportType[frequency] for frequency in _FREQUENCY_ORDER}


@dataclass(frozen=True)
class ReportWindow:
    country_id: int
    country_code: str
    country_name: str
    frequency: str
    latest_observation: date
    period_start: date
    period_end: date
    record_count: int

    @property
    def schedule_key(self) -> str:
        return f"{self.country_code}:{self.frequency}:{self.period_start.isoformat()}"


def normalize_frequency(value: Any) -> str | None:
    frequency = re.sub(r"[^A-Z]", "", str(value or "").upper())
    if frequency in {"YEARLY", "YEAR"}:
        frequency = "ANNUAL"
    if frequency in {"DAY", "WEEK", "MONTH", "QUARTER"}:
        frequency += "LY"
    return frequency if frequency in _FREQUENCY_ORDER else None


def _previous_month(month_start: date) -> date:
    return (month_start - timedelta(days=1)).replace(day=1)


def _previous_quarter(quarter_start: date) -> date:
    first = quarter_start.month - 3
    year = quarter_start.year
    if first < 1:
        first += 12
        year -= 1
    return date(year, first, 1)


def complete_reporting_period(frequency: str, latest: date, today: date) -> tuple[date, date] | None:
    """Return the most recent complete period supported by the latest observation."""

    cadence = normalize_frequency(frequency)
    if cadence is None or latest > today:
        return None
    if cadence == "DAILY":
        end = min(latest, today - timedelta(days=1))
        return end, end
    if cadence == "WEEKLY":
        start = latest - timedelta(days=latest.weekday())
        end = start + timedelta(days=6)
        if end >= today:
            start -= timedelta(days=7)
            end -= timedelta(days=7)
        return (start, end) if end < today else None
    if cadence == "MONTHLY":
        start = latest.replace(day=1)
        current_month = today.replace(day=1)
        if start >= current_month:
            start = _previous_month(start)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return (start, end) if end < today else None
    if cadence == "QUARTERLY":
        start = date(latest.year, 1 + 3 * ((latest.month - 1) // 3), 1)
        current_quarter = date(today.year, 1 + 3 * ((today.month - 1) // 3), 1)
        if start >= current_quarter:
            start = _previous_quarter(start)
        next_quarter = date(start.year + (start.month == 10), (start.month + 3 - 1) % 12 + 1, 1)
        end = next_quarter - timedelta(days=1)
        return (start, end) if end < today else None
    start = date(latest.year, 1, 1)
    if start >= date(today.year, 1, 1):
        start = date(today.year - 1, 1, 1)
    end = date(start.year, 12, 31)
    return (start, end) if end < today else None


def infer_frequency_from_dates(dates: list[date]) -> str | None:
    """Conservatively infer old DiseaseRecord cadence for legacy-only countries."""

    ordered = sorted(set(dates))
    if len(ordered) < 2:
        return None
    gaps = [(right - left).days for left, right in zip(ordered, ordered[1:]) if right > left]
    if not gaps:
        return None
    gaps.sort()
    median = gaps[len(gaps) // 2] if len(gaps) % 2 else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) / 2
    if median >= 300:
        return "ANNUAL"
    if 75 <= median <= 120:
        return "QUARTERLY"
    if 25 <= median <= 35:
        return "MONTHLY"
    if 5 <= median <= 10:
        return "WEEKLY"
    return "DAILY"


def frequency_is_fresh(frequency: str, latest: date, today: date) -> bool:
    cadence = normalize_frequency(frequency)
    return bool(cadence and latest <= today and (today - latest).days <= _MAX_AGE_DAYS[cadence])


class ReportScheduleService:
    KIND = "report"
    JOB_ID = "reports-auto"

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._next_run_at: datetime | None = None
        self._last_started_at: datetime | None = None
        self._last_finished_at: datetime | None = None
        self._last_status = "idle"
        self._last_error: str | None = None
        self._last_task_uuid: str | None = None

    def _config(self):
        return get_config().report

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def maybe_trigger_after_crawl(self, task: Task) -> None:
        """Check report windows immediately after a completed Japan import."""
        data = task.input_data or {}
        country = str(data.get("country_code") or data.get("country") or "").upper()
        if not self._config().schedule_enabled or country != REPORT_PILOT_COUNTRY or data.get("process") is False:
            return
        await self.trigger_job(self.JOB_ID, manual=False)

    async def _load_state(self) -> None:
        state = (await schedule_state_repository.load(self.KIND)).get(self.JOB_ID, {})
        for name in ("next_run_at", "last_started_at", "last_finished_at", "last_status", "last_error", "last_task_uuid"):
            value = state.get(name)
            if name == "next_run_at": self._next_run_at = value
            elif name == "last_started_at": self._last_started_at = value
            elif name == "last_finished_at": self._last_finished_at = value
            elif name == "last_status": self._last_status = value or "idle"
            elif name == "last_error": self._last_error = value
            elif name == "last_task_uuid": self._last_task_uuid = value

    async def _save_state(self) -> None:
        from types import SimpleNamespace
        await schedule_state_repository.save(self.KIND, self.JOB_ID, SimpleNamespace(
            next_run_at=self._next_run_at,
            last_started_at=self._last_started_at,
            last_finished_at=self._last_finished_at,
            last_status=self._last_status,
            last_error=self._last_error,
            last_task_uuid=self._last_task_uuid,
        ))

    async def start(self) -> None:
        cfg = self._config()
        if not cfg.schedule_enabled:
            logger.info("Automatic report scheduler disabled by configuration")
            return
        await self._load_state()
        if self._next_run_at is None:
            self._next_run_at = self._now() + timedelta(seconds=cfg.schedule_poll_interval_seconds)
        await self._save_state()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="globalid-report-scheduler")
        logger.info("Automatic report scheduler started")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                if self._next_run_at is None or self._now() >= self._next_run_at:
                    await self.trigger_job(self.JOB_ID, manual=False)
            except Exception:
                logger.exception("Automatic country report scheduling tick failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=min(60, self._config().schedule_poll_interval_seconds))
            except asyncio.TimeoutError:
                pass

    async def _frequency_rows(self) -> list[dict[str, Any]]:
        query = text("""
            SELECT c.id AS country_id, upper(c.code) AS country_code, c.name_en AS country_name,
                   upper(dss.temporal_granularity) AS cadence, dss.source_system,
                   max(dso.time)::date AS latest_observation
            FROM disease_surveillance_series dss
            JOIN disease_series_observations dso ON dso.series_code = dss.series_code
            JOIN countries c ON c.code = dss.country_code
            WHERE c.is_active IS TRUE
              AND upper(c.code) = :pilot_country
              AND c.code !~ '-'
              AND dso.geography_key = concat('country:', dss.country_code, chr(58), 'national')
              AND dso.suppressed IS FALSE
              AND dso.value IS NOT NULL
              AND dso.quality_status <> 'rejected'
            GROUP BY c.id, c.code, c.name_en, upper(dss.temporal_granularity), dss.source_system
        """)
        async with get_database() as db:
            return [dict(row) for row in (await db.execute(query, {"pilot_country": REPORT_PILOT_COUNTRY})).mappings().all()]

    async def _legacy_dates(self, country_ids: list[int]) -> dict[int, list[date]]:
        if not country_ids:
            return {}
        query = text("""
            SELECT dr.country_id, timezone('UTC', dr.time)::date AS observed_date
            FROM disease_records dr
            WHERE dr.country_id = ANY(:country_ids)
            GROUP BY dr.country_id, timezone('UTC', dr.time)::date
            ORDER BY dr.country_id, observed_date
        """)
        async with get_database() as db:
            rows = (await db.execute(query, {"country_ids": country_ids})).mappings().all()
        result: dict[int, list[date]] = {}
        for row in rows:
            result.setdefault(int(row["country_id"]), []).append(row["observed_date"])
        return result

    async def _country_report_windows(self, today: date | None = None, *, allow_stale: bool = False) -> list[ReportWindow]:
        now_day = today or self._now().date()
        rows = await self._frequency_rows()
        by_country: dict[int, dict[str, Any]] = {}
        inferred_ids: set[int] = set()
        for row in rows:
            code = str(row["country_code"] or "").upper()
            if code != REPORT_PILOT_COUNTRY:
                continue
            public_sources = {
                str(item).strip()
                for item in get_country_bootstrap_config(code).get("public_source_systems", []) or []
                if str(item).strip()
            }
            source_system = str(row.get("source_system") or "")
            if public_sources and source_system not in public_sources:
                continue
            country_id = int(row["country_id"])
            item = by_country.setdefault(country_id, {
                "country_id": country_id,
                "country_code": code,
                "country_name": str(row["country_name"] or code),
                "frequencies": {},
            })
            cadence = normalize_frequency(row.get("cadence"))
            if cadence is None:
                inferred_ids.add(country_id)
                continue
            latest = row["latest_observation"]
            previous = item["frequencies"].get(cadence)
            if previous is None or latest > previous:
                item["frequencies"][cadence] = latest

        # Old DiseaseRecord-only imports do not carry series metadata. Include
        # those countries in the same conservative date-gap fallback.
        # Restrict the fallback scan to countries that have legacy observations.
        async with get_database() as db:
            legacy_countries = (await db.execute(text("""
                SELECT c.id AS country_id, upper(c.code) AS country_code, c.name_en AS country_name
                FROM countries c
                WHERE c.is_active IS TRUE AND c.code !~ '-'
                  AND upper(c.code) = :pilot_country
                  AND EXISTS (SELECT 1 FROM disease_records dr WHERE dr.country_id = c.id)
            """), {"pilot_country": REPORT_PILOT_COUNTRY})).mappings().all()
        for row in legacy_countries:
            if str(row["country_code"]).upper() != REPORT_PILOT_COUNTRY:
                continue
            country_id = int(row["country_id"])
            by_country.setdefault(country_id, {
                "country_id": country_id,
                "country_code": str(row["country_code"]),
                "country_name": str(row["country_name"] or row["country_code"]),
                "frequencies": {},
            })
            inferred_ids.add(country_id)
        # Use record-gap inference only when series metadata does not provide a
        # recognized cadence; legacy event dates can be irregular even when a
        # country's official surveillance feed has a slower fixed cadence.
        inferred_ids.difference_update(
            country_id
            for country_id, item in by_country.items()
            if item["frequencies"]
        )
        legacy_dates = await self._legacy_dates(sorted(inferred_ids))
        windows: list[ReportWindow] = []
        for country_id, item in by_country.items():
            options = [
                (cadence, latest)
                for cadence, latest in item["frequencies"].items()
                if latest <= now_day and (allow_stale or frequency_is_fresh(cadence, latest, now_day))
            ]
            inferred = infer_frequency_from_dates(legacy_dates.get(country_id, []))
            if inferred:
                dates = legacy_dates[country_id]
                latest_legacy = max(dates)
                if latest_legacy <= now_day and (allow_stale or frequency_is_fresh(inferred, latest_legacy, now_day)):
                    options.append((inferred, latest_legacy))
            if not options:
                continue
            cadence, latest = min(options, key=lambda pair: (_FREQUENCY_ORDER.index(pair[0]), -pair[1].toordinal()))
            period = complete_reporting_period(cadence, latest, now_day)
            if period is None:
                continue
            start, end = period
            windows.append(ReportWindow(
                country_id=country_id,
                country_code=item["country_code"],
                country_name=item["country_name"],
                frequency=cadence,
                latest_observation=latest,
                period_start=start,
                period_end=end,
                record_count=0,
            ))
        if not windows:
            return []
        windows = await self._attach_record_counts(windows)
        minimum = int(self._config().schedule_min_records)
        return [window for window in windows if window.record_count >= minimum]

    async def _attach_record_counts(self, windows: list[ReportWindow]) -> list[ReportWindow]:
        values: list[str] = []
        params: dict[str, Any] = {}
        for index, window in enumerate(windows):
            values.append(
                f"(CAST(:country_{index} AS integer), "
                f"CAST(:start_{index} AS timestamptz), "
                f"CAST(:end_{index} AS timestamptz))"
            )
            params[f"country_{index}"] = window.country_id
            params[f"start_{index}"] = datetime.combine(window.period_start, time.min, timezone.utc)
            params[f"end_{index}"] = datetime.combine(window.period_end + timedelta(days=1), time.min, timezone.utc)
        query = text(f"""
            WITH candidates(country_id, period_start, period_end) AS (VALUES {', '.join(values)})
            SELECT candidates.country_id, candidates.period_start, count(dr.time)::int AS record_count
            FROM candidates
            LEFT JOIN disease_records dr
              ON dr.country_id = candidates.country_id
             AND dr.time >= candidates.period_start
             AND dr.time < candidates.period_end
            GROUP BY candidates.country_id, candidates.period_start
        """)
        async with get_database() as db:
            counts = (await db.execute(query, params)).mappings().all()
        by_key = {(int(row["country_id"]), row["period_start"].date()): int(row["record_count"] or 0) for row in counts}
        return [
            ReportWindow(**{**window.__dict__, "record_count": by_key.get((window.country_id, window.period_start), 0)})
            for window in windows
        ]

    async def _existing_report_windows(self, windows: list[ReportWindow]) -> set[tuple[int, str, date]]:
        if not windows:
            return set()
        country_ids = {window.country_id for window in windows}
        frequencies = {_REPORT_TYPE[window.frequency] for window in windows}
        async with get_database() as db:
            reports = (await db.execute(
                select(Report).where(
                    Report.country_id.in_(country_ids),
                    Report.report_type.in_(frequencies),
                    Report.status != ReportStatus.FAILED,
                )
            )).scalars().all()
        return {
            (int(report.country_id), getattr(report.report_type, "value", report.report_type), report.period_start.date())
            for report in reports
        }

    async def _active_scheduled_tasks(self) -> list[Task]:
        async with get_database() as db:
            tasks = (await db.execute(
                select(Task).join(Country, Country.id == Task.country_id).where(
                    Task.task_type == TaskType.GENERATE_REPORT,
                    Task.status.in_(_ACTIVE),
                    func.upper(Country.code) == REPORT_PILOT_COUNTRY,
                ).order_by(Task.created_at.asc())
            )).scalars().all()
        return list(tasks)

    @staticmethod
    def _active_window_keys(tasks: list[Task]) -> set[str]:
        keys = set()
        for task in tasks:
            payload = task.input_data or {}
            if payload.get("report_schedule_key"):
                keys.add(str(payload["report_schedule_key"]))
                continue
            frequency = normalize_frequency(payload.get("report_type"))
            start = str(payload.get("period_start") or "")[:10]
            keys.add(f"JP:{frequency}:{start}" if frequency and start else "JP:*")
        return keys

    async def _enqueue_due_reports(self, *, manual: bool) -> dict[str, Any]:
        cfg = self._config()
        windows = await self._country_report_windows(allow_stale=manual)
        existing = await self._existing_report_windows(windows)
        active = await self._active_scheduled_tasks()
        active_keys = self._active_window_keys(active)
        active_slots = max(0, int(getattr(cfg, "schedule_max_active_tasks", 6)) - len(active))
        limit = min(int(cfg.schedule_max_tasks_per_tick), active_slots)
        ready: list[ReportWindow] = []
        for window in sorted(windows, key=lambda row: (row.period_end, row.country_code)):
            if window.country_code != REPORT_PILOT_COUNTRY:
                continue
            type_key = _REPORT_TYPE[window.frequency].value
            identity = (window.country_id, type_key, window.period_start)
            if identity in existing or window.schedule_key in active_keys or "JP:*" in active_keys:
                continue
            ready.append(window)
        queued: list[dict[str, str]] = []
        for window in ready[:limit]:
            start = datetime.combine(window.period_start, time.min, timezone.utc)
            end = datetime.combine(window.period_end, time.max, timezone.utc)
            task = await task_manager.create_task(
                task_type=TaskType.GENERATE_REPORT,
                task_name=f"{window.frequency.title()} report: {window.country_name} ({window.period_start}–{window.period_end})",
                country_id=window.country_id,
                priority=TaskPriority.NORMAL if manual else TaskPriority.LOW,
                status=TaskStatus.QUEUED,
                description="Japan pilot: deterministic evidence, bounded ECharts selection, AI writing and peer review.",
                input_data={
                    "scheduled_report": True,
                    "report_schedule_key": window.schedule_key,
                    "country": window.country_code,
                    "report_type": window.frequency.lower(),
                    "report_cadence": window.frequency.lower(),
                    "report_pilot": True,
                    "source_data_stale": not frequency_is_fresh(window.frequency, window.latest_observation, self._now().date()),
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "days": (window.period_end - window.period_start).days + 1,
                    "enable_review": True,
                    "send_email": False,
                    "analysis_depth": "deep",
                    "reuse_from_failed": True,
                    "reuse_strategy": "auto",
                },
                tags=["report", "scheduled", window.frequency.lower(), window.country_code.lower()],
            )
            queued.append({"country_code": window.country_code, "frequency": window.frequency, "task_uuid": task.task_uuid})
        return {
            "status": "queued" if queued else "idle",
            "candidate_count": len(windows),
            "ready_count": len(ready),
            "queued": queued,
            "active_count": len(active) + len(queued),
            "manual": manual,
        }

    async def trigger_job(self, job_id: str, *, manual: bool) -> dict[str, Any]:
        if job_id != self.JOB_ID:
            raise ValueError(f"Report schedule not found: {job_id}")
        cfg = self._config()
        async with self._lock:
            self._last_started_at = self._now()
            try:
                # Scheduler and API can run in separate processes. Hold one DB
                # transaction lock through discovery and enqueue to prevent races.
                async with get_database() as lock_db:
                    locked = (await lock_db.execute(text("SELECT pg_try_advisory_xact_lock(74002501)"))).scalar_one()
                    if not locked:
                        self._last_status = "busy"
                        return {"job_id": job_id, "status": "busy", "task_uuid": None, "queued": []}
                    result = await self._enqueue_due_reports(manual=manual)
                self._last_status = result["status"]
                self._last_error = None
                self._last_task_uuid = result["queued"][-1]["task_uuid"] if result["queued"] else self._last_task_uuid
                result["task_uuid"] = result["queued"][-1]["task_uuid"] if result["queued"] else None
                return {"job_id": job_id, **result}
            except Exception as exc:
                self._last_status = "failed"
                self._last_error = str(exc)
                raise
            finally:
                self._last_finished_at = self._now()
                self._next_run_at = self._last_finished_at + timedelta(seconds=cfg.schedule_poll_interval_seconds)
                await self._save_state()

    async def snapshot_async(self) -> dict[str, Any]:
        cfg = self._config()
        await self._load_state()
        active = await self._active_scheduled_tasks()
        return {
            "timezone": "UTC",
            "jobs": [{
                "job_id": self.JOB_ID,
                "name": "Japan Pilot Reports",
                "country_code": REPORT_PILOT_COUNTRY,
                "enabled": bool(cfg.schedule_enabled),
                "timezone": "UTC",
                "interval_minutes": max(1, int(cfg.schedule_poll_interval_seconds // 60)),
                "daily_time": None,
                "next_run_at": self._next_run_at,
                "last_started_at": self._last_started_at,
                "last_finished_at": self._last_finished_at,
                "last_status": self._last_status,
                "last_error": self._last_error,
                "last_task_uuid": self._last_task_uuid,
                "poll_interval_seconds": int(cfg.schedule_poll_interval_seconds),
                "max_tasks_per_tick": int(cfg.schedule_max_tasks_per_tick),
                "max_active_tasks": int(getattr(cfg, "schedule_max_active_tasks", 6)),
                "active_tasks": len(active),
                "frequency_source": "latest public national series metadata with DiseaseRecord fallback",
            }],
        }

    async def workflow_snapshot(self) -> dict[str, Any]:
        """Read-only pilot readiness; manual runs may use dated historical data."""
        from src.generation.report_v4.figures import CATALOG, MAX_FIGURES
        from src.generation.report_v4.models import METHOD_VERSION

        windows = await self._country_report_windows(allow_stale=True)
        existing = await self._existing_report_windows(windows)
        active = await self._active_scheduled_tasks()
        active_keys = self._active_window_keys(active)
        periods = []
        for window in windows:
            identity = (window.country_id, _REPORT_TYPE[window.frequency].value, window.period_start)
            state = "active" if window.schedule_key in active_keys or "JP:*" in active_keys else "existing" if identity in existing else "ready"
            periods.append({
                "country_code": window.country_code, "frequency": window.frequency.lower(),
                "period_start": window.period_start.isoformat(), "period_end": window.period_end.isoformat(),
                "latest_observation": window.latest_observation.isoformat(), "record_count": window.record_count,
                "fresh": frequency_is_fresh(window.frequency, window.latest_observation, self._now().date()),
                "state": state,
            })
        snapshot = await self.snapshot_async()
        return {
            "country_code": REPORT_PILOT_COUNTRY, "method_version": METHOD_VERSION,
            "figure_catalog": list(CATALOG), "max_figures": MAX_FIGURES, "max_figures_per_section": 1,
            "schedule": snapshot["jobs"][0], "periods": periods,
        }


report_schedule_service = ReportScheduleService()

__all__ = [
    "ReportScheduleService",
    "ReportWindow",
    "complete_reporting_period",
    "frequency_is_fresh",
    "infer_frequency_from_dates",
    "normalize_frequency",
    "report_schedule_service",
]
