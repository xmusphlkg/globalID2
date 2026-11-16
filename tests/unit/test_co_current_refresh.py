"""A current publication check must never certify only one imported page."""
import json
from types import SimpleNamespace

import pytest

from src.data.crawlers.co import FIELDS
from src.data.crawlers.co_current import DEFAULT_SCOPE, parse_metadata
from src.services.ingestion import co_current_refresh as refresh


class Writer:
    def __init__(self, directory):
        self.directory, self.artifacts = directory, []

    async def write(self, payload, **values):
        self.artifacts.append(SimpleNamespace(payload=payload, **values))


def setup(monkeypatch, tmp_path, *, complete):
    metadata = json.dumps({
        "id": "wns8-p38i", "attribution": "Instituto Nacional de Salud - INS, Bogotá D.C.",
        "licenseId": "CC_40_BY", "rowsUpdatedAt": 1, "viewLastModified": 2,
        "columns": [{"fieldName": name} for name in FIELDS[:-1]],
    }).encode()
    version, counts = parse_metadata(metadata), {"2023": 2, "2024": 3}
    state = {"status": "complete" if complete else "running", "completed_rows": 5, "expected_rows": 5,
             "identity": {"dataset_version": version, "year_counts": counts,
                          "start_year": 2023, "end_year": 2024}}
    acceptance = {"complete": complete, "dataset_version": version,
                  "years": {year: {"complete": True, "official_rows": count,
                                   "database_rows": count} for year, count in counts.items()}}
    state_path, acceptance_path = tmp_path / "state.json", tmp_path / "acceptance.json"
    state_path.write_text(json.dumps(state))
    acceptance_path.write_text(json.dumps(acceptance))
    monkeypatch.setattr(refresh, "STATE_PATH", state_path)
    monkeypatch.setattr(refresh, "ACCEPTANCE_PATH", acceptance_path)
    calls = []

    class Crawler:
        session = SimpleNamespace(close=lambda: None)

        def get(self, url, params=None):
            calls.append(url)
            payload = (json.dumps({"resultSetSize": 1, "results": [
                {"resource": {"id": "wns8-p38i", "name": "SIVIGILA"}}]}).encode()
                if url == refresh.CATALOG_URL else
                json.dumps([{"ano": "2022", "n": "99"},
                                   {"ano": "2023", "n": "2"}, {"ano": "2024", "n": "3"}]).encode()
                       if params else metadata)
            return SimpleNamespace(content=payload, url=url)

    monkeypatch.setattr(refresh, "ColombiaINSSivigilaCrawler", Crawler)
    return Writer(tmp_path), calls, state_path, acceptance_path


@pytest.mark.asyncio
async def test_unchanged_complete_successor_skips_case_downloads(monkeypatch, tmp_path):
    writer, calls, _, _ = setup(monkeypatch, tmp_path, complete=True)

    async def forbidden(*args):
        pytest.fail("A complete unchanged release must not launch a backfill")

    monkeypatch.setattr(refresh, "_run", forbidden)
    await refresh.collect(writer)
    audit = json.loads(writer.artifacts[-1].payload)
    assert len(calls) == 4
    assert audit["status"] == "unchanged_verified"
    assert audit["source_rows"] == 5 and "2022" not in audit["year_counts"]


@pytest.mark.asyncio
async def test_partial_successor_runs_full_controller_then_verifier(monkeypatch, tmp_path):
    writer, _, state_path, acceptance_path = setup(monkeypatch, tmp_path, complete=False)
    commands = []

    async def run(command, log_path):
        commands.append(command)
        if len(commands) == 2:
            state, acceptance = json.loads(state_path.read_bytes()), json.loads(acceptance_path.read_bytes())
            state["status"], acceptance["complete"] = "complete", True
            state_path.write_text(json.dumps(state))
            acceptance_path.write_text(json.dumps(acceptance))

    monkeypatch.setattr(refresh, "_run", run)
    await refresh.collect(writer)
    assert len(commands) == 2
    assert commands[0][0].name == "co_sivigila_history.py"
    assert commands[1][0].name == "verify_co_sivigila_history.py"
    assert DEFAULT_SCOPE in commands[0] and "--apply" in commands[0]
    assert "--require-complete" in commands[1]
    assert json.loads(writer.artifacts[-1].payload)["status"] == "complete_refreshed_verified"


@pytest.mark.asyncio
async def test_partial_import_cannot_emit_success_receipt(monkeypatch, tmp_path):
    writer, _, _, _ = setup(monkeypatch, tmp_path, complete=False)

    async def incomplete(command, log_path):
        pass

    monkeypatch.setattr(refresh, "_run", incomplete)
    with pytest.raises(RuntimeError, match="final reconciliation"):
        await refresh.collect(writer)
    assert len(writer.artifacts) == 4


def test_another_ins_sivigila_edition_cannot_be_silently_ignored():
    document = {"resultSetSize": 1, "results": [{"resource": {
        "id": "newx-2025", "name": "SIVIGILA 2025"}}]}
    with pytest.raises(RuntimeError, match="requires_new_source_contract.*newx-2025"):
        refresh.check_catalog(json.dumps(document).encode())
    document["resultSetSize"] = 2
    with pytest.raises(ValueError, match="Incomplete"):
        refresh.check_catalog(json.dumps(document).encode())
