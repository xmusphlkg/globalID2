"""Data-only JSON publication retains the independently published archive."""
import sys
from unittest.mock import AsyncMock

import pytest

from scripts import generate_site_data
from src.core.database_snapshot import SNAPSHOT_ENV
from src.generation import surveillance_data_export


@pytest.mark.parametrize("coupled", [False, True])
def test_data_only_cli_passes_archive_manifest_without_regenerating_downloads(tmp_path, monkeypatch, coupled):
    archive = tmp_path / "published-downloads"
    archive.mkdir()
    body = '{"generated_at":"2026-11-07T00:00:00Z","surveillance_release_id":"original-archive","countries":[],"diseases":[]}'
    (archive / "manifest.json").write_text(body)
    command = ["generate_site_data.py", "--data-only", "--data-release-id", "new-surveillance",
               "--direct-download-output", str(archive)]
    candidate = tmp_path / "candidate" if coupled else None
    if candidate is not None:
        command.extend(["--surveillance-downloads", str(candidate)])
    export = AsyncMock(return_value={})
    monkeypatch.setattr(sys, "argv", command)
    monkeypatch.setenv(SNAPSHOT_ENV, "existing-test-snapshot")
    monkeypatch.setattr(surveillance_data_export, "export_surveillance_data", export)

    generate_site_data.main()

    export.assert_awaited_once()
    options = export.await_args.kwargs
    assert options["downloads_manifest"] == archive / "manifest.json"
    assert options["download_candidate"] == candidate
    assert options["release_id"] == "new-surveillance"
    assert (archive / "manifest.json").read_text() == body
