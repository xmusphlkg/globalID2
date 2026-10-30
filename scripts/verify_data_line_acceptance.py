#!/usr/bin/env python3
"""Read-only migration acceptance for explicitly selected task UUIDs.

Usage (from any directory)::

    venv/bin/python scripts/verify_data_line_acceptance.py --task-uuid UUID --json
    venv/bin/python scripts/verify_data_line_acceptance.py --task-uuid UUID --expected-attempt 2

Uses DATABASE_URL (or the repository .env), never application initialization,
workers, crawlers, retry, publishing, or source downloads. PostgreSQL reads use
one REPEATABLE READ, READ ONLY transaction; SQLite uses mode=ro and query_only.
Only existing local artifact files are streamed. Exit codes: 0 PASS, 1 FAIL,
2 invalid CLI input. Missing tables/evidence fail closed.

This verifies recorded evidence, not historical absence of network calls,
projection changes, or atomicity of the original store transaction. The latest
successful attempt must contain exactly one completed collect, organize, and
store stage. Knowledge briefs may collect existing database inputs without
requesting sources and therefore need no new raw artifacts. Other completed
collect stages need artifacts, including for an otherwise successful no-op.
AI attempts are item retries, not parent stage attempts. Historical failed
stages are evidence, not a failure of a later successful attempt.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import stat
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("collect", "organize", "store")
TASK_LINES = {
    "crawl_data": "surveillance",
    "sync_literature": "literature",
    "refresh_situation_sources": "situation",
    "refresh_disease_knowledge_sources": "knowledge",
    "update_disease_knowledge": "knowledge",
}
# Explicit columns exclude model prompts/results and unrelated task metadata.
QUERIES = {
    "task": "SELECT task_uuid, task_type, status, retry_count, input_data FROM tasks WHERE task_uuid = :task_uuid",
    "stages": "SELECT id, task_uuid, stage, attempt, status, metrics, error, started_at, finished_at FROM run_stages WHERE task_uuid = :task_uuid ORDER BY attempt, id",
    "artifacts": "SELECT id, task_uuid, attempt, stage, relative_path, sha256, byte_size, kind FROM source_artifacts WHERE task_uuid = :task_uuid ORDER BY attempt, id",
    "dirty": "SELECT id, line, scope_key, source_task_uuid, marked_at, published_task_uuid, published_at FROM publish_dirty WHERE source_task_uuid = :task_uuid ORDER BY id",
    "ai_items": "SELECT id, parent_task_uuid, line, status, attempt, route_key, available_at, lease_expires_at, finished_at, error FROM ai_work_items WHERE parent_task_uuid = :task_uuid ORDER BY id",
}


class EvidenceReadError(RuntimeError):
    """Identify unavailable evidence without exposing driver error contents."""

    def __init__(self, task_uuid: str, section: str, error: Exception):
        super().__init__(f"Cannot read {section} evidence")
        self.evidence = {
            "task_uuid": task_uuid,
            "section": section,
            "error_type": type(error).__name__,
        }


@dataclass
class Report:
    task_uuid: str
    checks: list[dict[str, Any]] = field(default_factory=list)

    def check(self, name: str, passed: bool, evidence: Any) -> None:
        self.checks.append(
            {"check": name, "passed": bool(passed), "evidence": evidence}
        )

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check["passed"] for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_uuid": self.task_uuid,
            "passed": self.passed,
            "checks": self.checks,
        }


def _object(value: Any) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _name(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


def _time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def verify_artifact(row: dict, root: Path, task_uuid: str) -> tuple[bool, dict]:
    """Validate containment before reading; never follow a source URL."""
    evidence = dict(row)
    try:
        relative = Path(row["relative_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact path must be relative without traversal")
        if relative.parts[:2] != ("data", "raw") or len(relative.parts) < 5:
            raise ValueError("artifact path must be data/raw/<scope>/<task_uuid>/...")
        if relative.parts[3] != task_uuid:
            raise ValueError("artifact path belongs to a different task")
        root = root.resolve()
        raw = root / "data" / "raw"
        path = root / relative
        # Reject symlinks even when they currently resolve within data/raw.
        for parent in (path, *path.parents):
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError("artifact path contains a symlink")
        if not path.resolve().is_relative_to(raw.resolve()):
            raise ValueError("artifact path escapes data/raw")
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("artifact is not a regular file")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(row.get("sha256", ""))):
            raise ValueError("invalid recorded SHA-256")
        if type(row.get("byte_size")) is not int or row["byte_size"] < 0:
            raise ValueError("invalid recorded byte_size")
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        evidence.update(actual_sha256=digest.hexdigest(), actual_byte_size=size)
        passed = (
            size == row["byte_size"] and digest.hexdigest() == row["sha256"].lower()
        )
        return passed, evidence
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        evidence["error"] = str(exc)
        return False, evidence


def verify_snapshot(
    task_uuid: str,
    snapshot: dict,
    *,
    root: Path = ROOT,
    expected_attempt: int | None = None,
    publish_state: str = "either",
    require_ai_items: bool = False,
) -> Report:
    report = Report(task_uuid)
    tasks = snapshot.get("task", [])
    report.check("task.exists", len(tasks) == 1, {"rows": len(tasks)})
    if len(tasks) != 1:
        return report
    task = tasks[0]
    task_type, status = _name(task["task_type"]), _name(task["status"])
    line = TASK_LINES.get(task_type)
    inputs = _object(task.get("input_data"))
    report.check(
        "task.identity", task.get("task_uuid") == task_uuid, task.get("task_uuid")
    )
    report.check("task.line", line is not None, {"task_type": task_type, "line": line})
    report.check(
        "task.completed",
        status == "completed",
        {"status": status, "retry_count": task.get("retry_count")},
    )
    report.check(
        "task.ai_waiting",
        not inputs.get("ai_waiting"),
        {"ai_waiting": inputs.get("ai_waiting", False)},
    )

    history = defaultdict(list)
    for row in snapshot["stages"]:
        valid = _positive_int(row.get("attempt")) and row.get("task_uuid") == task_uuid
        report.check(f"stage.{row['id']}.identity", valid, row)
        if valid:
            history[row["attempt"]].append(row)
    attempts = sorted(history)
    latest = max(attempts, default=0)
    report.check(
        "attempt.history",
        bool(attempts)
        and all(number == attempt for number, attempt in enumerate(attempts, 1)),
        {"attempts": attempts, "latest": latest},
    )
    if expected_attempt is not None:
        report.check(
            "attempt.expected",
            latest == expected_attempt,
            {"expected": expected_attempt, "actual": latest},
        )

    latest_stages = {}
    for attempt, rows in sorted(history.items()):
        counts = Counter(row["stage"] for row in rows)
        report.check(
            f"attempt.{attempt}.stages",
            set(counts) == set(STAGES) and all(n == 1 for n in counts.values()),
            dict(counts),
        )
        for row in rows:
            name, state = row["stage"], row["status"]
            metrics = _object(row.get("metrics"))
            started, finished = (
                _time(row.get("started_at")),
                _time(row.get("finished_at")),
            )
            valid_time = started is not None and (
                finished is None or finished >= started
            )
            if state in {"completed", "failed"}:
                valid_time = valid_time and finished is not None
            report.check(
                f"attempt.{attempt}.{name}.record",
                state in {"completed", "failed", "skipped"} and valid_time,
                row,
            )
            if attempt == latest:
                latest_stages[name] = row
                report.check(
                    f"latest.{name}.completed",
                    state == "completed",
                    {"status": state},
                )
                report.check(
                    f"latest.{name}.not_fused",
                    not metrics.get("fused"),
                    {"fused": metrics.get("fused", False)},
                )

    previous_finish = None
    ordered = True
    for name in STAGES:
        row = latest_stages.get(name)
        if row is None or row["status"] == "skipped":
            continue
        started, finished = _time(row.get("started_at")), _time(row.get("finished_at"))
        if previous_finish is not None and (
            started is None or started < previous_finish
        ):
            ordered = False
        previous_finish = finished
    report.check(
        "latest.stage_order",
        ordered,
        {
            name: {key: row.get(key) for key in ("started_at", "finished_at")}
            for name, row in latest_stages.items()
        },
    )

    artifact_counts, artifact_bytes = Counter(), Counter()
    paths = Counter(row.get("relative_path") for row in snapshot["artifacts"])
    report.check(
        "artifacts.unique_paths",
        all(n == 1 for n in paths.values()),
        {"duplicates": [p for p, n in paths.items() if n > 1]},
    )
    for row in snapshot["artifacts"]:
        attempt = row.get("attempt")
        owner = (
            row.get("task_uuid") == task_uuid
            and attempt in history
            and row.get("stage") == "collect"
            and any(stage["stage"] == "collect" for stage in history[attempt])
        )
        report.check(
            f"artifact.{row['id']}.owner",
            owner,
            {
                "task_uuid": row.get("task_uuid"),
                "attempt": attempt,
                "stage": row.get("stage"),
            },
        )
        passed, evidence = verify_artifact(row, root, task_uuid)
        report.check(f"artifact.{row['id']}.integrity", passed, evidence)
        artifact_counts[attempt] += 1
        if type(row.get("byte_size")) is int:
            artifact_bytes[attempt] += row["byte_size"]
    collect = latest_stages.get("collect", {})
    source_free_brief = task_type == "update_disease_knowledge" and not inputs.get(
        "source_only"
    )
    report.check(
        "artifacts.latest",
        collect.get("status") != "completed"
        or artifact_counts[latest] > 0
        or source_free_brief,
        {
            "attempt": latest,
            "count": artifact_counts[latest],
            "collect_status": collect.get("status"),
            "source_free_brief": source_free_brief,
        },
    )
    for attempt, rows in history.items():
        for row in rows:
            if row["stage"] != "collect":
                continue
            metrics = _object(row.get("metrics"))
            for key, actual in (
                ("artifact_count", artifact_counts[attempt]),
                ("artifact_bytes", artifact_bytes[attempt]),
            ):
                if key in metrics:
                    report.check(
                        f"attempt.{attempt}.{key}",
                        metrics[key] == actual,
                        {"recorded": metrics[key], "actual": actual},
                    )

    store_start = _time(latest_stages.get("store", {}).get("started_at"))
    current_dirty = []
    scopes = set()
    if line == "surveillance" and inputs.get("country_code"):
        scopes = {str(inputs["country_code"]).upper()}
    elif line in {"literature", "situation"}:
        scopes = {"*"}
    elif line == "knowledge":
        if task_type == "refresh_disease_knowledge_sources" or inputs.get(
            "source_only"
        ):
            scopes = {"sources"}
        else:
            disease = inputs.get("disease_id") or inputs.get("disease")
            scopes = (
                {str(disease)}
                if disease
                else set(map(str, inputs.get("disease_ids") or []))
            )
    for row in snapshot["dirty"]:
        marked, published = _time(row.get("marked_at")), _time(row.get("published_at"))
        owner = (
            row.get("source_task_uuid") == task_uuid
            and row.get("line") == line
            and bool(row.get("scope_key"))
        )
        paired = (published is not None) == bool(row.get("published_task_uuid"))
        valid_time = marked is not None and (published is None or published >= marked)
        report.check(
            f"publish_dirty.{row['id']}.record", owner and paired and valid_time, row
        )
        if store_start and marked and marked >= store_start and owner:
            current_dirty.append(row)
    actual_scopes = {row["scope_key"] for row in current_dirty}
    report.check(
        "publish_dirty.latest",
        bool(current_dirty) and scopes <= actual_scopes,
        {
            "ids": [r["id"] for r in current_dirty],
            "expected_scopes": sorted(scopes),
            "actual_scopes": sorted(actual_scopes),
            "store_started_at": store_start,
        },
    )
    if publish_state != "either":
        report.check(
            "publish_dirty.state",
            bool(current_dirty)
            and all(
                (row.get("published_at") is not None) == (publish_state == "published")
                for row in current_dirty
            ),
            {"expected": publish_state, "rows": current_dirty},
        )

    items = snapshot["ai_items"]
    report.check(
        "ai.items",
        bool(items) or not require_ai_items,
        {
            "count": len(items),
            "required": require_ai_items,
            "statuses": dict(Counter(row["status"] for row in items)),
        },
    )
    for row in items:
        terminal = row["status"] in {"succeeded", "failed", "cancelled"}
        # Section D2 tolerates individual extraction failures, not cancellation.
        tolerated = (
            line == "situation"
            and row["line"] == "situation_notice"
            and row["status"] == "failed"
        )
        valid = (
            row.get("parent_task_uuid") == task_uuid
            and _positive_int(row.get("attempt"))
            and row["attempt"] <= 3
        )
        valid = (
            valid
            and terminal
            and row.get("finished_at") is not None
            and row.get("lease_expires_at") is None
        )
        valid = valid and (row["status"] == "succeeded" or tolerated)
        if row["status"] == "succeeded":
            valid = valid and bool(row.get("route_key"))
        report.check(
            f"ai.{row['id']}.terminal", valid, {**row, "failure_tolerated": tolerated}
        )
    return report


@asynccontextmanager
async def readonly_connection(engine):
    """Never commit, autoflush, initialize a schema, or take write locks."""
    async with engine.connect() as connection:
        try:
            if connection.dialect.name == "postgresql":
                await connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                await connection.execute(text("SET LOCAL statement_timeout = '15s'"))
            elif connection.dialect.name == "sqlite":
                await connection.execute(text("PRAGMA query_only = ON"))
                await connection.execute(text("BEGIN"))
            else:
                raise ValueError(
                    "Only PostgreSQL and SQLite support this verifier's read-only guard"
                )
            yield connection
        finally:
            await connection.rollback()


async def load_snapshots(engine, task_uuids: list[str]) -> dict[str, dict]:
    snapshots = {}
    async with readonly_connection(engine) as connection:
        for task_uuid in task_uuids:
            snapshot = {}
            for name, query in QUERIES.items():
                try:
                    result = await connection.execute(
                        text(query), {"task_uuid": task_uuid}
                    )
                except Exception as exc:
                    raise EvidenceReadError(task_uuid, name, exc) from exc
                snapshot[name] = [dict(row) for row in result.mappings()]
            snapshots[task_uuid] = snapshot
    return snapshots


def _database_url(value: str | None):
    if not value:
        from dotenv import dotenv_values

        value = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get(
            "DATABASE_URL"
        )
    if not value:
        raise ValueError("DATABASE_URL is required")
    url = make_url(value)
    if url.get_backend_name() == "postgresql":
        return url.set(drivername="postgresql+asyncpg")
    if url.get_backend_name() == "sqlite":
        path = Path(url.database or "").resolve()
        if not path.is_file():
            raise ValueError("SQLite database must already exist")
        return url.set(
            drivername="sqlite+aiosqlite",
            database=f"file:{path.as_posix()}",
            query={"mode": "ro", "uri": "true"},
        )
    raise ValueError("Unsupported database")


async def run(args) -> list[Report]:
    engine = None
    try:
        engine = create_async_engine(_database_url(args.database_url), echo=False)
        snapshots = await load_snapshots(engine, args.task_uuid)
        return [
            verify_snapshot(
                task_uuid,
                snapshots[task_uuid],
                root=args.root,
                expected_attempt=args.expected_attempt,
                publish_state=args.publish_state,
                require_ai_items=args.require_ai_items,
            )
            for task_uuid in args.task_uuid
        ]
    except Exception as exc:  # noqa: BLE001 -- CLI boundary must fail closed without leaking DB credentials.
        # DB exceptions may contain credentials or bound JSON. Report type only.
        reports = []
        for task_uuid in args.task_uuid:
            report = Report(task_uuid)
            report.check(
                "snapshot.read",
                False,
                {
                    "error_type": type(exc).__name__,
                    "reason": "Unable to read/validate evidence; check database configuration, migrations 0022/0023 and row formats.",
                    **(exc.evidence if isinstance(exc, EvidenceReadError) else {}),
                },
            )
            reports.append(report)
        return reports
    finally:
        if engine is not None:
            await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--task-uuid",
        action="append",
        required=True,
        type=lambda value: str(UUID(value)),
        help="Exact task UUID; repeat to verify multiple tasks",
    )
    parser.add_argument(
        "--database-url", help="Defaults to DATABASE_URL or repository .env"
    )
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="Repository root containing data/raw"
    )
    parser.add_argument(
        "--expected-attempt",
        type=int,
        help="Require this latest attempt (e.g. 2 after manual retry)",
    )
    parser.add_argument(
        "--publish-state", choices=("either", "pending", "published"), default="either"
    )
    parser.add_argument(
        "--require-ai-items",
        action="store_true",
        help="Fail if this task has no model work items",
    )
    parser.add_argument("--json", action="store_true", help="Emit structured evidence")
    args = parser.parse_args(argv)
    if args.expected_attempt is not None and args.expected_attempt < 1:
        parser.error("--expected-attempt must be positive")
    reports = asyncio.run(run(args))
    passed = all(report.passed for report in reports)
    if args.json:
        print(
            json.dumps(
                {"passed": passed, "tasks": [r.as_dict() for r in reports]},
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )
    else:
        for report in reports:
            print(f"{'PASS' if report.passed else 'FAIL'} task={report.task_uuid}")
            for check in report.checks:
                print(
                    f"  {'PASS' if check['passed'] else 'FAIL'} {check['check']}: {json.dumps(check['evidence'], ensure_ascii=False, default=str)}"
                )
        print(f"{'PASS' if passed else 'FAIL'}: {len(reports)} task(s)")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
