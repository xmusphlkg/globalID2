"""Every ECDC country has a captured fixture and preserves source observations."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRY_CODES
from src.data.crawlers.ecdc import ECDCAtlasCrawler
from src.data.processors.ecdc import ECDCAnnualUpdater
from src.services.ingestion.countries import ecdc
from src.services.ingestion.strategies import (
    EcdcWindowCommitStrategy,
    UpdaterCommitStrategy,
)
from tests.contract.native import assert_native_observations_match

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion"


def save_artifact(payload, metadata, index):
    raw = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    path = Path("data/raw/ecdc/contract") / f"{index}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return SimpleNamespace(
        relative_path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_size=len(raw),
        kind="archive_json",
        metadata_=metadata,
    )


def fixture_requests(country):
    config = json.loads((FIXTURES / country / "ecdc.json").read_text())
    return json.loads(
        (FIXTURES / "ECDC/responses.json")
        .read_text()
        .replace("__GEO__", config["source_geo_code"])
    )


def stable(rows):
    return [
        {key: value for key, value in row.items() if key != "RetrievedAt"}
        for row in rows
    ]


@pytest.mark.parametrize("country", sorted(ECDC_BASELINE_COUNTRY_CODES))
def test_ecdc_country_contract_matches_previous_parser(country, tmp_path, monkeypatch):
    requests = fixture_requests(country)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "history.csv"
    output.write_text(
        "Date,SourceDiseaseCode,Cases\n1989-01-01,anth,1\n1988-01-01,unknown,\nbad-date,invalid,7\n2020-01-01,withdrawn,9\n"
    )
    history = save_artifact(output.read_bytes(), {}, len(requests))
    history.kind = "history_csv"
    artifacts = [
        save_artifact(
            r["payload"], {"endpoint": r["endpoint"], "params": r["params"]}, i
        )
        for i, r in enumerate(requests)
    ] + [history]
    lookup = {
        (r["endpoint"], tuple(sorted(r["params"].items()))): r["payload"]
        for r in requests
    }
    crawler = ECDCAtlasCrawler(country)
    monkeypatch.setattr(
        crawler,
        "_json",
        lambda endpoint, **params: lookup[(endpoint, tuple(sorted(params.items())))],
    )
    crawler.crawl_annual_baseline(output, start_year=2020)
    expected = ECDCAnnualUpdater(country, output_csv=output)._load_rows()
    monkeypatch.setattr(
        ECDCAtlasCrawler,
        "get",
        lambda *a, **kw: pytest.fail("organize requested a source"),
    )
    actual = ecdc.organize(
        artifacts,
        updater=ECDCAnnualUpdater(country, output_csv=output),
        source="ecdc",
        country_code=country,
        start_year=2020,
    )
    assert stable(actual.rows) == stable(expected)
    assert_native_observations_match(actual.rows, expected, ECDCAnnualUpdater(country))
    assert any(row["Date"] == "1989-01-01" for row in actual.rows)
    assert not any(row["SourceDiseaseCode"] == "withdrawn" for row in actual.rows)
    assert not any(row["Date"][:4] in {"2022", "2026"} for row in actual.rows)
    assert any(row["Cases"] == "0" for row in actual.rows)
    assert all(
        row["GeographyKey"] == f"country:{country}:national"
        for row in actual.rows
        if row["Date"][:4] >= "2020"
    )


async def test_ecdc_collect_records_bytes_before_business_parser(tmp_path, monkeypatch):
    requests = fixture_requests("FR")
    lookup = {
        (r["endpoint"], tuple(sorted(r["params"].items()))): r["payload"]
        for r in requests
    }

    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, raw, **kwargs):
            self.artifacts.append((raw, kwargs))

    def get(self, url, params):
        payload = lookup[(url.split("/")[-1], tuple(sorted(params.items())))]
        return SimpleNamespace(content=json.dumps(payload).encode(), url=url)

    monkeypatch.setattr(ECDCAtlasCrawler, "get", get)
    monkeypatch.setattr(
        ECDCAtlasCrawler,
        "parse_topic_payloads",
        lambda *a, **kw: pytest.fail("collect parsed business rows"),
    )
    writer = Writer()
    artifacts = await ecdc.collect(writer, source="ecdc", country_code="FR")
    assert len(artifacts) >= len(requests)
    assert all(
        raw and metadata["kind"] == "archive_json" for raw, metadata in artifacts
    )


async def test_ecdc_delete_is_confined_to_its_commit_strategy(monkeypatch):
    lock = AsyncMock()
    monkeypatch.setattr("src.services.ingestion.strategies.acquire_disease_data_mutation_lock", lock)
    updater = SimpleNamespace(delete_authoritative_window=AsyncMock(return_value=3))
    commit = AsyncMock(return_value="stored")
    monkeypatch.setattr(UpdaterCommitStrategy, "commit", commit)
    assert (
        await EcdcWindowCommitStrategy().commit("db", updater=updater, start_year=2020)
        == "stored"
    )
    lock.assert_awaited_once_with("db")
    updater.delete_authoritative_window.assert_awaited_once_with("db", start_year=2020)
    assert commit.call_args.kwargs["updater"] is updater
