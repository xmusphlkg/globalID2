"""Norway retains its own closed-month/future-zero policy and source keys."""

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import requests
from src.data.processors.no import NOMonthlyUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.ingestion.countries import no
from tests.contract.compare import assert_observations_match, assert_records_match

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/NO"


class Writer:
    task_uuid = "contract"
    attempt = 1

    def __init__(self, country="no"):
        self.artifacts = []
        self.country = country

    async def write(self, raw, *, source_url, kind, metadata=None, **kwargs):
        path = Path(f"data/raw/{self.country}/contract") / f"{len(self.artifacts)}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        artifact = SimpleNamespace(
            relative_path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(),
            byte_size=len(raw),
            source_url=source_url,
            kind=kind,
            metadata_=metadata or {},
            created_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        )
        self.artifacts.append(artifact)
        return artifact


def install(monkeypatch):
    def get(self, url, **kwargs):
        path = FIXTURES / (
            "diagnoses.json" if url.endswith("/kodeverk/diagnoser") else "monthly.json"
        )
        response = requests.Response()
        response.status_code = 200
        params = kwargs.get("params") or []
        response.url = url + ("?" + urlencode(params, doseq=True) if params else "")
        response.headers["Content-Type"] = "application/json"
        response._content = path.read_bytes()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.Session, "get", get)


@pytest.mark.parametrize(
    "force,include_current", [(False, False), (True, False), (False, True)]
)
async def test_no_offline_contract_preserves_closed_months_and_explicit_zero(
    tmp_path, monkeypatch, force, include_current
):
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    updater = NOMonthlyUpdater(
        output_csv=tmp_path / "source.csv",
        include_current_month=include_current,
        today_provider=lambda: date(2026, 8, 7),
    )
    months = [(2026, m) for m in (6, 7, 8, 9)]
    expected = updater.refresh_source(source="fhi_msis", force=force, months=months)
    # Collection must never run the case parser, even when metadata is decoded.
    import src.data.crawlers.no as crawler_module

    with monkeypatch.context() as guard:
        guard.setattr(
            crawler_module,
            "validate_monthly_payload",
            lambda *a, **k: pytest.fail("collect parsed cases"),
        )
        writer = Writer()
        saved = await no.collect(
            writer, source="fhi_msis", updater=updater, force=force, months=months
        )
    monkeypatch.setattr(
        requests.Session,
        "get",
        lambda *a, **k: pytest.fail("organize requested source"),
    )
    actual = no.organize(
        saved, updater=updater, source="fhi_msis", force=force, months=months
    )
    strip = lambda rows: [
        {k: v for k, v in row.items() if k not in {"RetrievedAt", "RawArtifact"}}
        for row in rows
    ]
    assert strip(actual.rows) == strip(expected.rows)
    assert any(row["Cases"] == "0" for row in actual.rows)
    assert all(
        date.fromisoformat(row["Date"]) < date(2026, 9, 1) for row in actual.rows
    )
    if not include_current:
        assert all(row["DataStatus"] == "closed" for row in actual.rows)
    store = SeriesObservationStore()
    expected_obs = store.build_observations(
        expected.rows, "NO", source_id="SRC_NO_FHI_MSIS"
    ).observations
    actual_obs = store.build_observations(
        actual.rows, "NO", source_id="SRC_NO_FHI_MSIS"
    ).observations
    assert actual_obs
    assert_observations_match(actual_obs, expected_obs)
    golden = json.loads((FIXTURES / "native_observations.json").read_text())
    assert_observations_match(
        actual_obs, golden["provisional" if include_current else "closed"]
    )
    from unittest.mock import AsyncMock

    monkeypatch.setattr(updater, "_get_country_id", AsyncMock(return_value=1))
    monkeypatch.setattr(
        updater,
        "_load_mapping_dict",
        AsyncMock(return_value={"difteri": 11, "kikhoste": 12}),
    )
    records = []

    async def execute(query, rows):
        records.append(rows)

    db = SimpleNamespace(execute=execute)
    for rows in (expected.rows, actual.rows):
        await updater.import_rows(
            db, rows, db_latest_date=None, source_latest_date=actual.source_latest_date
        )
    assert_records_match(records[1], records[0])


async def test_no_source_failure_keeps_validated_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    updater = NOMonthlyUpdater(
        output_csv=tmp_path / "source.csv", today_provider=lambda: date(2026, 8, 7)
    )
    expected = updater.refresh_source(months=[(2026, 7)])
    monkeypatch.setattr(
        requests.Session,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("upstream failed")),
    )
    saved = await no.collect(
        Writer(), source="fhi_msis", updater=updater, months=[(2026, 7)]
    )
    actual = no.organize(saved, updater=updater, source="fhi_msis", months=[(2026, 7)])
    assert actual.rows == expected.rows
    assert any("previous CSV snapshot" in entry for entry in actual.script_logs)


def test_no_pipeline_retains_country_local_dispatch():
    from src.services.crawl_pipelines.no import execute_no_pipeline
    import inspect

    source = inspect.getsource(execute_no_pipeline)
    assert "run_surveillance" in source
    assert "_execute_configured_monthly" not in source
