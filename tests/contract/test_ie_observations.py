"""HPSC three independent source contracts: weekly, annual and archive PDF."""

import json
from pathlib import Path
from urllib.parse import urlencode

import pytest
import requests
from src.data.crawlers.ie_annual import ANNUAL_REPORTS, DEFAULT_ANNUAL_INDEX_URL
from src.data.crawlers.ie_weekly_archive import (
    DEFAULT_LENUS_DISCOVERY_URL,
    DEFAULT_WAYBACK_CDX_URL,
)
from src.data.processors.ie import IEWeeklyUpdater
from src.data.processors.ie_annual import IEAnnualUpdater
from src.data.processors.ie_weekly_archive import IEWeeklyArchiveUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.ingestion.countries import ie
from tests.contract.compare import assert_observations_match, assert_records_match
from tests.contract.test_no_observations import Writer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/IE"


def install(monkeypatch):
    annual = {
        report.url: FIXTURES / f"{report.report_id}.pdf" for report in ANNUAL_REPORTS
    }
    item = {
        "id": "item-20",
        "handle": "10147/638115",
        "metadata": {},
        "name": "Statutory Notifications of Infectious Diseases reported in Ireland via the Computerised Infectious Disease Reporting (CIDR) system for: Week 20, 2020 (Notification Period: 10/05/2020 - 16/05/2020)",
    }
    import json

    def get(self, url, **kwargs):
        params = kwargs.get("params") or {}
        options = dict(params)
        if url == DEFAULT_ANNUAL_INDEX_URL:
            raw = "".join(
                f'<a href="{report.url}">Annual Infectious Disease Notifications in Ireland '
                f'{report.report_id.rsplit("_", 2)[-2]} - {report.report_id.rsplit("_", 2)[-1]}</a>'
                for report in ANNUAL_REPORTS
            ).encode()
        elif url in annual:
            raw = annual[url].read_bytes()
        elif url == DEFAULT_LENUS_DISCOVERY_URL:
            raw = json.dumps(
                {
                    "_embedded": {
                        "searchResult": {
                            "_embedded": {
                                "objects": [{"_embedded": {"indexableObject": item}}]
                            },
                            "page": {"totalPages": 1},
                        }
                    }
                }
            ).encode()
        elif url == DEFAULT_WAYBACK_CDX_URL:
            raw = json.dumps(
                [["timestamp", "original", "statuscode", "mimetype", "digest"]]
            ).encode()
        elif url.endswith("/bundles"):
            raw = json.dumps(
                {
                    "_embedded": {
                        "bundles": [
                            {
                                "name": "ORIGINAL",
                                "_links": {
                                    "bitstreams": {
                                        "href": "https://fixture.test/bitstreams"
                                    }
                                },
                            }
                        ]
                    }
                }
            ).encode()
        elif url == "https://fixture.test/bitstreams":
            raw = json.dumps(
                {
                    "_embedded": {
                        "bitstreams": [
                            {
                                "name": "weekly.pdf",
                                "id": "pdf-20",
                                "_links": {
                                    "content": {
                                        "href": "https://fixture.test/week20.pdf"
                                    }
                                },
                            }
                        ]
                    }
                }
            ).encode()
        elif url == "https://fixture.test/week20.pdf":
            raw = (FIXTURES / "weekly_archive_2020_w20.pdf").read_bytes()
        elif "outStatistics" in options:
            raw = (FIXTURES / "bounds.json").read_bytes()
        elif options.get("outFields") == "Disease":
            raw = (FIXTURES / "catalogue.json").read_bytes()
        elif "resultOffset" in options:
            raw = (FIXTURES / "weekly.json").read_bytes()
        else:
            raw = (FIXTURES / "metadata.json").read_bytes()
        response = requests.Response()
        response.status_code = 200
        response.url = url + ("?" + urlencode(params, doseq=True) if params else "")
        response._content = raw
        response._content_consumed = True
        response.headers["Content-Type"] = (
            "application/pdf" if raw.startswith(b"%PDF") else "application/json"
        )
        return response

    monkeypatch.setattr(requests.Session, "get", get)


@pytest.mark.parametrize(
    "source,updater_type,start",
    [
        ("hpsc_ndh", IEWeeklyUpdater, 2021),
        ("hpsc_annual", IEAnnualUpdater, 2004),
        ("hpsc_weekly_archive", IEWeeklyArchiveUpdater, 2020),
    ],
)
@pytest.mark.parametrize(
    "force,fill_missing", [(False, False), (True, False), (False, True)]
)
async def test_hpsc_all_source_contracts_keep_observations_and_permission_gate(
    source, updater_type, start, force, fill_missing, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    updater = updater_type(
        output_csv=tmp_path / "source.csv",
        **({"refresh_recent_weeks": 2} if source == "hpsc_ndh" else {}),
    )
    expected = updater.refresh_source(
        source=source, force=force, fill_missing=fill_missing, start_year=start
    )
    updater.output_csv.unlink()
    writer = Writer("ie")
    import src.data.crawlers.ie as weekly
    import src.data.crawlers.ie_annual as annual
    import src.data.crawlers.ie_weekly_archive as archive

    # Fail if collect enters any source-business parser.
    with monkeypatch.context() as guard:
        guard.setattr(
            weekly,
            "validate_feature_page",
            lambda *a, **k: pytest.fail("collect parsed cases"),
        )
        guard.setattr(
            annual,
            "parse_annual_pdf",
            lambda *a, **k: pytest.fail("collect parsed annual cases"),
        )
        guard.setattr(
            archive,
            "parse_weekly_archive_pdf",
            lambda *a, **k: pytest.fail("collect parsed archive cases"),
        )
        artifacts = await ie.collect(
            writer,
            updater=updater,
            source=source,
            force=force,
            fill_missing=fill_missing,
            start_year=start,
        )
    monkeypatch.setattr(
        requests.Session,
        "get",
        lambda *a, **k: pytest.fail("organize requested source"),
    )
    actual = ie.organize(
        artifacts,
        updater=updater,
        source=source,
        force=force,
        fill_missing=fill_missing,
        start_year=start,
    )
    strip = lambda rows: [
        {k: v for k, v in row.items() if k not in {"RetrievedAt", "RawArtifact"}}
        for row in rows
    ]
    assert strip(actual.rows) == strip(expected.rows)
    assert updater.public_release_enabled is False
    assert all(row["PublicReleaseEnabled"] == "false" for row in actual.rows)
    assert any(row["Cases"] == "0" for row in actual.rows)
    store = SeriesObservationStore()
    actual_obs = store.build_observations(
        actual.rows, "IE", source_id=updater.ontology_source_id
    ).observations
    expected_obs = store.build_observations(
        expected.rows, "IE", source_id=updater.ontology_source_id
    ).observations
    assert actual_obs
    assert_observations_match(actual_obs, expected_obs)
    golden = json.loads((FIXTURES / "native_observations.json").read_text())
    golden_mode = (
        "full" if force or (source == "hpsc_annual" and fill_missing) else "recent"
    )
    assert_observations_match(actual_obs, golden[f"{source}:{golden_mode}"])
    if source == "hpsc_ndh":
        from src.data.processors.ie import build_legacy_projection

        old_records, _, _ = build_legacy_projection(
            expected.rows, {"measles": 11, "anthrax": 12}, country_id=1
        )
        new_records, _, _ = build_legacy_projection(
            actual.rows, {"measles": 11, "anthrax": 12}, country_id=1
        )
        assert_records_match(new_records, old_records)
    else:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        db = SimpleNamespace(execute=AsyncMock())
        await updater.import_rows(
            db,
            actual.rows,
            db_latest_date=None,
            source_latest_date=actual.source_latest_date,
        )
        db.execute.assert_not_awaited()  # Historical sources never enter merged disease_records.
    if source == "hpsc_ndh":
        assert any(row["ValueStatus"] == "missing" for row in actual.rows)
    if source == "hpsc_annual":
        assert any(row["ValueStatus"] == "not_applicable" for row in actual.rows)
    if source == "hpsc_weekly_archive":
        assert actual.periods_fetched == ((2020, 20),)
        assert actual.missing_periods
        assert {row["Date"] for row in actual.rows} == {"2020-05-11"}
