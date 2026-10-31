"""Iceland's five reviewed sources keep observation and legacy projection keys."""

import hashlib
import importlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.is_history import (
    IcelandHistoryRawFile,
    IcelandHistoryWorkbookSpec,
)
from src.data.crawlers.powerbi_public import (
    PowerBIQueryResult,
    PowerBIReportContext,
    decode_dsr_v2,
)
from src.data.processors.is_history import IcelandHistoryProcessor
from src.services.ingestion.countries import is_ as connector
from tests.contract.native import assert_native_observations_match

c = importlib.import_module("src.data.crawlers.is")
p = importlib.import_module("src.data.processors.is")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/IS"


def artifact(raw, kind, meta, index, url="https://example.org/source"):
    extension = "xlsx" if kind == "workbook_xlsx" else "json"
    path = Path("data/raw/is/contract") / f"{index}.{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return SimpleNamespace(
        relative_path=str(path),
        byte_size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        kind=kind,
        metadata_=meta,
        source_url=url,
        created_at=datetime.now(timezone.utc),
    )


def query_fixtures():
    return json.loads((FIXTURES / "current_queries.json").read_text())


@pytest.mark.parametrize("source", ["all", *c.SUPPORTED_SOURCE_SCOPES])
def test_current_source_and_observation_contracts(source, tmp_path, monkeypatch):
    captures = query_fixtures()
    monkeypatch.chdir(tmp_path)
    saved = [
        artifact(
            json.dumps(item["payload"]).encode(),
            "archive_json",
            {
                "endpoint": "query",
                **{key: item[key] for key in ("scope", "entity", "context", "request")},
            },
            i,
        )
        for i, item in enumerate(captures)
    ]

    class PowerBI:
        def discover(self, url):
            return PowerBIReportContext(
                **next(
                    item["context"]
                    for item in captures
                    if item["context"]["view_url"] == url
                ),
                models_payload={},
                schema_payload={},
                landing_html="",
            )

        def query_entity_sum(self, context, **kw):
            item = next(
                item
                for item in captures
                if item["entity"] == kw["entity"]
                and item["context"]["view_url"] == context.view_url
            )
            return PowerBIQueryResult(
                decode_dsr_v2(item["payload"]), item["request"], item["payload"]
            )

    class Crawler(c.IcelandDOHCrawler):
        def __init__(self, **kw):
            super().__init__(powerbi_client=PowerBI(), **kw)

        def discover_view_urls(self):
            return self.configured_view_urls

    monkeypatch.setattr(p, "IcelandDOHCrawler", Crawler)
    output = tmp_path / "is.csv"
    updater = p.ISMultiFrequencyUpdater(output_csv=output)
    expected = updater.refresh_source(source=source)
    monkeypatch.setattr(
        c.IcelandDOHCrawler,
        "get",
        lambda *a, **kw: pytest.fail("organize requested source"),
    )
    actual = connector.organize(saved, updater=updater, source=source)

    def stable(rows):
        return [
            {k: v for k, v in row.items() if k not in {"RetrievedAt", "RawArtifact"}}
            for row in rows
        ]

    assert stable(actual.rows) == stable(expected.rows)
    assert_native_observations_match(actual.rows, expected.rows, updater)
    assert actual.source_row_counts == expected.source_row_counts
    assert all(row["Cases"] == "0" for row in actual.rows)
    if source == "is_doh_respiratory":
        assert (
            actual.rows[0]["Date"] == "2019-12-30"
            and actual.rows[0]["ISOYear"] == "2020"
        )


@pytest.mark.parametrize("source", ["is_doh_history", "is_doh_legacy_icd"])
def test_history_sources_preserve_registry_and_projection_rows(
    source, tmp_path, monkeypatch
):
    specs = [
        IcelandHistoryWorkbookSpec(**item)
        for item in json.loads((FIXTURES / "workbooks.json").read_text())
    ]
    selected = [
        spec
        for spec in specs
        if (spec.source_kind == "legacy_icd_monthly") == (source == "is_doh_legacy_icd")
    ]
    monkeypatch.chdir(tmp_path)
    saved = []
    original = []
    for i, spec in enumerate(selected):
        raw = (FIXTURES / spec.filename).read_bytes()
        a = artifact(raw, "workbook_xlsx", {"spec": asdict(spec)}, i, spec.url)
        saved.append(a)
        original.append(
            IcelandHistoryRawFile(
                key=spec.key,
                source_kind=spec.source_kind,
                filename=spec.filename,
                path=str(FIXTURES / spec.filename),
                source_url=spec.url,
                sha256=a.sha256,
                size_bytes=a.byte_size,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                disease_key=spec.disease_key,
            )
        )
    processor = IcelandHistoryProcessor()
    expected = processor.prepare_files(original)
    actual = connector.organize(saved, updater=processor, source=source)
    assert actual.rows == expected.rows
    assert actual.series_rows == expected.series_rows
    identity = SimpleNamespace(
        country_code="IS",
        ontology_source_id={
            "Iceland Directorate of Health Historical Registry": "SRC_IS_DOH_HISTORY",
            "Iceland Directorate of Health Legacy ICD Monthly": "SRC_IS_DOH_LEGACY_ICD",
        },
        series_geography_key="country:IS:national",
    )
    assert_native_observations_match(actual.series_rows, expected.series_rows, identity)
    assert any(row["Cases"] == "0" for row in actual.series_rows)
    if source == "is_doh_legacy_icd":
        assert actual.rows == [] and all(
            row["Measure"] == "registered_diagnoses" for row in actual.series_rows
        )
    else:
        assert actual.prepared.quarantine


@pytest.mark.parametrize(
    "source,options",
    [
        ("all", {"fill_missing": True}),
        ("is_doh_history", {"start_year": 2020}),
        ("is_doh_legacy_icd", {"start_year": 2020}),
    ],
)
async def test_options_rejected_before_any_source_request(source, options):
    with pytest.raises(ValueError):
        await connector.collect(SimpleNamespace(artifacts=[]), source=source, **options)


async def test_history_collect_downloads_without_parsing(tmp_path, monkeypatch):
    specs = [
        IcelandHistoryWorkbookSpec(**item)
        for item in json.loads((FIXTURES / "workbooks.json").read_text())
    ]
    monkeypatch.setattr(connector, "OFFICIAL_WORKBOOKS", specs)
    monkeypatch.setattr(
        connector.IcelandHistoryCrawler,
        "get",
        lambda self, url: (
            SimpleNamespace(content=(FIXTURES / url.rsplit("/", 1)[1]).read_bytes())
            if url.endswith(".xlsx")
            else SimpleNamespace(content=b"<html></html>")
        ),
    )
    monkeypatch.setattr(
        IcelandHistoryProcessor,
        "prepare_files",
        lambda *a, **kw: pytest.fail("collect parsed workbook"),
    )

    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, raw, **kw):
            self.artifacts.append((raw, kw))

    saved = await connector.collect(Writer(), source="is_doh_history")
    assert (
        len(saved) == 3
        and sum(meta["kind"] == "workbook_xlsx" for _, meta in saved) == 2
    )


async def test_current_collect_captures_queries_without_decoding(monkeypatch):
    captures = query_fixtures()
    monkeypatch.setattr(
        c.IcelandDOHCrawler,
        "discover_view_urls",
        lambda self: self.configured_view_urls,
    )
    monkeypatch.setattr(
        connector.crawler_module.PublicPowerBIClient,
        "discover",
        lambda self, url: PowerBIReportContext(
            **next(
                item["context"]
                for item in captures
                if item["context"]["view_url"] == url
            ),
            models_payload={},
            schema_payload={},
            landing_html="",
        ),
    )
    monkeypatch.setattr(
        connector.crawler_module.PublicPowerBIClient,
        "entity_sum_request",
        lambda self, context, **kw: {
            "entity": kw["entity"],
            "view_url": context.view_url,
        },
    )
    import requests

    def post(self, url, **kwargs):
        item = next(
            item
            for item in captures
            if item["entity"] == kwargs["json"]["entity"]
            and item["context"]["view_url"] == kwargs["json"]["view_url"]
        )
        return SimpleNamespace(
            content=json.dumps(item["payload"]).encode(),
            url=url,
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(requests.Session, "post", post)
    monkeypatch.setattr(
        connector,
        "decode_dsr_v2",
        lambda *a, **kw: pytest.fail("collect decoded source rows"),
    )

    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, raw, **kw):
            self.artifacts.append((raw, kw))

    saved = await connector.collect(Writer(), source="all")
    assert len(saved) == len(captures)
    assert all(meta["metadata"]["endpoint"] == "query" for _, meta in saved)
