import asyncio
import json

import pytest

from scripts import (
    backfill_literature_metadata,
    backfill_pubmed_abstracts,
    compact_literature_payloads,
)
from src.literature import maintenance_cli


@pytest.mark.parametrize("script,arguments", [
    (backfill_literature_metadata, ["--concurrency", "0"]),
    (backfill_literature_metadata, ["--min-interval-seconds", "nan"]),
    (backfill_literature_metadata, ["--min-interval-seconds", "inf"]),
    (backfill_literature_metadata, ["--providers", ""]),
    (backfill_literature_metadata, ["--providers", "unknown"]),
    (backfill_pubmed_abstracts, ["--limit", "0"]),
    (backfill_pubmed_abstracts, ["--batch-size", "501"]),
    (backfill_pubmed_abstracts, ["--min-abstract-characters", "0"]),
    (compact_literature_payloads, ["--batch-size", "0"]),
    (compact_literature_payloads, ["--batch-size", "10001"]),
    (compact_literature_payloads, ["--max-rows", "-1"]),
])
def test_invalid_cli_input_is_rejected_before_execution(monkeypatch, script, arguments):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid arguments must not start maintenance or acquire an apply lock")

    monkeypatch.setattr(script, "run_maintenance", unexpected)
    if hasattr(script, "_exclusive_apply_lock"):
        monkeypatch.setattr(script, "_exclusive_apply_lock", unexpected)
    with pytest.raises(SystemExit) as error:
        script.main(["--apply", *arguments])
    assert error.value.code == 2


@pytest.mark.parametrize("script", [backfill_literature_metadata, backfill_pubmed_abstracts])
def test_apply_lock_releases_after_failure_and_preserves_inode(script, tmp_path):
    path = tmp_path / "nested" / "apply.lock"
    with (
        pytest.raises(RuntimeError, match="operation failed"),
        script._exclusive_apply_lock(path),
    ):
        inode = path.stat().st_ino
        with (
            pytest.raises(script.ConcurrentApplyError, match="already running"),
            script._exclusive_apply_lock(path),
        ):
            pytest.fail("concurrent writer acquired lock")
        raise RuntimeError("operation failed")

    with script._exclusive_apply_lock(path):
        assert path.stat().st_ino == inode


@pytest.mark.parametrize("error", [None, RuntimeError("failed"), asyncio.CancelledError()])
def test_maintenance_disposes_database_on_same_loop_even_after_failure(monkeypatch, error):
    events = []

    async def operation():
        events.append(("operation", asyncio.get_running_loop()))
        if error is not None:
            raise error
        return {"completed": True}

    async def dispose():
        events.append(("dispose", asyncio.get_running_loop()))

    monkeypatch.setattr(maintenance_cli, "dispose_database", dispose)
    if error is None:
        assert maintenance_cli.run_maintenance(operation) == {"completed": True}
    else:
        with pytest.raises(type(error)) as raised:
            maintenance_cli.run_maintenance(operation)
        assert raised.value is error
    assert [event for event, loop in events] == ["operation", "dispose"]
    assert events[0][1] is events[1][1]
    assert events[0][1].is_closed()


@pytest.mark.parametrize("failures,exit_code", [(0, 0), (1, 2)])
def test_metadata_cli_reports_provider_failure(monkeypatch, capsys, failures, exit_code):
    async def run(args):
        assert args.apply is False
        return {"failure_count": failures}

    async def dispose():
        pass

    monkeypatch.setattr(backfill_literature_metadata, "_main", run)
    monkeypatch.setattr(maintenance_cli, "dispose_database", dispose)
    assert backfill_literature_metadata.main([]) == exit_code
    assert json.loads(capsys.readouterr().out) == {"failure_count": failures}


@pytest.mark.parametrize("apply,expected_sizes", [(True, [3, 2]), (False, [3])])
async def test_compaction_respects_row_limit_and_single_batch_dry_run(monkeypatch, apply, expected_sizes):
    calls = []

    async def compact(*, batch_size, dry_run):
        calls.append(batch_size)
        assert dry_run is not apply
        return {
            "scanned": batch_size, "compacted": batch_size,
            "before_bytes": batch_size * 10, "after_bytes": batch_size * 2,
            "saved_bytes": batch_size * 8,
        }

    monkeypatch.setattr(compact_literature_payloads, "compact_source_payload_batch", compact)
    result = await compact_literature_payloads.run(apply=apply, batch_size=3, max_rows=5)
    assert calls == expected_sizes
    assert result["scanned"] == sum(expected_sizes)
    assert result["saved_bytes"] == 8 * sum(expected_sizes)


@pytest.mark.parametrize("batch_size,max_rows", [(0, None), (10001, 1), (1, 0)])
async def test_compaction_api_rejects_invalid_bounds(batch_size, max_rows):
    with pytest.raises(ValueError):
        await compact_literature_payloads.run(apply=True, batch_size=batch_size, max_rows=max_rows)
