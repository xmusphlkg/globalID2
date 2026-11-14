"""Research monitor accepts verified site commits without masking newer data."""
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import CompileError

from src.services.data_release.realtime_monitor import (
    _research_publication_state, collect_database_snapshot,
)
from src.services.data_release.research_publication_receipt import verified_research_commit
from tests.unit.test_realtime_data_monitor import NOW, codes, db_state, evaluate, manifest_fixture, public_state

SETTINGS = SimpleNamespace(r2_papers_public_base_url="https://papers.example", literature_publish_interval_minutes=360)


def receipt(root, *, task="site-task", site=True, committed=None, completed=None):
    committed = committed or NOW - timedelta(minutes=3)
    completed = completed or NOW - timedelta(minutes=1)
    release = "site-release" if site else f"literature-{task}"
    run = root / "exports" / ("site-research-runs" if site else "research-runs") / task
    if site:
        run /= release
    run.mkdir(parents=True)
    pointer = json.loads(manifest_fixture()[1])
    pointer.update(release_id=release, papers={"papers_origin": SETTINGS.r2_papers_public_base_url,
        "papers_generation_sha256": "b" * 64})
    body = json.dumps(pointer).encode()
    digest = hashlib.sha256(body).hexdigest()
    (run / "current.json").write_bytes(body)
    (run / "authority-committed.json").write_text(json.dumps({"schema": 1, "release_id": release,
        "manifest_sha256": digest, "generation": "b" * 64, "authoritative_committed": True,
        "public_verification_required": True, "committed_at": committed.isoformat()}))
    (run / "published.json").write_text(json.dumps({"release_id": release,
        "json": {"release_id": release, "manifest_sha256": digest},
        "papers": {"generation_sha256": "b" * 64},
        "public_verification": {"status": "verified", "generation": "b" * 64,
            "canaries": [{"locale": locale, "html_sha256": "c" * 64,
                "url": "https://papers.example/" + ("" if locale == "en" else locale + "/") + "research/articles/paper-a/"}
                for locale in ("en", "zh", "fr")]}}))
    fields = {"task_uuid": task, "release_id": release,
        "created_at": NOW - timedelta(hours=1), "completed_at": completed}
    return run, fields, {**fields, "manifest_sha256": digest, "age_seconds": 60, "status": "completed"}


class Database:
    def __init__(self, values):
        self.values = iter(values)
        self.statements = []

    async def execute(self, statement):
        try:
            sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        except CompileError:  # PostgreSQL JSONB containment has no literal renderer.
            sql = str(statement.compile(dialect=postgresql.dialect()))
        self.statements.append(sql)
        value = next(self.values)
        return SimpleNamespace(all=lambda: value, first=lambda: value, scalar_one=lambda: value,
            scalar_one_or_none=lambda: value)


def site_row(fields):
    return tuple(fields[key] for key in ("task_uuid", "created_at", "completed_at", "release_id"))


@pytest.mark.asyncio
async def test_verified_site_commit_resolves_mismatch_and_keeps_backlog(tmp_path):
    _, fields, expected = receipt(tmp_path)
    db = Database([[site_row(fields)]])
    state = db_state()
    state["last_completed"] = await _research_publication_state(db, state["last_completed"],
        now=NOW, root=tmp_path, settings=SETTINGS)
    state["publishable_pending"].update(count=12, oldest_age_seconds=800)
    public = {**public_state(), "release_id": expected["release_id"], "manifest_sha256": expected["manifest_sha256"]}
    result = evaluate(state, public)
    assert codes(result) == {"pending_changes_delayed": "critical"}
    assert result["database"]["publishable_pending"]["count"] == 12
    assert state["last_completed"]["publication_kind"] == "site"
    sql = db.statements[0]
    assert "'COMPLETED'" in sql and "r2_papers_published" in sql and "IS true" in sql
    assert "data_only" in sql and "IS NOT true" in sql
    assert all(query.startswith("SELECT") for query in db.statements)


@pytest.mark.parametrize("fault", ["missing", "no_canary", "wrong_generation", "wrong_manifest", "wrong_release",
    "missing_locale", "duplicate_locale", "foreign_origin", "early_commit", "late_commit", "symlink"])
@pytest.mark.asyncio
async def test_unproven_site_publication_never_suppresses_mismatch(tmp_path, fault):
    run, fields, expected = receipt(tmp_path)
    path = run / "published.json"
    value = json.loads(path.read_text())
    if fault == "missing":
        path.unlink()
    elif fault == "symlink":
        saved = run / "saved.json"
        path.rename(saved)
        path.symlink_to(saved)
    else:
        if fault == "no_canary": value.pop("public_verification")
        if fault == "wrong_generation": value["public_verification"]["generation"] = "d" * 64
        if fault == "wrong_release": value["release_id"] = "other-release"
        if fault == "missing_locale": value["public_verification"]["canaries"].pop()
        if fault == "duplicate_locale": value["public_verification"]["canaries"][0]["locale"] = "fr"
        if fault == "foreign_origin": value["public_verification"]["canaries"][0]["url"] = "https://foreign.example/research/articles/paper-a/"
        path.write_text(json.dumps(value))
        if fault in {"wrong_manifest", "early_commit", "late_commit"}:
            authority = run / "authority-committed.json"
            data = json.loads(authority.read_text())
            if fault == "wrong_manifest": data["manifest_sha256"] = "d" * 64
            if fault == "early_commit": data["committed_at"] = (fields["created_at"] - timedelta(seconds=1)).isoformat()
            if fault == "late_commit": data["committed_at"] = (fields["completed_at"] + timedelta(seconds=1)).isoformat()
            authority.write_text(json.dumps(data))
    state = db_state()
    original = state["last_completed"]
    state["last_completed"] = await _research_publication_state(Database([[site_row(fields)]]), original,
        now=NOW, root=tmp_path, settings=SETTINGS)
    assert state["last_completed"] == original
    assert "public_completed_version_mismatch" in codes(evaluate(state,
        {**public_state(), "release_id": expected["release_id"], "manifest_sha256": expected["manifest_sha256"]}))


@pytest.mark.asyncio
@pytest.mark.parametrize("site_is_newer", [True, False])
async def test_actual_commit_order_wins_over_late_task_completion(tmp_path, site_is_newer):
    _, _, current = receipt(tmp_path, task="independent", site=False,
        committed=NOW - timedelta(minutes=5), completed=NOW - timedelta(minutes=4))
    _, fields, expected = receipt(tmp_path,
        committed=NOW - timedelta(minutes=3 if site_is_newer else 6), completed=NOW - timedelta(minutes=1))
    selected = await _research_publication_state(Database([[site_row(fields)]]), current,
        now=NOW, root=tmp_path, settings=SETTINGS)
    assert selected["release_id"] == (expected if site_is_newer else current)["release_id"]
    assert selected["public_verified"] is True


def test_site_receipt_cannot_be_adopted_under_another_task(tmp_path):
    _, fields, _ = receipt(tmp_path)
    assert verified_research_commit(tmp_path, **{**fields, "task_uuid": "unrelated"},
        papers_origin=SETTINGS.r2_papers_public_base_url, site=True) is None


@pytest.mark.asyncio
async def test_literature_schedule_uses_independent_task_creation_and_preserves_pending_age(tmp_path):
    last_created = NOW - timedelta(hours=2)
    db = Database([[('*', 150, NOW - timedelta(hours=3), 1)], [], [], 0, None, None, [], last_created])
    state = (await collect_database_snapshot(db, now=NOW, channels=("literature",),
        root=tmp_path, settings=SETTINGS))["literature"]
    assert state["schedule"]["next_due_at"] == (last_created + timedelta(hours=6)).isoformat()
    assert state["schedule"]["waiting_for_schedule"] is True
    assert state["publishable_pending"]["oldest_age_seconds"] == 10800
    assert state["publishable_pending"]["count"] == 150
    assert "pending_changes_delayed" not in codes(evaluate(state))
    assert "created_at DESC" in db.statements[-1]
    assert "literature" in db.statements[-1] and "data_only" in db.statements[-1]


@pytest.mark.parametrize("due_offset,expected", [(3600, None), (-119, None), (-120, "warning"), (-600, "critical")])
def test_literature_pending_delay_starts_after_scheduled_due_with_existing_tolerance(due_offset, expected):
    state = db_state()
    state["publishable_pending"].update(count=50, oldest_age_seconds=20000)
    state["schedule"] = {"next_due_at": (NOW + timedelta(seconds=due_offset)).isoformat()}
    result = evaluate(state)
    assert codes(result).get("pending_changes_delayed") == expected
    assert result["database"]["publishable_pending"]["oldest_age_seconds"] == 20000


def test_schedule_does_not_mask_new_pending_age_running_failure_or_version_mismatch():
    state = db_state()
    state["schedule"] = {"next_due_at": (NOW - timedelta(hours=1)).isoformat()}
    state["publishable_pending"].update(count=1, oldest_age_seconds=30)
    assert "pending_changes_delayed" not in codes(evaluate(state))
    state["schedule"]["next_due_at"] = (NOW + timedelta(hours=4)).isoformat()
    state["active"] = [{"task_uuid": "stuck", "status": "running", "age_seconds": 1900}]
    state["latest_terminal"] = {"status": "failed"}
    result = evaluate(state, {**public_state(), "manifest_sha256": "d" * 64})
    assert set(codes(result)) == {"running_task_delayed", "latest_release_unsuccessful", "public_completed_version_mismatch"}
