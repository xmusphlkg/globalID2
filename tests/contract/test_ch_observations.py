"""FOPH national/CHFL and canton geographies preserve source facts."""
import hashlib
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.ch import SwitzerlandIDDCrawler, CHSeriesSource, CH_CANTONS
from src.data.processors.ch import CHMonthlyUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.ingestion.countries.ch import organize
from tests.contract.compare import assert_observations_match

FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/CH/series.json"


@pytest.mark.parametrize("country", ["CH", *[f"CH-{code}" for code in CH_CANTONS]])
@pytest.mark.parametrize("recover_from_history", [False, True])
def test_ch_source_and_observation_contract(country, tmp_path, monkeypatch, recover_from_history):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "idd.csv"
    subtype = country != "CH"
    payload = json.loads(FIXTURE.read_bytes())
    if subtype:
        for point in payload["values"]:
            point["x"] = str(point["x"])[:4]
        payload["values"] = payload["values"][:1]
    series = CHSeriesSource("aids" if subtype else "hiv", "AIDS" if subtype else "Human Immunodeficiency Virus (HIV)", ("aids" if subtype else "hiv") + "/cases/value/" + ("year" if subtype else "month"), "year" if subtype else "month", {}, country.removeprefix("CH-") if subtype else "CHFL", "2025-03-01")
    url = SwitzerlandIDDCrawler()._api_url(f"data/{series.identifier}")
    monkeypatch.setattr(SwitzerlandIDDCrawler, "fetch_version", lambda self: "fixture")
    monkeypatch.setattr(SwitzerlandIDDCrawler, "fetch_series_sources", lambda self, **kw: [series])
    monkeypatch.setattr(SwitzerlandIDDCrawler, "fetch_sets", lambda self: [series.identifier])
    monkeypatch.setattr(SwitzerlandIDDCrawler, "get", lambda self, url: SimpleNamespace(json=lambda: {"availableSeriesConfigurations": [{"canton": country.removeprefix("CH-"), "georegion": "canton", "sex": "all", "agegroup": "all"}]}))
    monkeypatch.setattr(SwitzerlandIDDCrawler, "_fetch_one_series", lambda self, s: self.parse_series_payload(s, payload, url=url))
    expected = CHMonthlyUpdater(country_code=country, output_csv=output).refresh_source(force=True, start_year=2024)
    encoded = json.dumps(payload).encode()
    path = Path("data/raw/ch/contract/response.json")
    path.parent.mkdir(parents=True)
    path.write_bytes(encoded)
    artifact = SimpleNamespace(relative_path=str(path), byte_size=len(encoded), sha256=hashlib.sha256(encoded).hexdigest(), kind="idd_series", source_url=url, metadata_={"series": asdict(series)})
    artifacts = [artifact]
    if recover_from_history:
        cached = expected.source_csv.read_bytes()
        cache_path = path.with_name("history.csv")
        cache_path.write_bytes(cached)
        artifacts.insert(0, SimpleNamespace(relative_path=str(cache_path), byte_size=len(cached), sha256=hashlib.sha256(cached).hexdigest(), kind="history_csv"))
        def invalid_live(*args, **kwargs):
            raise ValueError("invalid live IDD JSON")
        monkeypatch.setattr(SwitzerlandIDDCrawler, "_fetch_one_series", invalid_live)
        expected = CHMonthlyUpdater(country_code=country, output_csv=output).refresh_source(force=True, start_year=2024)
        invalid = b"invalid live IDD JSON"
        path.write_bytes(invalid)
        artifact.byte_size = len(invalid)
        artifact.sha256 = hashlib.sha256(invalid).hexdigest()
    monkeypatch.setattr(SwitzerlandIDDCrawler, "get", lambda *a, **kw: pytest.fail("offline parser requested HTTP"))
    actual = organize(artifacts, updater=CHMonthlyUpdater(country_code=country, output_csv=output), source="all", country_code=country, history_csv=output, force=True, start_year=2024)
    assert actual.rows == expected.rows
    store = SeriesObservationStore()
    before = store.build_observations(expected.rows, "CH", source_id="SRC_CH_FOPH_IDD").observations
    after = store.build_observations(actual.rows, "CH", source_id="SRC_CH_FOPH_IDD").observations
    assert after
    assert_observations_match(after, before)
