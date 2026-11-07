from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from src.services.data_release.realtime_monitor import (
    MonitorThresholds, assemble_report, classify_pending, collect_database_snapshot, evaluate_channel,
    probe_public_manifest, report_text, task_channel_clause, validate_public_url,
)

NOW = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)


def db_state():
    return {"pending": {"count": 0, "scope_count": 0, "oldest_at": None, "oldest_age_seconds": None},
        "publishable_pending": {"count": 0, "scope_count": 0, "oldest_at": None, "oldest_age_seconds": None},
        "blocked_pending": {"count": 0, "scope_count": 0},
        "queue": {"pending": 0, "queued": 0, "running": 0, "retrying": 0}, "active": [],
        "active_sample_truncated": False, "failed_last_24h": 3,
        "latest_terminal": {"status": "completed"},
        "last_completed": {"release_id": "data-current", "manifest_sha256": "a" * 64,
            "completed_at": (NOW - timedelta(days=30)).isoformat(), "age_seconds": 2592000}}


def public_state():
    return {"available": True, "http_status": 200, "release_id": "data-current",
        "manifest_sha256": "a" * 64, "generated_at": (NOW - timedelta(days=30)).isoformat()}


def codes(result):
    return {item["code"]: item["level"] for item in result["issues"]}


def evaluate(db=None, public=None):
    return evaluate_channel(db if db is not None else db_state(), public or public_state(), now=NOW, thresholds=MonitorThresholds())


def test_idle_old_data_and_recovered_failures_do_not_alert():
    assert evaluate()["status"] == "ok"


def test_intentional_pause_reports_backlog_without_false_automatic_delay_alerts():
    db = db_state()
    db["publishable_pending"].update(count=7, oldest_age_seconds=3600)
    db["queue"]["retrying"] = 1
    db["active"] = [{"status": "retrying", "task_uuid": "retry",
        "next_attempt_at": (NOW - timedelta(hours=1)).isoformat()}]
    result = evaluate_channel(db, public_state(), now=NOW, thresholds=MonitorThresholds(),
        control={"paused": True, "updated_at": NOW.isoformat(), "error_code": None})
    assert codes(result) == {"automatic_publication_paused": "warning"}
    assert result["database"]["publishable_pending"]["count"] == 7


@pytest.mark.parametrize("fault", ["public", "running", "control"])
def test_pause_never_hides_integrity_running_or_control_failures(fault):
    db, public = db_state(), public_state()
    control = {"paused": True, "updated_at": None, "error_code": None}
    if fault == "public":
        public.update(manifest_sha256="b" * 64)
    elif fault == "running":
        db["active"] = [{"status": "running", "age_seconds": 3600, "task_uuid": "run"}]
    else:
        control["error_code"] = "control_state_invalid"
    result = evaluate_channel(db, public, now=NOW, thresholds=MonitorThresholds(), control=control)
    assert result["status"] == "critical"


def test_report_applies_control_to_only_its_channel():
    report = assemble_report({line: db_state() for line in ("surveillance", "situation")},
        {line: public_state() for line in ("surveillance", "situation")},
        now=NOW, thresholds=MonitorThresholds(), controls={"situation": {"paused": True}})
    assert report["channels"]["situation"]["status"] == "warning"
    assert report["channels"]["surveillance"]["status"] == "ok"


def test_situation_monitor_matches_global_snapshot_selection_contract():
    groups = [("*", 2, NOW, 0), ("legacy-source", 3, NOW, 0)]
    result = classify_pending(groups, [], channel="situation", now=NOW)
    assert result["pending"]["count"] == 5
    assert result["publishable_pending"]["count"] == 2
    assert result["blocked_pending"]["count"] == 3


@pytest.mark.parametrize("seconds,expected", [(119, "ok"), (120, "warning"), (599, "warning"), (600, "critical")])
def test_pending_age_thresholds(seconds, expected):
    db = db_state()
    db["pending"].update(count=7, oldest_age_seconds=seconds)
    db["publishable_pending"].update(count=7, oldest_age_seconds=seconds)
    assert evaluate(db)["status"] == expected


def test_running_failure_and_retry_states():
    db = db_state()
    db["latest_terminal"] = {"status": "failed"}
    assert codes(evaluate(db)) == {"latest_release_unsuccessful": "critical"}
    db["queue"]["retrying"] = 1
    db["active"] = [{"status": "retrying", "task_uuid": "retry", "next_attempt_at": (NOW + timedelta(seconds=300)).isoformat()}]
    assert codes(evaluate(db)) == {"latest_release_unsuccessful": "warning"}
    db["active"][0]["next_attempt_at"] = (NOW - timedelta(seconds=700)).isoformat()
    assert codes(evaluate(db))["retry_overdue"] == "critical"
    db["active"][0]["next_attempt_at"] = None
    assert "retry_schedule_missing" in codes(evaluate(db))
    db["active"] = [{"status": "running", "task_uuid": "running", "age_seconds": 1801}]
    assert codes(evaluate(db))["running_task_delayed"] == "critical"
    db["active"] = [{"status": "queued", "task_uuid": "queued", "age_seconds": 601}]
    assert codes(evaluate(db))["queued_task_delayed"] == "critical"


def test_completed_hash_mismatch_and_active_pointer_before_ack():
    db, public = db_state(), public_state()
    public.update(release_id="data-new", manifest_sha256="b" * 64)
    assert codes(evaluate(db, public)) == {"public_completed_version_mismatch": "critical"}
    db["last_completed"]["age_seconds"] = 10
    assert codes(evaluate(db, public)) == {"public_completed_version_mismatch": "warning"}
    db["queue"]["running"] = 1
    db["active"] = [{"task_uuid": "new", "status": "running", "age_seconds": 20}]
    assert evaluate(db, public)["status"] == "ok"


def test_database_failure_does_not_hide_public_failure():
    result = assemble_report(None, {"situation": {"available": False, "error_code": "http_timeout"}},
        now=NOW, thresholds=MonitorThresholds(), database_error="database_timeout")
    assert result["status"] == "critical"
    assert codes(result["channels"]["situation"]) == {"database_unavailable": "critical", "http_timeout": "critical"}
    assert "database=unavailable" in report_text(result)


def manifest_fixture(payload=b'{"sample":1}'):
    sha = hashlib.sha256(payload).hexdigest()
    body = json.dumps({"schema_version": 1, "release_id": "situation-current", "generated_at": NOW.isoformat(),
        "files": {"map-data.json": {"url": f"objects/{sha}.json", "sha256": sha, "bytes": len(payload)}}}).encode()
    return payload, body


async def probe(handler, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await probe_public_manifest(client, "https://public.example/site-data/situation/current.json", **kwargs)


@pytest.mark.asyncio
async def test_http_decodes_gzip_and_checks_actual_manifest_sha_and_object():
    payload, body = manifest_fixture()
    requests = []
    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/current.json"):
            return httpx.Response(200, content=body, headers={"Cache-Control": "no-store"})
        return httpx.Response(200, content=gzip.compress(payload), headers={"Content-Encoding": "gzip"})
    result = await probe(handler)
    assert result["available"] and result["sample_verified"]
    assert result["manifest_sha256"] == hashlib.sha256(body).hexdigest()
    assert requests[1].startswith("/site-data/situation/objects/")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connection", "503"])
async def test_transient_http_failure_is_rechecked_and_reported_when_recovered(failure):
    payload, body = manifest_fixture()
    requests = []
    def handler(request):
        requests.append(request.url.path)
        if len(requests) == 1:
            if failure == "503":
                return httpx.Response(503)
            raise httpx.ConnectError("secret-token=do-not-print", request=request)
        if request.url.path.endswith("/current.json"):
            return httpx.Response(200, content=body)
        return httpx.Response(200, content=payload)
    result = await probe(handler)
    assert result["available"] and result["sample_verified"]
    assert result["attempts"] == 2 and len(requests) == 3
    assert result["transient_errors"] == ["http_status" if failure == "503" else "http_unreachable"]
    assert "secret" not in json.dumps(result)
    database = db_state()
    database["last_completed"].update(release_id=result["release_id"], manifest_sha256=result["manifest_sha256"])
    report = evaluate_channel(database, result, now=NOW, thresholds=MonitorThresholds())
    assert codes(report) == {"transient_http_recovered": "warning"}


@pytest.mark.asyncio
async def test_repeated_network_failure_remains_critical():
    requests = []
    def handler(request):
        requests.append(request.url.path)
        raise httpx.ConnectError("private error", request=request)
    result = await probe(handler)
    assert result["attempts"] == len(requests) == 2
    assert result["error_code"] == "http_unreachable"
    assert evaluate_channel(db_state(), result, now=NOW, thresholds=MonitorThresholds())["status"] == "critical"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["404", "corrupt"])
async def test_permanent_status_and_checksum_errors_are_not_retried(failure):
    payload, body = manifest_fixture()
    requests = []
    def handler(request):
        requests.append(request.url.path)
        if failure == "404":
            return httpx.Response(404)
        return httpx.Response(200, content=body if request.url.path.endswith("/current.json") else b"bad")
    result = await probe(handler)
    assert result["attempts"] == 1
    assert len(requests) == (1 if failure == "404" else 2)
    assert result["error_code"] == ("http_status" if failure == "404" else "object_checksum_mismatch")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [("404", "http_status"), ("invalid", "invalid_manifest"),
    ("corrupt", "object_checksum_mismatch"), ("large", "response_too_large"), ("unreachable", "http_unreachable")])
async def test_public_probe_safe_failure_codes(failure, expected):
    payload, body = manifest_fixture()
    def handler(request):
        if failure == "unreachable":
            raise httpx.ConnectError("secret-token=do-not-print", request=request)
        if request.url.path.endswith("/current.json"):
            if failure == "404": return httpx.Response(404, text="secret-token=do-not-print")
            if failure == "invalid": return httpx.Response(200, text="not json")
            return httpx.Response(200, content=body)
        return httpx.Response(200, content=b"bad" if failure == "corrupt" else b"x" * 100)
    result = await probe(handler, object_limit_bytes=50)
    assert result["error_code"] == expected
    assert "secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_probe_timeout_bounds_entire_manifest_and_object_operation():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, content=b"{}")
    assert (await probe(handler, timeout_seconds=0.01))["error_code"] == "http_timeout"


@pytest.mark.parametrize("url", ["https://user:password@example.com/current.json", "https://example.com/current.json?token=secret", "file:///etc/passwd"])
def test_public_urls_reject_credentials_and_query_strings(url):
    with pytest.raises(ValueError): validate_public_url(url)


def test_channel_query_retains_legacy_surveillance_fallback():
    sql = " ".join(str(item.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})) for item in task_channel_clause("situation"))
    assert "data_only" in sql and "data_line" in sql and "coalesce" in sql
    assert "surveillance" in sql and "situation" in sql


@pytest.mark.asyncio
async def test_database_collection_only_selects_and_redacts_terminal_payloads():
    class Result:
        def __init__(self, value): self.value = value
        def one(self): return self.value
        def all(self): return self.value
        def first(self): return self.value
        def scalar_one(self): return self.value
    values = iter([[("JP", True)], [("JP", 2, NOW - timedelta(seconds=200), 1)], [("retrying", 1)],
        [("task-retry", "retrying", NOW, None, "not-a-timestamp", 2)], 1,
        ("completed", NOW, NOW, "data-safe", "a" * 64), ("failed", NOW, NOW, "secret token", "secret")])
    statements = []
    async def execute(statement):
        statements.append(str(statement))
        return Result(next(values))
    result = await collect_database_snapshot(SimpleNamespace(execute=execute), now=NOW, channels=("surveillance",))
    assert len(statements) == 7 and all(item.startswith("SELECT") for item in statements)
    assert result["surveillance"]["pending"]["oldest_age_seconds"] == 200
    assert result["surveillance"]["active"][0]["next_attempt_at"] is None
    assert result["surveillance"]["latest_terminal"]["release_id"] is None
    assert "secret" not in json.dumps(result)


def test_pending_visibility_and_parent_expansion_match_release_planner(monkeypatch):
    from src.services.data_release import realtime_monitor as monitor
    monkeypatch.setattr(monitor, "get_country_bootstrap_config", lambda code: {"public_release_enabled": code != "ZZ"})
    countries = [("JP", True), ("JP-25", True), ("JP-26", True), ("US", False), ("ZZ", True)]
    groups = [("JP", 2, NOW - timedelta(seconds=30), 0),
        ("US", 1, NOW - timedelta(days=2), 0), ("ZZ", 3, NOW - timedelta(days=1), 1),
        ("MISSING", 1, NOW - timedelta(days=3), 0)]
    result = classify_pending(groups, countries, channel="surveillance", now=NOW)
    assert result["pending"]["count"] == 7
    assert result["publishable_pending"]["count"] == 2
    assert result["publishable_pending"]["oldest_age_seconds"] == 30
    assert result["publishable_pending"]["expanded_country_count"] == 3
    assert result["blocked_pending"]["count"] == 5
    assert result["blocked_pending"]["scopes"] == ["US", "ZZ", "MISSING"]
    precise = classify_pending([("JP", 2, NOW, 1)], countries, channel="surveillance", now=NOW)
    assert precise["publishable_pending"]["expanded_country_count"] == 1
    db = db_state()
    db.update(result)
    assert codes(evaluate(db)) == {"pending_scopes_blocked": "warning"}


@pytest.mark.parametrize("values", [{"pending_warning_seconds": float("nan")}, {"pending_critical_seconds": -1}, {"running_warning_seconds": 2000}])
def test_thresholds_reject_invalid_numbers_and_order(values):
    with pytest.raises(ValueError): MonitorThresholds(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("timeout", "database_timeout"), ("error", "database_unavailable")])
async def test_cli_db_failure_still_reports_http_and_does_not_print_exception(mode, expected):
    from scripts import monitor_realtime_data as cli
    args = cli.parser().parse_args(["--channel", "surveillance", "--origin", "https://public.example", "--timeout-seconds", "0.01"])
    async def reader(**kwargs):
        if mode == "timeout": await asyncio.sleep(1)
        raise RuntimeError("postgresql://password:secret@private-host")
    payload, body = manifest_fixture()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
        content=body if request.url.path.endswith("current.json") else payload))) as client:
        report = await cli.check_once(args, MonitorThresholds(), client=client, database_reader=reader)
    assert report["database_error_code"] == expected
    assert report["channels"]["surveillance"]["public"]["available"]
    assert "password" not in json.dumps(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_cli_database_transaction_is_read_only_and_always_rolled_back(monkeypatch, failure):
    from scripts import monitor_realtime_data as cli
    calls = []
    class Database:
        async def execute(self, statement): calls.append(str(statement))
        async def rollback(self): calls.append("ROLLBACK")
    @asynccontextmanager
    async def context(): yield Database()
    async def collect(*args, **kwargs):
        if failure: raise RuntimeError("internal detail")
        return {}
    monkeypatch.setattr(cli, "get_engine", lambda: SimpleNamespace(echo=True))
    monkeypatch.setattr(cli, "get_session_maker", lambda: context)
    monkeypatch.setattr(cli, "collect_database_snapshot", collect)
    if failure:
        with pytest.raises(RuntimeError): await cli.database_snapshot(now=NOW, channels=("situation",))
    else:
        assert await cli.database_snapshot(now=NOW, channels=("situation",)) == {}
    assert calls == ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY", "ROLLBACK"]


def test_archive_ndjson_and_latest(tmp_path):
    from scripts.monitor_realtime_data import archive_report
    report = assemble_report({"surveillance": db_state()}, {"surveillance": public_state()}, now=NOW, thresholds=MonitorThresholds())
    archive_report(tmp_path, report)
    archive_report(tmp_path, report)
    assert json.loads((tmp_path / "latest.json").read_text()) == report
    assert len((tmp_path / "2026-11-07.ndjson").read_text().splitlines()) == 2


@pytest.mark.parametrize("status,expected", [("ok", 0), ("warning", 1), ("critical", 2)])
@pytest.mark.asyncio
async def test_observe_exit_states(status, expected, monkeypatch, capsys):
    from scripts import monitor_realtime_data as cli
    async def check(*args, **kwargs): return {"status": status, "checked_at": NOW.isoformat(), "channels": {}}
    monkeypatch.setattr(cli, "check_once", check)
    args = cli.parser().parse_args([])
    assert await cli.observe(args, MonitorThresholds()) == expected
    assert json.loads(capsys.readouterr().out)["status"] == status


@pytest.mark.asyncio
async def test_bounded_observation_preserves_early_failure_and_interval(monkeypatch, capsys):
    from scripts import monitor_realtime_data as cli
    statuses, pauses = iter(("critical", "warning", "ok")), []
    async def check(*args, **kwargs):
        return {"status": next(statuses), "checked_at": NOW.isoformat(), "channels": {}}
    async def sleep(seconds): pauses.append(seconds)
    monkeypatch.setattr(cli, "check_once", check)
    monkeypatch.setattr(cli.asyncio, "sleep", sleep)
    args = cli.parser().parse_args(["--samples", "3", "--interval-seconds", "15"])
    assert await cli.observe(args, MonitorThresholds()) == 2
    assert pauses == [15, 15]
    assert [json.loads(line)["status"] for line in capsys.readouterr().out.splitlines()] == ["critical", "warning", "ok"]


def test_invalid_cli_options_return_sanitized_error(capsys):
    from scripts import monitor_realtime_data as cli
    assert cli.main(["--timeout-seconds", "nan"]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "monitor_check_failed"


@pytest.mark.parametrize("status,flag,expected", [
    ("ok", True, 0), ("warning", False, 1), ("warning", True, 0), ("critical", True, 2),
])
def test_warning_exit_option_preserves_report_and_critical_failure(status, flag, expected, monkeypatch, capsys):
    from scripts import monitor_realtime_data as cli
    async def check(*args, **kwargs):
        return {"status": status, "checked_at": NOW.isoformat(), "channels": {}}
    async def dispose(): pass
    monkeypatch.setattr(cli, "check_once", check)
    monkeypatch.setattr(cli, "dispose_database", dispose)
    assert cli.main(["--warning-exit-zero"] if flag else []) == expected
    assert json.loads(capsys.readouterr().out)["status"] == status
