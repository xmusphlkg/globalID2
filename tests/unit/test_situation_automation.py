from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest
import yaml

from scripts.automation.check_contract_drift import CONTRACT_PATHS, changed_contracts
from scripts.automation.run_situation_release import (
    PipelineStep,
    _run_step,
    build_steps,
    dist_inventory,
)
from scripts.automation.prepare_site_build_fixture import (
    FixturePreparationError,
    prepare_fixture,
)
from scripts.automation.smoke_migrations import (
    MigrationSmokeError,
    validate_disposable_url,
    validate_revision_graph,
)
from scripts.automation.verify_situation_artifact import (
    ArtifactVerificationError,
    verify_artifact,
)
from scripts.automation.verify_situation_deployment import (
    DeploymentVerificationError,
    verify_deployment,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "situation-room.yml"
QUALITY_WORKFLOW = ROOT / ".github" / "workflows" / "project-quality.yml"
TEST_RUNNER = ROOT / "tests" / "run_tests.sh"


def _write_dist(root: Path) -> Path:
    dist = root / "dist"
    files = {
        "situation/index.html": b"<html>Situation</html>",
        "sitemaps/situation.xml": b"<urlset />",
        "site-data/situation/v3/latest.json": b'{"report":{"report_id":"r1"}}',
        "site-data/situation/latest.json": b'{"report":{"report_id":"r1"}}',
        "_astro/app.js": b"console.log('release')",
    }
    for relative, content in files.items():
        path = dist / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return dist


def _manifest(dist: Path) -> dict:
    return {
        "run_id": "123-1",
        "source_commit": "a" * 40,
        "status": "passed",
        "deployment_ready": True,
        "steps": [
            {"name": step.name, "status": "passed"}
            for step in build_steps(python_executable="python")
        ],
        "dist": dist_inventory(dist),
    }


class _Response:
    def __init__(self, body: bytes, *, status: int = 200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def test_release_steps_are_live_and_fail_closed() -> None:
    steps = build_steps(python_executable="venv/bin/python")

    assert [step.name for step in steps] == [
        "live_source_analysis",
        "contract_export",
        "contract_drift_check",
        "site_data_export",
        "astro_build",
        "release_gate",
    ]
    assert "--no-fetch-events" not in steps[0].command
    assert steps[2].command == (
        "venv/bin/python",
        "scripts/automation/check_contract_drift.py",
    )
    assert steps[-1].command[-2:] == ("--site-dir", "astro-site/dist")
    assert all(step.timeout_seconds > 0 for step in steps)


def test_step_failure_and_timeout_are_recorded(tmp_path: Path) -> None:
    failed = _run_step(
        PipelineStep(
            name="failure",
            command=(sys.executable, "-c", "print('expected failure'); raise SystemExit(7)"),
            cwd=ROOT,
            timeout_seconds=10,
        ),
        tmp_path / "failure.log",
    )
    assert failed["status"] == "failed"
    assert failed["exit_code"] == 7
    assert "expected failure" in (tmp_path / "failure.log").read_text(encoding="utf-8")

    timed_out = _run_step(
        PipelineStep(
            name="timeout",
            command=(sys.executable, "-c", "import time; time.sleep(2)"),
            cwd=ROOT,
            timeout_seconds=1,
        ),
        tmp_path / "timeout.log",
    )
    assert timed_out["status"] == "timed_out"
    assert timed_out["timeout_seconds"] == 1


def test_contract_drift_check_catches_modified_and_untracked_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=tmp_path, check=True)
    for relative in CONTRACT_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("current\n", encoding="utf-8")
    subprocess.run(["git", "add", *CONTRACT_PATHS], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "contracts"], cwd=tmp_path, check=True)
    assert changed_contracts(tmp_path) == []

    (tmp_path / CONTRACT_PATHS[0]).write_text("changed\n", encoding="utf-8")
    assert any(CONTRACT_PATHS[0] in line for line in changed_contracts(tmp_path))

    subprocess.run(["git", "checkout", "--", CONTRACT_PATHS[0]], cwd=tmp_path, check=True)
    subprocess.run(["git", "rm", "-q", "--cached", CONTRACT_PATHS[1]], cwd=tmp_path, check=True)
    assert any(CONTRACT_PATHS[1] in line for line in changed_contracts(tmp_path))


def test_workflow_is_scheduled_serialized_and_artifact_gated() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    # BaseLoader avoids YAML 1.1 coercing the GitHub key `on` to a boolean.
    parsed = yaml.load(text, Loader=yaml.BaseLoader)

    assert parsed["on"]["schedule"][0]["cron"] == "17 03 * * *"
    assert "workflow_dispatch" in parsed["on"]
    assert parsed["concurrency"]["group"] == "situation-room-production"
    assert parsed["concurrency"]["cancel-in-progress"] == "false"
    assert parsed["jobs"]["build-and-gate"]["timeout-minutes"] == "90"
    assert parsed["jobs"]["deploy-production"]["needs"] == "build-and-gate"
    assert "needs.build-and-gate.result == 'success'" in parsed["jobs"]["deploy-production"]["if"]
    assert "vars.SITUATION_AUTO_DEPLOY == 'true'" in parsed["jobs"]["deploy-production"]["if"]
    assert "github.event_name == 'workflow_dispatch'" in parsed["jobs"]["deploy-production"]["if"]
    assert "inputs.deploy == true" in parsed["jobs"]["deploy-production"]["if"]
    assert "actions/upload-artifact@v4" in text
    assert "if: always()" in text
    assert "--require-env" in text
    assert "validate_situation_release.py" not in text  # The orchestrator owns the sequence.
    assert "wrangler pages deploy" in text
    assert "secrets.SITUATION_DATABASE_URL" in text
    assert "secrets.SITUATION_HISTORY_DATABASE_URL" in text
    assert "secrets.CLOUDFLARE_API_TOKEN" in text
    assert "secrets.CLOUDFLARE_ACCOUNT_ID" in text
    assert "scripts/automation/verify_situation_artifact.py" in text
    assert "--expected-source-commit \"${GITHUB_SHA}\"" in text
    assert "scripts/automation/verify_situation_deployment.py" in text
    assert "vars.SITUATION_PUBLIC_DATA_URL" in text
    dispatch = parsed["jobs"]["dispatch-reviewed-alerts"]
    assert dispatch["needs"] == "deploy-production"
    assert "needs.deploy-production.result == 'success'" in dispatch["if"]
    assert "scripts/automation/dispatch_situation_alerts.py" in text
    assert "secrets.SITUATION_ALERT_INGEST_TOKEN" in text
    assert dispatch["env"]["SITUATION_ALERT_MAX_ATTEMPTS"] == "4"


def test_orchestrator_dry_run_creates_non_deployable_manifest(tmp_path: Path) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "automation" / "run_situation_release.py"),
        "--dry-run",
        "--artifact-root",
        str(tmp_path),
        "--run-id",
        "unit-test",
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    manifest = yaml.safe_load((tmp_path / "unit-test" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "dry_run"
    assert manifest["deployment_ready"] is False
    assert len(manifest["steps"]) == 6


def test_complete_artifact_inventory_detects_any_post_gate_mutation(tmp_path: Path) -> None:
    dist = _write_dist(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(dist)), encoding="utf-8")

    verified = verify_artifact(
        manifest_path,
        dist,
        expected_run_id="123-1",
        expected_source_commit="a" * 40,
    )
    assert verified["status"] == "verified"
    assert verified["file_count"] == 5
    assert len(verified["tree_sha256"]) == 64

    (dist / "_astro" / "app.js").write_text("tampered", encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="artifact_total_bytes_mismatch|artifact_tree_sha256_mismatch"):
        verify_artifact(manifest_path, dist)


def test_artifact_verifier_rejects_wrong_run_source_steps_and_symlinks(tmp_path: Path) -> None:
    dist = _write_dist(tmp_path)
    payload = _manifest(dist)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="manifest_run_id_mismatch"):
        verify_artifact(manifest_path, dist, expected_run_id="wrong")
    with pytest.raises(ArtifactVerificationError, match="manifest_source_commit_mismatch"):
        verify_artifact(
            manifest_path,
            dist,
            expected_source_commit="b" * 40,
        )
    payload["steps"] = payload["steps"][:-1]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="manifest_step_sequence_mismatch"):
        verify_artifact(manifest_path, dist)

    payload = _manifest(dist)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    (dist / "linked.js").symlink_to(dist / "_astro" / "app.js")
    with pytest.raises(ArtifactVerificationError, match="symbolic links"):
        verify_artifact(manifest_path, dist)


def test_deployment_probe_retries_stale_content_until_exact_bytes_are_public(tmp_path: Path) -> None:
    artifact = tmp_path / "latest.json"
    expected = b'{"report":{"report_id":"r1"}}'
    artifact.write_bytes(expected)
    responses = [b'{"report":{"report_id":"old"}}', expected]
    calls = []
    sleeps = []

    def opener(request, *, timeout):
        calls.append((request.full_url, timeout))
        return _Response(responses.pop(0))

    result = verify_deployment(
        artifact,
        "https://example.invalid/site-data/situation/v3/latest.json",
        attempts=3,
        timeout_seconds=4,
        initial_delay_seconds=0.25,
        maximum_delay_seconds=1,
        opener=opener,
        sleep=sleeps.append,
    )

    assert result["status"] == "verified"
    assert result["attempts"] == 2
    assert len(calls) == 2
    assert sleeps == [0.25]
    assert all("release_sha256=" in url for url, _ in calls)


def test_deployment_probe_does_not_retry_auth_or_configuration_failure(tmp_path: Path) -> None:
    artifact = tmp_path / "latest.json"
    artifact.write_text("{}", encoding="utf-8")
    calls = 0

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(request.full_url, 403, "forbidden", {}, None)

    with pytest.raises(DeploymentVerificationError, match="public_http_error:403:attempts=1"):
        verify_deployment(
            artifact,
            "https://example.invalid/latest.json",
            attempts=5,
            opener=opener,
            sleep=lambda _delay: pytest.fail("fatal 403 must not sleep"),
        )
    assert calls == 1


def test_migration_graph_is_linear_and_database_guard_requires_disposable_opt_in() -> None:
    graph = validate_revision_graph()
    assert graph["head"] == "0009_situation_v3"
    assert graph["base"] == "0001_control_plane_baseline"
    assert graph["revision_count"] == 9

    url = "postgresql://user:pass@localhost/globalid_migration_smoke"
    with pytest.raises(MigrationSmokeError, match="opt_in"):
        validate_disposable_url(url, {})
    with pytest.raises(MigrationSmokeError, match="disposable_migration_database_name"):
        validate_disposable_url(
            "postgresql://user:pass@localhost/globalid",
            {"MIGRATION_SMOKE_ALLOW_DESTRUCTIVE": "1"},
        )
    assert validate_disposable_url(
        url, {"MIGRATION_SMOKE_ALLOW_DESTRUCTIVE": "1"}
    ) == "globalid_migration_smoke"


def test_project_quality_workflow_covers_backend_frontends_worker_and_migrations() -> None:
    text = QUALITY_WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    jobs = parsed["jobs"]
    assert set(jobs) == {
        "backend-and-contracts",
        "dashboard",
        "astro-site",
        "subscriptions-worker",
        "migration-smoke",
    }
    assert "./tests/run_tests.sh --type all" in text
    assert "check_contract_drift.py" in text
    assert "scripts/automation/smoke_migrations.py" in text
    assert "postgres:15-alpine" in text
    assert "full_rebuild_database.py --mode full --yes" in text
    assert "init_situation_history_database.py --backfill" in text
    assert "npm run check" in text
    assert "prepare_site_build_fixture.py" in text
    assert "npm run test:perf" in text
    assert "npm run build:astro" in text
    assert "npm run typecheck" in text
    assert parsed["concurrency"]["cancel-in-progress"] == "true"


def test_canonical_test_runner_cannot_mask_integration_failures() -> None:
    text = TEST_RUNNER.read_text(encoding="utf-8")
    assert "|| true" not in text
    assert "eval " not in text
    assert "verify_ai_test_setup.py" not in text
    assert "test_ai_with_real_data.py" not in text
    assert "set -uo pipefail" in text
    assert '"$PYTHON_BIN" -m pytest -q' in text


def test_ci_site_fixture_is_guarded_missing_only_and_supplies_required_inputs(tmp_path: Path) -> None:
    site = tmp_path / "astro-site"
    site.mkdir()
    (site / "package.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FixturePreparationError, match="ci_environment_required"):
        prepare_fixture(site, environment={})

    meta = site / "src" / "data" / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text('{"preserve":true}\n', encoding="utf-8")
    result = prepare_fixture(site, environment={"CI": "true"})

    assert result["status"] == "prepared"
    assert "src/data/meta.json" in result["preserved"]
    assert json.loads(meta.read_text(encoding="utf-8")) == {"preserve": True}
    assert json.loads(
        (site / "src" / "data" / "diseases" / "index.json").read_text(encoding="utf-8")
    ) == []
    research = json.loads(
        (site / "src" / "data" / "research" / "index.json").read_text(encoding="utf-8")
    )
    assert len(research["articles"]) == 1
    assert len(research["reviews_and_guidelines"]) == 1
    assert len(research["emerging_topics"]) == 1
    assert research["surveillance_evidence"]["available"] is True
    assert research["surveillance_evidence"]["visibility"] == "public"
    assert research["facets"]["diseases"][0]["slug"] == "influenza"
    assert research["facets"]["countries"][0]["slug"] == "united-states"
    assert research["facets"]["topics"][0]["slug"] == "surveillance"
    latest = site / "public" / "site-data" / "situation" / "v3" / "latest.json"
    assert json.loads(latest.read_text(encoding="utf-8"))["schema_version"] == "situation_room.v3"
