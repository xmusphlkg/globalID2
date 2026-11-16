"""Official SIVIGILA municipality and source-version contracts."""
from __future__ import annotations

import csv
from concurrent.futures import Future
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests

from src.data.crawlers.co import (
    ColombiaINSSivigilaCrawler, ColombiaSIVIGILAContractError, FIELDS,
    can_skip_import, parse_count,
    parse_metadata, parse_page,
)
from src.data.processors.co import ColombiaINSSivigilaUpdater
from scripts.imports.co_sivigila_history import (
    merge_year_states, next_ready_future, stored_row_matches_source,
)


VERSION = {"rows_updated_at": 1729208137, "view_last_modified": 1779130843}
PAGE_URL = "https://www.datos.gov.co/resource/4hyg-wa9d.csv?$where=ano%20%3D%202008"


def csv_page(*records):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    for record in records:
        writer.writerow(record)
    return stream.getvalue().encode()


def record(**changes):
    row = dict(cod_eve="210", nombre_evento="DENGUE", semana="53", ano="2008",
               cod_dpto_o="8", cod_mun_o="8372", departamento_ocurrencia="ATLANTICO",
               municipio_ocurrencia="JUAN DE ACOSTA", conteo="0", socrata_id="row-a")
    row.update(changes)
    return row


def parse(payload, expected):
    return parse_page(payload, year=2008, offset=0, expected_rows=expected,
                      source_url=PAGE_URL, dataset_version=VERSION)


def test_zero_week53_and_non_national_geography():
    rows, audit = parse(csv_page(record()), 1)
    row = rows[0]
    assert row["Cases"] == "0" and row["Date"] == "2008-12-30"
    assert row["GeographyKey"] == "country:CO:source-municipality:08372"
    assert row["PublicReleaseEnabled"] == "false"
    assert json.loads(row["Dimensions"])["date_role"] == "synthetic_week_ordinal_anchor_not_event_date"
    assert audit["national_aggregate_derived"] is False


def test_external_and_unknown_municipality_never_become_colombian_municipalities():
    rows, _ = parse(csv_page(
        record(cod_dpto_o="1", cod_mun_o="1862", departamento_ocurrencia="EXTERIOR",
               municipio_ocurrencia="EXTERIOR_VENEZUELA", socrata_id="row-b"),
        record(cod_dpto_o="5", cod_mun_o="5000", departamento_ocurrencia="ANTIOQUIA",
               municipio_ocurrencia="* ANTIOQUIA. MUNICIPIO DESCONOCIDO", socrata_id="row-c")), 2)
    assert rows[0]["LocationType"] == "source_external"
    assert rows[0]["GeographyKey"].startswith("source:SRC_CO_INS_SIVIGILA:exterior:")
    assert rows[1]["LocationType"] == "source_unknown"
    assert rows[1]["GeographyKey"] == "source:SRC_CO_INS_SIVIGILA:unknown-location:05"


@pytest.mark.parametrize("changes", [
    {"ano": "2009"}, {"semana": "54"}, {"conteo": "-1"},
    {"cod_mun_o": "25001"}, {"nombre_evento": ""},
])
def test_invalid_source_cells_fail_closed(changes):
    with pytest.raises(ColombiaSIVIGILAContractError):
        parse(csv_page(record(**changes)), 1)


def test_truncation_duplicate_and_changed_header_fail_closed():
    with pytest.raises(ColombiaSIVIGILAContractError, match="Truncated"):
        parse(csv_page(record()), 2)
    with pytest.raises(ColombiaSIVIGILAContractError, match="Duplicate"):
        parse(csv_page(record(), record(socrata_id="row-b")), 2)
    with pytest.raises(ColombiaSIVIGILAContractError, match="schema"):
        parse(b"unexpected,header\n1,2\n", 1)


def test_official_metadata_and_year_count_are_explicit():
    metadata = {"id": "4hyg-wa9d", "attribution": "Instituto Nacional de Salud - INS, Bogotá D.C.",
                "columns": [{"fieldName": field} for field in FIELDS[:-1]],
                "rowsUpdatedAt": VERSION["rows_updated_at"],
                "viewLastModified": VERSION["view_last_modified"], "licenseId": "CC_40_BY_SA"}
    assert parse_metadata(json.dumps(metadata).encode())["rows_updated_at"] == 1729208137
    assert parse_count(b'[{"n":"48305"}]') == 48305
    with pytest.raises(ColombiaSIVIGILAContractError):
        parse_metadata(json.dumps({**metadata, "id": "wrong"}).encode())
    with pytest.raises(ColombiaSIVIGILAContractError):
        parse_count(b'[{"n":"-1"}]')
    with pytest.raises(ValueError):
        ColombiaINSSivigilaUpdater.normalize_source("another")


def test_unchanged_metadata_cannot_skip_partial_or_unreconciled_history():
    counts = {"2007": 2, "2008": 3}
    version = {"rows_updated_at": 1, "view_last_modified": 2}
    state = {"status": "complete", "completed_rows": 5, "expected_rows": 5,
             "identity": {"dataset_version": version, "year_counts": counts,
                          "start_year": 2007, "end_year": 2008}}
    acceptance = {"complete": True, "dataset_version": version,
                  "years": {year: {"official_rows": total, "database_rows": total,
                                   "complete": True} for year, total in counts.items()}}
    assert can_skip_import(version, counts, state, acceptance)
    assert not can_skip_import(version, counts, {**state, "status": "running"}, acceptance)
    assert not can_skip_import(version, counts, state, {**acceptance, "complete": False})
    assert not can_skip_import({**version, "rows_updated_at": 3}, counts, state, acceptance)
    assert not can_skip_import(version, {**counts, "2009": 1}, state, acceptance)


def test_transient_official_tls_failure_is_retried_without_cached_success(monkeypatch):
    class Session:
        headers = {}
        calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise requests.exceptions.SSLError("temporary TLS EOF")
            response = requests.Response()
            response.status_code = 200
            response.url = url
            response._content = b"official"
            return response

    session = Session()
    monkeypatch.setattr("src.data.crawlers.co.time.sleep", lambda _: None)
    response = ColombiaINSSivigilaCrawler(session=session).get(PAGE_URL)
    assert response.content == b"official" and session.calls == 2


def test_year_state_recovers_applied_page_without_downgrading_aggregate(tmp_path):
    identity = {"dataset_version": VERSION, "year_counts": {"2008": 3},
                "page_limit": 10000}
    year_dir = tmp_path / "2008"
    year_dir.mkdir()
    (year_dir / "year-state.json").write_text(json.dumps({
        "identity": {"dataset_version": VERSION, "year": 2008,
                     "year_rows": 3, "page_limit": 10000},
        "batches": {"2008:0": {"year": 2008, "rows": 3,
                               "organized": True, "applied": True,
                               "task_uuid": "completed-task"}},
    }))
    state = {"batches": {"2008:0": {"year": 2008, "rows": 3,
                                    "organized": True, "applied": False}}}
    merge_year_states(state, tmp_path, identity)
    assert state["batches"]["2008:0"]["task_uuid"] == "completed-task"
    state["batches"]["2008:0"]["task_uuid"] = "newer-task"
    merge_year_states(state, tmp_path, identity)
    assert state["batches"]["2008:0"]["task_uuid"] == "completed-task"


def test_recovery_requires_exact_stored_identity_and_value():
    source = {"Date": "2008-12-30", "GeographyKey": "country:CO:source-municipality:08372",
              "Cases": "7", "SourceRecordID": "row-a", "SourceDiseaseCode": "sivigila_210",
              "Dimensions": '{"source_epi_week": 53}', "RawSHA256": "source-hash"}
    stored = SimpleNamespace(time=datetime(2008, 12, 30, tzinfo=timezone.utc),
                             geography_key=source["GeographyKey"], value=7,
                             raw_data=source.copy())
    assert stored_row_matches_source(stored, source)
    assert not stored_row_matches_source(SimpleNamespace(**{**vars(stored), "value": 8}), source)
    assert not stored_row_matches_source(SimpleNamespace(**{**vars(stored),
        "geography_key": "country:CO:source-municipality:99999"}), source)
    assert not stored_row_matches_source(SimpleNamespace(**{**vars(stored),
        "raw_data": {**source, "RawSHA256": "another-page"}}), source)


def test_completed_capture_pages_are_drained_in_submission_order():
    older, newer = Future(), Future()
    newer.set_result("newer year")
    older.set_result("older year")
    assert next_ready_future({older: 2012, newer: 2016}) is older
