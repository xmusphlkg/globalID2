"""Offline acceptance evidence, filesystem integrity, and read-only DB guards."""

import hashlib
import json
import socket
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from scripts import verify_data_line_acceptance as verifier

TASK = "0a660a5f-4068-4eca-9c3c-01ff92f22f60"
OTHER = "0a660a5f-4068-4eca-9c3c-01ff92f22f61"
START = datetime(2026, 10, 30, 1, tzinfo=timezone.utc)


@pytest.fixture
def snapshot(tmp_path):
    payload = b"time,cases\n2026-10-30,0\n"
    relative = f"data/raw/us/{TASK}/1/source.csv"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    return {
        "task": [
            {
                "task_uuid": TASK,
                "task_type": "CRAWL_DATA",
                "status": "COMPLETED",
                "retry_count": 0,
                "input_data": {"country_code": "US"},
            }
        ],
        "stages": [
            {
                "id": i + 1,
                "task_uuid": TASK,
                "stage": stage,
                "attempt": 1,
                "status": "completed",
                "metrics": {"artifact_count": 1, "artifact_bytes": len(payload)}
                if stage == "collect"
                else {},
                "error": None,
                "started_at": START + timedelta(seconds=i * 2),
                "finished_at": START + timedelta(seconds=i * 2 + 1),
            }
            for i, stage in enumerate(verifier.STAGES)
        ],
        "artifacts": [
            {
                "id": 1,
                "task_uuid": TASK,
                "attempt": 1,
                "stage": "collect",
                "relative_path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "byte_size": len(payload),
                "kind": "csv_page",
            }
        ],
        "dirty": [
            {
                "id": 1,
                "line": "surveillance",
                "scope_key": "US",
                "source_task_uuid": TASK,
                "marked_at": START + timedelta(seconds=5),
                "published_task_uuid": None,
                "published_at": None,
            }
        ],
        "ai_items": [],
    }


def check(snapshot, tmp_path, **kwargs):
    return verifier.verify_snapshot(TASK, snapshot, root=tmp_path, **kwargs)


def failures(report):
    return {row["check"] for row in report.checks if not row["passed"]}


def ai_item(status="succeeded", line="knowledge_brief"):
    return {
        "id": 1,
        "parent_task_uuid": TASK,
        "line": line,
        "status": status,
        "attempt": 1,
        "route_key": "provider/model",
        "available_at": START,
        "lease_expires_at": None,
        "finished_at": START + timedelta(seconds=3),
        "error": None,
    }


def test_valid_snapshot_is_pass_with_evidence(snapshot, tmp_path):
    report = check(snapshot, tmp_path)
    assert report.passed, report.as_dict()
    integrity = next(c for c in report.checks if c["check"] == "artifact.1.integrity")
    assert integrity["evidence"]["actual_sha256"] == snapshot["artifacts"][0]["sha256"]
    assert "payload" not in json.dumps(report.as_dict(), default=str)


@pytest.mark.parametrize("table", ["task", "stages", "artifacts", "dirty"])
def test_missing_evidence_fails(snapshot, tmp_path, table):
    snapshot[table] = []
    assert not check(snapshot, tmp_path).passed


@pytest.mark.parametrize(
    "state", ["queued", "running", "failed", "cancelled", "retrying"]
)
def test_parent_must_be_complete(snapshot, tmp_path, state):
    snapshot["task"][0]["status"] = state
    assert "task.completed" in failures(check(snapshot, tmp_path))


@pytest.mark.parametrize("stage", verifier.STAGES)
@pytest.mark.parametrize("state", ["running", "failed", "skipped"])
def test_latest_stages_must_be_completed(snapshot, tmp_path, stage, state):
    next(row for row in snapshot["stages"] if row["stage"] == stage)["status"] = state
    assert f"latest.{stage}.completed" in failures(check(snapshot, tmp_path))


def test_duplicate_stages_and_fused_cannot_pass(snapshot, tmp_path):
    snapshot["stages"].append(dict(snapshot["stages"][0], id=4))
    snapshot["stages"][0]["metrics"]["fused"] = True
    failed = failures(check(snapshot, tmp_path))
    assert {"attempt.1.stages", "latest.collect.not_fused"} <= failed


def test_stage_order_and_terminal_timestamps_are_validated(snapshot, tmp_path):
    snapshot["stages"][2]["started_at"] = START
    assert "latest.stage_order" in failures(check(snapshot, tmp_path))
    snapshot["stages"][0]["finished_at"] = None
    assert "attempt.1.collect.record" in failures(check(snapshot, tmp_path))


def test_historical_failure_is_preserved_and_retry_count_is_not_attempt(
    snapshot, tmp_path
):
    old = deepcopy(snapshot["stages"])
    old[0].update(status="failed", error="old collection failure", metrics={})
    for row in old[1:]:
        row.update(status="skipped", finished_at=None)
    for row in snapshot["stages"]:
        row.update(attempt=2, id=row["id"] + 3)
    snapshot["stages"] = old + snapshot["stages"]
    artifact = snapshot["artifacts"][0]
    previous = tmp_path / artifact["relative_path"]
    artifact.update(
        attempt=2, relative_path=artifact["relative_path"].replace("/1/", "/2/")
    )
    target = tmp_path / artifact["relative_path"]
    target.parent.mkdir()
    previous.rename(target)
    report = check(snapshot, tmp_path, expected_attempt=2)
    assert report.passed, report.as_dict()
    assert "old collection failure" in json.dumps(report.as_dict(), default=str)
    assert snapshot["task"][0]["retry_count"] == 0
    assert "attempt.expected" in failures(check(snapshot, tmp_path, expected_attempt=3))


def test_gaps_and_orphan_artifacts_fail(snapshot, tmp_path):
    for row in snapshot["stages"]:
        row["attempt"] = 3
    failed = failures(check(snapshot, tmp_path))
    assert {"attempt.history", "artifact.1.owner"} <= failed


@pytest.mark.parametrize(
    "kind",
    [
        "hash",
        "size",
        "missing",
        "directory",
        "traversal",
        "absolute",
        "outside",
        "symlink",
        "other_task",
        "invalid_hash",
        "negative_size",
    ],
)
def test_bad_artifact_fails_without_downloading(snapshot, tmp_path, monkeypatch, kind):
    monkeypatch.setattr(
        socket.socket, "connect", lambda *args: pytest.fail("network access")
    )
    row = snapshot["artifacts"][0]
    path = tmp_path / row["relative_path"]
    if kind == "hash":
        path.write_bytes(b"x" * row["byte_size"])
    elif kind == "size":
        row["byte_size"] += 1
    elif kind == "missing":
        path.unlink()
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind == "traversal":
        row["relative_path"] = "data/raw/../../../private"
    elif kind == "absolute":
        row["relative_path"] = str(path)
    elif kind == "outside":
        row["relative_path"] = f"outside/raw/us/{TASK}/source.csv"
    elif kind == "symlink":
        target = tmp_path / "private"
        path.rename(target)
        path.symlink_to(target)
    elif kind == "other_task":
        row["relative_path"] = row["relative_path"].replace(TASK, OTHER)
    elif kind == "invalid_hash":
        row["sha256"] = "bad"
    elif kind == "negative_size":
        row["byte_size"] = -1
    assert "artifact.1.integrity" in failures(check(snapshot, tmp_path))


def test_duplicate_artifacts_and_metrics_mismatch_fail(snapshot, tmp_path):
    old = deepcopy(snapshot["artifacts"][0])
    old.update(id=2, sha256="0" * 64)
    snapshot["artifacts"].append(old)
    failed = failures(check(snapshot, tmp_path))
    assert {
        "artifacts.unique_paths",
        "artifact.2.integrity",
        "attempt.1.artifact_count",
    } <= failed


def test_tampering_in_old_attempt_is_also_checked(snapshot, tmp_path):
    old_rows = deepcopy(snapshot["stages"])
    old_artifact = deepcopy(snapshot["artifacts"][0])
    for row in snapshot["stages"]:
        row.update(attempt=2, id=row["id"] + 3)
    snapshot["stages"] = old_rows + snapshot["stages"]
    artifact = snapshot["artifacts"][0]
    artifact.update(
        id=2, attempt=2, relative_path=artifact["relative_path"].replace("/1/", "/2/")
    )
    path = tmp_path / artifact["relative_path"]
    path.parent.mkdir()
    path.write_bytes((tmp_path / old_artifact["relative_path"]).read_bytes())
    snapshot["artifacts"].append(old_artifact)
    assert check(snapshot, tmp_path).passed
    (tmp_path / old_artifact["relative_path"]).write_bytes(b"changed old bytes")
    assert failures(check(snapshot, tmp_path)) == {"artifact.1.integrity"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("line", "literature"),
        ("scope_key", "JP"),
        ("source_task_uuid", OTHER),
        ("published_task_uuid", OTHER),
        ("marked_at", START),
    ],
)
def test_invalid_or_stale_dirty_marker_fails(snapshot, tmp_path, field, value):
    snapshot["dirty"][0][field] = value
    assert not check(snapshot, tmp_path).passed


def test_pending_and_published_dirty_are_explicit(snapshot, tmp_path):
    assert check(snapshot, tmp_path, publish_state="pending").passed
    assert not check(snapshot, tmp_path, publish_state="published").passed
    snapshot["dirty"][0].update(
        published_task_uuid=OTHER, published_at=START + timedelta(hours=1)
    )
    assert check(snapshot, tmp_path).passed
    assert check(snapshot, tmp_path, publish_state="published").passed
    assert not check(snapshot, tmp_path, publish_state="pending").passed


@pytest.mark.parametrize(
    "task_type,line,scope",
    [
        ("SYNC_LITERATURE", "literature", "*"),
        ("REFRESH_SITUATION_SOURCES", "situation", "*"),
        ("REFRESH_DISEASE_KNOWLEDGE_SOURCES", "knowledge", "sources"),
        ("UPDATE_DISEASE_KNOWLEDGE", "knowledge", "D001"),
    ],
)
def test_four_lines_require_three_completed_stages(
    snapshot, tmp_path, task_type, line, scope
):
    snapshot["task"][0].update(task_type=task_type, input_data={"disease_id": "D001"})
    snapshot["dirty"][0].update(line=line, scope_key=scope)
    assert check(snapshot, tmp_path).passed


@pytest.fixture(params=["sources", "brief", "source_only"])
def knowledge_snapshot(snapshot, request):
    source_only = request.param == "source_only"
    snapshot["task"][0].update(
        task_type=(
            "REFRESH_DISEASE_KNOWLEDGE_SOURCES"
            if request.param == "sources"
            else "UPDATE_DISEASE_KNOWLEDGE"
        ),
        input_data={"disease_id": "D001", "source_only": source_only},
    )
    snapshot["dirty"][0].update(
        line="knowledge", scope_key="D001" if request.param == "brief" else "sources"
    )
    return snapshot


@pytest.mark.parametrize("stage", verifier.STAGES)
def test_knowledge_missing_any_stage_fails(knowledge_snapshot, tmp_path, stage):
    knowledge_snapshot["stages"] = [
        row for row in knowledge_snapshot["stages"] if row["stage"] != stage
    ]
    assert "attempt.1.stages" in failures(check(knowledge_snapshot, tmp_path))


def test_knowledge_three_completed_stages_pass(knowledge_snapshot, tmp_path):
    report = check(knowledge_snapshot, tmp_path)
    assert report.passed, report.as_dict()


@pytest.mark.parametrize("stage", verifier.STAGES)
def test_knowledge_latest_skipped_stage_fails(knowledge_snapshot, tmp_path, stage):
    next(row for row in knowledge_snapshot["stages"] if row["stage"] == stage)[
        "status"
    ] = "skipped"
    assert f"latest.{stage}.completed" in failures(check(knowledge_snapshot, tmp_path))


def without_raw_artifacts(snapshot):
    snapshot["artifacts"] = []
    collect = next(row for row in snapshot["stages"] if row["stage"] == "collect")
    collect["metrics"] = {"artifact_count": 0, "artifact_bytes": 0}


def test_knowledge_brief_three_completed_stages_without_new_raw_pass(
    snapshot, tmp_path
):
    snapshot["task"][0].update(
        task_type="UPDATE_DISEASE_KNOWLEDGE", input_data={"disease_id": "D001"}
    )
    snapshot["dirty"][0].update(line="knowledge", scope_key="D001")
    without_raw_artifacts(snapshot)
    report = check(snapshot, tmp_path)
    assert report.passed, report.as_dict()


def test_only_knowledge_brief_may_complete_without_raw_artifacts(
    knowledge_snapshot, tmp_path
):
    without_raw_artifacts(knowledge_snapshot)
    is_brief = knowledge_snapshot["dirty"][0]["scope_key"] == "D001"
    assert check(knowledge_snapshot, tmp_path).passed == is_brief


def test_knowledge_failed_attempt_may_keep_skipped_stages(snapshot, tmp_path):
    snapshot["task"][0].update(
        task_type="UPDATE_DISEASE_KNOWLEDGE", input_data={"disease_id": "D001"}
    )
    snapshot["dirty"][0].update(line="knowledge", scope_key="D001")
    without_raw_artifacts(snapshot)
    old = deepcopy(snapshot["stages"])
    old[0].update(status="failed", error="old collection failure")
    for row in old[1:]:
        row.update(status="skipped", finished_at=None)
    for row in snapshot["stages"]:
        row.update(attempt=2, id=row["id"] + 3)
    snapshot["stages"] = old + snapshot["stages"]
    report = check(snapshot, tmp_path, expected_attempt=2)
    assert report.passed, report.as_dict()


@pytest.mark.parametrize(
    "state", ["queued", "leased", "failed", "cancelled", "unknown"]
)
def test_unfinished_or_failed_model_items_fail(snapshot, tmp_path, state):
    snapshot["ai_items"] = [ai_item(state)]
    assert "ai.1.terminal" in failures(check(snapshot, tmp_path))


def test_model_success_and_required_items(snapshot, tmp_path):
    assert "ai.items" in failures(check(snapshot, tmp_path, require_ai_items=True))
    snapshot["ai_items"] = [ai_item()]
    assert check(snapshot, tmp_path, require_ai_items=True).passed
    snapshot["task"][0]["input_data"]["ai_waiting"] = True
    assert "task.ai_waiting" in failures(check(snapshot, tmp_path))


@pytest.mark.parametrize(
    "field,value",
    [
        ("route_key", None),
        ("attempt", 4),
        ("parent_task_uuid", OTHER),
        ("finished_at", None),
        ("lease_expires_at", START),
    ],
)
def test_invalid_model_terminal_evidence(snapshot, tmp_path, field, value):
    snapshot["ai_items"] = [{**ai_item(), field: value}]
    assert "ai.1.terminal" in failures(check(snapshot, tmp_path))


def test_situation_individual_failure_is_tolerated(snapshot, tmp_path):
    snapshot["task"][0]["task_type"] = "REFRESH_SITUATION_SOURCES"
    snapshot["dirty"][0].update(line="situation", scope_key="*")
    snapshot["ai_items"] = [ai_item("failed", "situation_notice")]
    assert check(snapshot, tmp_path).passed
    snapshot["ai_items"][0]["status"] = "cancelled"
    assert not check(snapshot, tmp_path).passed


def write_database(path, snapshot):
    """Only the test fixture creates tables; the verifier never does."""
    with sqlite3.connect(path) as db:
        for name, query in verifier.QUERIES.items():
            columns = query.split("SELECT ", 1)[1].split(" FROM ", 1)[0].split(", ")
            table = query.split(" FROM ", 1)[1].split()[0]
            db.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
            for row in snapshot[name]:
                values = [row.get(c) for c in columns]
                values = [
                    json.dumps(v)
                    if isinstance(v, dict)
                    else v.isoformat()
                    if isinstance(v, datetime)
                    else v
                    for v in values
                ]
                db.execute(
                    f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})",
                    values,
                )


async def test_real_sqlite_read_only_load_filters_uuid_and_rejects_writes(
    snapshot, tmp_path
):
    path = tmp_path / "evidence.sqlite"
    snapshot["task"].append({**snapshot["task"][0], "task_uuid": OTHER})
    write_database(path, snapshot)
    before = path.read_bytes()
    engine = create_async_engine(verifier._database_url(f"sqlite:///{path}"))
    try:
        loaded = await verifier.load_snapshots(engine, [TASK, OTHER])
        assert len(loaded[TASK]["task"]) == 1
        assert len(loaded[TASK]["stages"]) == 3
        assert not loaded[OTHER]["stages"]
        assert check(loaded[TASK], tmp_path).passed
        async with verifier.readonly_connection(engine) as connection:
            with pytest.raises(DBAPIError, match="readonly"):
                await connection.execute(text("DELETE FROM tasks"))
    finally:
        await engine.dispose()
    assert path.read_bytes() == before


async def test_postgres_guard_precedes_every_read_and_rolls_back():
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        execute=AsyncMock(),
        rollback=AsyncMock(),
    )
    manager = AsyncMock()
    manager.__aenter__.return_value = connection
    engine = SimpleNamespace(connect=lambda: manager)
    async with verifier.readonly_connection(engine):
        queries = [str(call.args[0]) for call in connection.execute.await_args_list]
        assert queries[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    connection.rollback.assert_awaited_once()


def test_cli_json_and_text_exit_codes(snapshot, tmp_path, capsys, monkeypatch):
    # Guard against accidental source HTTP and use only an offline DB fixture.
    monkeypatch.setattr(
        socket.socket, "connect", lambda *args: pytest.fail("network access")
    )
    path = tmp_path / "evidence.sqlite"
    write_database(path, snapshot)
    args = [
        "--task-uuid",
        TASK,
        "--database-url",
        f"sqlite:///{path}",
        "--root",
        str(tmp_path),
    ]
    before = path.read_bytes()
    assert verifier.main([*args, "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["passed"] and output["tasks"][0]["task_uuid"] == TASK
    assert verifier.main([*args, "--expected-attempt", "2"]) == 1
    assert "FAIL attempt.expected" in capsys.readouterr().out
    assert verifier.main([*args, "--task-uuid", OTHER, "--json"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert len(output["tasks"]) == 2 and not output["passed"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("defect", [None, "missing_collect", "skipped_organize"])
def test_cli_knowledge_brief_with_no_raw_requires_three_completed_stages(
    snapshot, tmp_path, capsys, monkeypatch, defect
):
    monkeypatch.setattr(
        socket.socket, "connect", lambda *args: pytest.fail("network access")
    )
    snapshot["task"][0].update(
        task_type="UPDATE_DISEASE_KNOWLEDGE", input_data={"disease_id": "D001"}
    )
    snapshot["dirty"][0].update(line="knowledge", scope_key="D001")
    without_raw_artifacts(snapshot)
    if defect == "missing_collect":
        snapshot["stages"] = [
            row for row in snapshot["stages"] if row["stage"] != "collect"
        ]
    elif defect == "skipped_organize":
        snapshot["stages"][1]["status"] = "skipped"
    path = tmp_path / "knowledge-evidence.sqlite"
    write_database(path, snapshot)
    before = path.read_bytes()
    code = verifier.main(
        [
            "--task-uuid",
            TASK,
            "--database-url",
            f"sqlite:///{path}",
            "--root",
            str(tmp_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == (0 if defect is None else 1)
    assert output["passed"] == (defect is None)
    assert path.read_bytes() == before


def test_missing_schema_fails_closed_without_bootstrap(tmp_path, capsys):
    path = tmp_path / "empty.sqlite"
    with sqlite3.connect(path):
        pass
    before = path.read_bytes()
    assert (
        verifier.main(
            ["--task-uuid", TASK, "--database-url", f"sqlite:///{path}", "--json"]
        )
        == 1
    )
    output = json.loads(capsys.readouterr().out)
    assert output["tasks"][0]["checks"][0]["check"] == "snapshot.read"
    assert output["tasks"][0]["checks"][0]["evidence"]["section"] == "task"
    assert path.read_bytes() == before


def test_sqlite_missing_file_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError, match="already exist"):
        verifier._database_url(f"sqlite:///{path}")
    assert not path.exists()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--task-uuid", "not-a-uuid"],
        ["--task-uuid", TASK, "--expected-attempt", "0"],
    ],
)
def test_invalid_cli_input_is_exit_two(args):
    with pytest.raises(SystemExit) as error:
        verifier.main(args)
    assert error.value.code == 2
