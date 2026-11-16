"""BES capture/replay never reports unverified bulletin numbers as imported."""
from __future__ import annotations

import json

import pytest

from scripts.imports import co_bes_history as history
from scripts.imports import import_surveillance_archive as archive
from scripts.imports import kh_camewarn_history as snapshots
from src.data.crawlers.co_bes import ARCHIVE_URL, NotYetVerifiedContract
from src.data.crawlers.co_ins_transport import INSTransportError


@pytest.fixture
def setup_capture(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for module in (history, archive, snapshots):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    args = history.argument_parser().parse_args([
        "--start-year", "2025", "--end-year", "2026", "--capture-only",
        "--state-path", str(tmp_path / "state.json"),
    ])
    calls = []

    async def collect(writer, **options):
        calls.append(options)
        await writer.write(b"%PDF fixture %%EOF", source_url=ARCHIVE_URL + "/report.pdf",
                           kind="cda_pdf", metadata={"endpoint": "pdf"})
        await writer.write(json.dumps({"reports": [{"pdf_url": "fixture"}],
                                       "failed_downloads": []}).encode(),
                           source_url=ARCHIVE_URL, kind="archive_json",
                           metadata={"endpoint": "collection_plan"})

    monkeypatch.setattr(history, "collect", collect)
    return args, calls


@pytest.mark.asyncio
async def test_capture_multiple_years_preserves_raw_manifest_without_import(setup_capture):
    args, calls = setup_capture
    state, code = await history.run(args)
    assert code == 0
    assert calls[0]["start_year"] == 2025 and calls[0]["end_year"] == 2026
    assert calls[0]["download_workers"] == 8
    assert state["status"] == "captured_awaiting_source_contract"
    assert state["database_imported"] is False
    assert state["coverage_verified"] is False
    assert state["report_periods_verified"] is False
    manifest = json.loads((history.ROOT / state["raw_manifest"]).read_text())
    assert manifest["database_registered"] is False
    assert len(manifest["artifacts"]) == 2


@pytest.mark.asyncio
async def test_apply_stops_before_any_database_registration_when_contract_unverified(setup_capture, monkeypatch):
    args, _ = setup_capture
    args.capture_only, args.apply = False, True

    def unverified(*args, **kwargs):
        raise NotYetVerifiedContract("No verified numerical tables")

    async def forbidden_import(*args, **kwargs):
        pytest.fail("Unverified originals must not create database import tasks")

    monkeypatch.setattr(history, "organize", unverified)
    monkeypatch.setattr(history, "import_archive", forbidden_import)
    state, code = await history.run(args)
    assert code == 2
    assert state["status"] == "captured_awaiting_source_contract"
    assert state["database_imported"] is False


@pytest.mark.asyncio
async def test_replay_verifies_hash_before_organization(setup_capture, monkeypatch):
    args, _ = setup_capture
    state, _ = await history.run(args)
    args.replay_manifest = history.ROOT / state["raw_manifest"]
    manifest = json.loads(args.replay_manifest.read_text())
    (history.ROOT / manifest["artifacts"][0]["relative_path"]).write_bytes(b"modified")
    args.capture_only = False

    def forbidden_organize(*args, **kwargs):
        pytest.fail("Modified bytes must be rejected before organization")

    monkeypatch.setattr(history, "organize", forbidden_organize)
    state, code = await history.run(args)
    assert code == 2 and state["database_imported"] is False
    assert state["error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_empty_capture_never_reports_success(setup_capture, monkeypatch):
    args, _ = setup_capture

    async def empty(writer, **options):
        await writer.write(json.dumps({"reports": [], "failed_downloads": []}).encode(),
                           source_url=ARCHIVE_URL, kind="archive_json",
                           metadata={"endpoint": "collection_plan"})

    monkeypatch.setattr(history, "collect", empty)
    state, code = await history.run(args)
    assert code == 2 and state["raw_reports"] == 0
    assert state["status"] == "failed_capture_or_organization"


@pytest.mark.asyncio
async def test_network_failure_state_redacts_error_details(setup_capture, monkeypatch):
    args, _ = setup_capture

    async def fail(*args, **kwargs):
        raise INSTransportError("http://operator:secret@proxy.example")

    monkeypatch.setattr(history, "collect", fail)
    monkeypatch.setattr(history, "ins_transport_readiness", lambda: {
        "configured": False, "proxy_kind": "direct", "dependency_ready": True})
    state, code = await history.run(args)
    assert code == 2 and state["status"] == "awaiting_colombian_egress"
    assert "secret" not in args.state_path.read_text()


def test_bes_updater_isolated_from_existing_soda_releases():
    updater = history.ColombiaINSBESUpdater()
    assert updater.source_scope == "co_ins_bes_weekly"
    assert updater.ontology_source_id == "SRC_CO_INS_BES"
    assert updater.full_history_start_year == 2025
    assert updater.public_release_enabled is False
    assert updater.series_geography_from_rows is True
    with pytest.raises(ValueError):
        updater.normalize_source("co_ins_sivigila_current")
