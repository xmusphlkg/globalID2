"""Keep current and historical Colombia controller contracts isolated."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
import json
from types import SimpleNamespace

import pytest

from scripts.imports import co_sivigila_history as controller
from scripts import verify_co_sivigila_history as verifier


def select_current(monkeypatch):
    for name in (
        "COUNT_URL", "HISTORY_START_YEAR", "METADATA_URL", "parse_metadata",
        "ColombiaINSSivigilaUpdater", "collect", "organize", "SOURCE_SCOPE",
        "SOURCE_ID", "ARCHIVE_SLUG", "STATE_BASENAME", "ACCEPTANCE_BASENAME",
    ):
        monkeypatch.setattr(controller, name, getattr(controller, name))
    controller.configure_source("co_ins_sivigila_current")


def test_current_controller_scope_paths_and_default_year(monkeypatch):
    select_current(monkeypatch)
    assert controller.SOURCE_SCOPE == "co_ins_sivigila_current"
    assert controller.SOURCE_ID == "SRC_CO_INS_SIVIGILA_CURRENT"
    assert controller.HISTORY_START_YEAR == 2023
    assert controller.ARCHIVE_SLUG == "sivigila-current"
    assert controller.STATE_BASENAME == "current"
    assert controller.ACCEPTANCE_BASENAME == "current_database_acceptance"
    assert controller.default_end_year(2023, None, controller.SOURCE_SCOPE,
                                       today=date(2026, 11, 16)) == 2026
    assert controller.default_end_year(2023, 2024, controller.SOURCE_SCOPE,
                                       today=date(2026, 11, 16)) == 2024
    assert controller.default_end_year(2007, None, "co_ins_sivigila_historical",
                                       today=date(2026, 11, 16)) == 2007


@pytest.mark.asyncio
async def test_current_recovery_filters_database_by_current_source_id(monkeypatch):
    select_current(monkeypatch)
    calls = []

    class Result:
        def all(self):
            return []

    class Database:
        async def execute(self, statement, parameters):
            calls.append((str(statement), parameters))
            return Result()

    @asynccontextmanager
    async def database():
        yield Database()

    async def dispose():
        pass

    monkeypatch.setattr(controller, "get_db", database)
    monkeypatch.setattr(controller, "dispose_database", dispose)
    row = {"SourceRecordID": "new-source-1"}
    assert await controller.reconcile_database_page([row], SimpleNamespace(resolve=lambda: "/manifest")) is None
    assert calls[0][1]["source_id"] == "SRC_CO_INS_SIVIGILA_CURRENT"
    assert "series.source_system=:source_id" in calls[0][0]


def test_verifier_rejects_historical_state_with_current_source(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"identity": {"dataset_version": {"dataset_id": "4hyg-wa9d"},
                                               "year_counts": {"2023": 1}}}))
    monkeypatch.setattr("sys.argv", ["verify_co_sivigila_history.py", "--source",
                                  "co_ins_sivigila_current", "--state", str(state)])
    with pytest.raises(SystemExit, match="another source dataset"):
        verifier.main()


def test_verifier_rejects_overlap_year_in_current_state(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"identity": {"dataset_version": {"dataset_id": "wns8-p38i"},
                                               "year_counts": {"2022": 1}}}))
    monkeypatch.setattr("sys.argv", ["verify_co_sivigila_history.py", "--source",
                                  "co_ins_sivigila_current", "--state", str(state)])
    with pytest.raises(SystemExit, match="historical source year"):
        verifier.main()
