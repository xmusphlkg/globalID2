"""CNDSS national source migration preserves zero, unknown, and full history."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.data.crawlers.ca_national import CanadaCNDSSNationalCrawler
from src.data.processors.ca_national import CanadaCNDSSAnnualUpdater
from src.services.ingestion.countries import ca_national
from src.services.ingestion.strategies import (
    CanadaCndssWindowCommitStrategy,
    UpdaterCommitStrategy,
)
from tests.contract.native import assert_native_observations_match

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/CA"


def test_ca_national_source_contract_and_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = Path("data/raw/ca/contract")
    directory.mkdir(parents=True)
    artifacts = []
    for name in ("describe", "raw"):
        raw = (FIXTURES / f"{name}.json").read_bytes()
        path = directory / f"{name}.json"
        path.write_bytes(raw)
        artifacts.append(
            SimpleNamespace(
                relative_path=str(path),
                byte_size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                metadata_={"endpoint": name},
            )
        )
    describe = json.loads((FIXTURES / "describe.json").read_text())
    raw = json.loads((FIXTURES / "raw.json").read_text())
    first, last = CanadaCNDSSNationalCrawler._validate_describe(describe)
    expected = CanadaCNDSSNationalCrawler._rows_from_payload(
        raw, first_year=first, last_year=last
    )
    monkeypatch.setattr(
        CanadaCNDSSNationalCrawler,
        "get",
        lambda *a, **kw: pytest.fail("organize requested a source"),
    )
    actual = ca_national.organize(
        artifacts, updater=CanadaCNDSSAnnualUpdater(), source="cndss", start_year=2023
    )

    def stable(rows):
        return [{k: v for k, v in r.items() if k != "RetrievedAt"} for r in rows]

    assert stable(actual.rows) == stable(expected)
    assert_native_observations_match(actual.rows, expected, CanadaCNDSSAnnualUpdater())
    assert [(r["Date"], r["SourceDiseaseCode"], r["Cases"]) for r in actual.rows] == [
        ("1924-01-01", "6", "1"),
        ("2023-01-01", "6", "0"),
        ("2024-01-01", "70", "12"),
    ]
    assert all(r["GeographyKey"] == "country:CA:national" for r in actual.rows)


async def test_ca_collect_does_not_parse_rows(monkeypatch):
    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, raw, **kwargs):
            self.artifacts.append((raw, kwargs))

    def get(self, url, **kwargs):
        name = "describe" if url.endswith("describe.json") else "raw"
        return SimpleNamespace(
            content=(FIXTURES / f"{name}.json").read_bytes(), url=url
        )

    monkeypatch.setattr(CanadaCNDSSNationalCrawler, "get", get)
    monkeypatch.setattr(
        CanadaCNDSSNationalCrawler,
        "_rows_from_payload",
        lambda *a, **kw: pytest.fail("collect parsed business rows"),
    )
    artifacts = await ca_national.collect(Writer(), source="cndss", start_year=2023)
    assert len(artifacts) == 2


async def test_ca_delete_is_confined_to_national_commit_strategy(monkeypatch):
    lock = AsyncMock()
    monkeypatch.setattr("src.services.ingestion.strategies.acquire_disease_data_mutation_lock", lock)
    updater = SimpleNamespace(
        country_code="CA", delete_authoritative_window=AsyncMock(return_value=4)
    )
    monkeypatch.setattr(
        UpdaterCommitStrategy, "commit", AsyncMock(return_value="stored")
    )
    strategy = CanadaCndssWindowCommitStrategy()
    assert await strategy.commit("db", updater=updater, start_year=2023) == "stored"
    lock.assert_awaited_once_with("db")
    updater.delete_authoritative_window.assert_awaited_once_with("db", start_year=2023)
    updater.country_code = "CA-ON"
    with pytest.raises(ValueError, match="national Canada"):
        await strategy.commit("db", updater=updater, start_year=2023)
    assert updater.delete_authoritative_window.await_count == 1
