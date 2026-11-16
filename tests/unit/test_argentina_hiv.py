"""National totals, paired-original verification, and fail-closed replay."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

from src.core.ar_hiv_source import (
    CATALOGUE_ID, CATALOGUE_URL, CSV_RESOURCE_ID, CSV_URL, DATASET_ID,
    DEFINITION_VERSION, METRIC_TYPE, NATIONAL_DIMENSIONS, SOURCE_ID,
    SOURCE_SCOPE, XLSX_RESOURCE_ID, XLSX_URL,
)
from src.data.crawlers.ar_hiv import (
    ArgentinaHIVAnnualCrawler, ArgentinaHIVContractError, INTERMEDIATE_PATH,
    INTERMEDIATE_SHA256, INTERMEDIATE_URL, SOURCE_COLUMNS, discover_resources,
    parse_hiv_payload, verified_health_ca_bundle,
)
from src.data.processors.ar_hiv import ArgentinaHIVAnnualUpdater
from src.services.ingestion.countries import ar_hiv


def source_rows():
    records = []
    for year in range(2014, 2024):
        records.extend([
            dict(zip(SOURCE_COLUMNS, ["3", "Ambos sexos", "ARG", "200", str(year), str(year - 2000)])),
            dict(zip(SOURCE_COLUMNS, ["3", "Ambos sexos", "Córdoba", "14", str(year), "99999"])),
            dict(zip(SOURCE_COLUMNS, ["1", "Varones", "ARG", "200", str(year), "88888"])),
        ])
    return records


def payloads(rows=None):
    rows = source_rows() if rows is None else rows
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=SOURCE_COLUMNS, delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(SOURCE_COLUMNS)
    for row in rows:
        sheet.append([int(row[k]) if k in {"id_sexo", "jurisdiccion", "anio", "casos_vih"} and str(row[k]).isdigit() else row[k] or None for k in SOURCE_COLUMNS])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return text.getvalue().encode("cp850"), stream.getvalue()


def catalogue():
    return json.dumps({"success": True, "result": {"id": CATALOGUE_ID, "original_identifier": DATASET_ID,
        "resources": [{"original_identifier": rid, "id": f"catalogue-{kind}", "url": url.replace("https:", "http:"),
                       "format": kind.upper(), "last_modified": "2024-12-27", "distribution_license": "CC-BY-4.0"}
                      for kind, rid, url in [("csv", CSV_RESOURCE_ID, CSV_URL), ("xlsx", XLSX_RESOURCE_ID, XLSX_URL)]]}}).encode()


def test_selects_source_reported_national_both_sexes_without_summing():
    csv_bytes, xlsx_bytes = payloads()
    rows, audit = parse_hiv_payload(csv_bytes, xlsx_bytes, retrieved_at="2026-11-16T00:00:00Z")
    assert len(rows) == 10
    assert [row["Cases"] for row in rows] == [str(i) for i in range(14, 24)]
    assert all(row["DefinitionVersion"] == DEFINITION_VERSION and row["Measure"] == METRIC_TYPE for row in rows)
    assert all(json.loads(row["Dimensions"]) == NATIONAL_DIMENSIONS for row in rows)
    assert rows[0]["PeriodStart"] == "2014-01-01"
    assert rows[0]["PeriodEnd"] == "2014-12-31"
    assert rows[0]["SourceRowNumber"] == "2"
    assert json.loads(rows[0]["SourceRawRecord"])["jurisdiccion"] == "200"
    assert audit["source_rows"] == 30 and audit["csv_xlsx_all_rows_equal"]
    assert audit["aggregation"] == "none_source_reported_national_total"


def test_real_zero_and_source_missing_remain_different():
    records = source_rows()
    records[0]["casos_vih"] = "0"
    records[3]["casos_vih"] = ""
    rows, _ = parse_hiv_payload(*payloads(records))
    assert (rows[0]["Cases"], rows[0]["MissingValueStatus"]) == ("0", "")
    assert (rows[1]["Cases"], rows[1]["MissingValueStatus"]) == ("", "source_missing")


@pytest.mark.parametrize("field,value", [("jurisdiccion", "199"), ("id_sexo", "2"), ("anio", "2024"), ("casos_vih", "1.5"), ("casos_vih", "-1")])
def test_changed_national_period_or_count_contract_fails(field, value):
    records = source_rows()
    records[0][field] = value
    with pytest.raises(ArgentinaHIVContractError):
        parse_hiv_payload(*payloads(records))


def test_rejects_duplicates_and_missing_national_years():
    rows = source_rows()
    with pytest.raises(ArgentinaHIVContractError, match="Duplicate"):
        parse_hiv_payload(*payloads(rows + [rows[0]]))
    with pytest.raises(ArgentinaHIVContractError, match="per reviewed year"):
        parse_hiv_payload(*payloads(rows[1:]))


def test_even_excluded_province_rows_must_agree_between_originals():
    csv_bytes, _ = payloads()
    changed = source_rows()
    changed[1]["casos_vih"] = "123"
    _, xlsx_bytes = payloads(changed)
    with pytest.raises(ArgentinaHIVContractError, match="every source row"):
        parse_hiv_payload(csv_bytes, xlsx_bytes)


def test_catalogue_is_bound_to_reviewed_resource_ids_and_official_url():
    _, resources = discover_resources(catalogue())
    assert resources["csv"]["url"] == CSV_URL
    changed = json.loads(catalogue())
    changed["result"]["resources"][0]["url"] = "https://elsewhere.test/hiv.csv"
    with pytest.raises(ArgentinaHIVContractError):
        discover_resources(json.dumps(changed).encode())
    changed = json.loads(catalogue())
    changed["result"]["resources"].pop()
    with pytest.raises(ArgentinaHIVContractError):
        discover_resources(json.dumps(changed).encode())


def test_health_transport_retains_tls_verification_and_rejects_redirect(monkeypatch):
    crawler = ArgentinaHIVAnnualCrawler()
    observed = {}
    def get(url, **kwargs):
        observed.update(kwargs)
        assert Path(kwargs["verify"]).read_bytes().endswith(INTERMEDIATE_PATH.read_bytes())
        return SimpleNamespace(status_code=200, url=url)
    monkeypatch.setattr(crawler, "get", get)
    assert crawler.get_health_resource(CSV_URL).url == CSV_URL
    assert observed["allow_redirects"] is False and observed["verify"] is not False
    with pytest.raises(ArgentinaHIVContractError):
        crawler.get_health_resource("http://datos.salud.gob.ar/data.csv")
    with pytest.raises(ArgentinaHIVContractError):
        crawler.get_health_resource("https://other.test/data.csv")
    monkeypatch.setattr(crawler, "get", lambda *a, **kw: SimpleNamespace(status_code=302, url=CSV_URL))
    with pytest.raises(ArgentinaHIVContractError):
        crawler.get_health_resource(CSV_URL)
    crawler.session.close()


def test_updater_does_not_clip_annual_data_to_month_windows():
    rows, audit = parse_hiv_payload(*payloads())
    updater = ArgentinaHIVAnnualUpdater()
    result = updater.prepare_rows(rows, months=[(2023, 11)], source_audit=audit)
    assert len(result.rows) == 10 and result.source_latest_date.year == 2023
    assert updater.ontology_source_id == SOURCE_ID
    with pytest.raises(ValueError):
        updater.normalize_source("ar_ben_weekly")
    with pytest.raises(ValueError):
        updater.prepare_rows(rows, start_year=2024)


class MemoryWriter:
    def __init__(self):
        self.artifacts = []
        self.payloads = {}

    async def write(self, payload, *, source_url, kind, metadata=None):
        key = (metadata or {})["endpoint"]
        self.payloads[key] = payload
        artifact = SimpleNamespace(source_url=source_url, kind=kind, metadata_=dict(metadata or {}),
                                   sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload))
        self.artifacts.append(artifact)
        return artifact


class FakeCrawler:
    closed = False
    def __init__(self):
        self.session = SimpleNamespace(close=lambda: setattr(self, "closed", True))
    def get(self, url):
        assert url == CATALOGUE_URL
        return SimpleNamespace(content=catalogue(), url=url)
    def get_health_resource(self, url):
        return SimpleNamespace(content=payloads()[0 if url == CSV_URL else 1], url=url)


def test_offline_replay_binds_every_artifact_and_never_uses_network(monkeypatch):
    writer = MemoryWriter()
    asyncio.run(ar_hiv.collect(writer, crawler_type=FakeCrawler))
    monkeypatch.setattr(ar_hiv, "read_artifact", lambda item: writer.payloads[item.metadata_["endpoint"]])
    monkeypatch.setattr(ArgentinaHIVAnnualCrawler, "get", lambda *a, **kw: pytest.fail("Network in organizer"))
    result = ar_hiv.organize(writer.artifacts, updater=ArgentinaHIVAnnualUpdater())
    assert len(result.rows) == 10 and result.source_audit["csv_xlsx_all_rows_equal"]
    assert len(writer.artifacts) == 5
    csv_artifact = next(a for a in writer.artifacts if a.metadata_["endpoint"] == "ar_hiv_csv")
    csv_artifact.sha256 = "0" * 64
    with pytest.raises(ArgentinaHIVContractError, match="does not match"):
        ar_hiv.organize(writer.artifacts, updater=ArgentinaHIVAnnualUpdater())


def test_capture_persistence_failure_stops_collection():
    class Broken(MemoryWriter):
        async def write(self, *args, **kwargs):
            raise OSError("disk write failed")
    with pytest.raises(OSError, match="disk write failed"):
        asyncio.run(ar_hiv.collect(Broken(), crawler_type=FakeCrawler))


def test_offline_collector_default_uses_raw_manifest_recorded_in_audit(tmp_path):
    from scripts.imports import collect_ar_hiv
    output = tmp_path / "current"
    output.mkdir()
    (output / "artifacts_manifest.json").write_text("[]")
    raw_path = "data/raw/ar/hiv_annual/verified-version/artifacts_manifest.json"
    (output / "source_audit.json").write_text(json.dumps({"artifacts_manifest": raw_path}))
    resolved = collect_ar_hiv.resolve_manifest_path(output=output)
    assert resolved == collect_ar_hiv.ROOT / raw_path
    assert resolved != output / "artifacts_manifest.json"
    # Explicit paths are still passed to the standard raw-root validator.
    assert collect_ar_hiv.resolve_manifest_path(output / "artifacts_manifest.json", output=output) == output / "artifacts_manifest.json"
    with pytest.raises(ValueError, match="Archive manifest must be under"):
        collect_ar_hiv.load_archive_manifest(output / "artifacts_manifest.json", "AR")


def test_offline_collector_missing_manifest_pointer_fails_clearly(tmp_path):
    from scripts.imports.collect_ar_hiv import resolve_manifest_path
    with pytest.raises(ValueError, match="run live collection first"):
        resolve_manifest_path(output=tmp_path)
