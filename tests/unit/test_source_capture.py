"""Raw capture precedes decoding and propagates through blocking I/O threads."""
import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from src.core import database
from src.domain.collection import SourceArtifact
from src.literature.clients.base import LiteratureHttpClient
from src.services.ingestion.capture import capture_sources, capture_sync


@pytest_asyncio.fixture
async def db(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(SourceArtifact.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "_session_maker", maker)
    yield maker
    await engine.dispose()


async def test_invalid_json_is_archived_before_decode_raises(db):
    raw = b'{"source": invalid json\n'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=raw))
    client = LiteratureHttpClient(user_agent="test", retries=1)
    with capture_sources(str(uuid4()), 1, "literature"):
        async with httpx.AsyncClient(transport=transport) as http:
            with pytest.raises(ValueError):
                await client.get_json(http, "https://example.org/feed", params={})
    async with db() as session:
        artifact = (await session.execute(select(SourceArtifact))).scalar_one()
    assert Path(artifact.relative_path).read_bytes() == raw
    assert artifact.sha256 == hashlib.sha256(raw).hexdigest()
    assert artifact.byte_size == len(raw)
    assert artifact.source_url == "https://example.org/feed"


async def test_blocking_capture_waits_for_durable_bytes_and_metadata(db):
    raw = b"<html>Original bytes</html>"
    with capture_sources(str(uuid4()), 2, "knowledge"):
        output = await asyncio.to_thread(capture_sync, raw, "https://example.org/report", "text/html")
    assert output == raw
    async with db() as session:
        artifact = (await session.execute(select(SourceArtifact))).scalar_one()
    assert artifact.attempt == 2
    assert artifact.kind == "knowledge_payload"
    assert Path(artifact.relative_path).read_bytes() == raw


async def test_ontario_live_collection_archives_before_decoding_and_excludes_auth(db, monkeypatch):
    import base64
    import json
    import requests
    from src.services.ingestion.artifacts import ArtifactWriter
    from src.services.ingestion.countries import ca_on
    from src.data.crawlers.ca import DEFAULT_REPORT_ID
    fixture = Path(__file__).resolve().parents[1] / "fixtures/ingestion/CA-ON"
    metadata = (fixture / "metadata.json").read_bytes()
    query = (fixture / "query.json").read_bytes()
    routing = base64.urlsafe_b64encode(json.dumps({"clusterUrl": "https://wabi-canada-central-api.analysis.windows.net/", "exp": 4102444800}).encode()).decode().rstrip("=")
    token = "opaque." + routing
    wrapper = f'<script>accessToken = "{token}"; embedReportId = "{DEFAULT_REPORT_ID}";</script>'.encode()
    writer = ArtifactWriter(str(uuid4()), 1, "ca-on")
    parsed = []
    class Response(requests.Response):
        def json(self, **kwargs):
            assert any(item.source_url.split("?", 1)[0] == self.url.split("?", 1)[0] for item in writer.artifacts)
            parsed.append(self.url)
            return super().json(**kwargs)
    def request(session, url, **kwargs):
        response = Response()
        response.status_code, response.url = 200, url
        response._content_consumed = True
        response._content = metadata if "modelsAndExploration" in url else query if "/querydata" in url else wrapper
        response.encoding = "utf-8"
        response.headers["content-type"] = "application/json" if response._content != wrapper else "text/html"
        return response
    monkeypatch.setattr(requests.Session, "get", request)
    monkeypatch.setattr(requests.Session, "post", request)
    collected = await ca_on.collect(writer, source="pho_idto_monthly", output_csv=Path("unused.csv"))
    assert len(collected) == 2
    assert len(parsed) == 1  # metadata was inspected only to construct the source query
    assert any("modelsAndExploration" in item.source_url for item in collected)
    assert any("/querydata" in item.source_url for item in collected)
    assert all(token.encode() not in Path(item.relative_path).read_bytes() for item in collected)


async def test_nz_real_artifact_registration_accepts_sitemap_date(db, monkeypatch):
    from datetime import date
    from types import SimpleNamespace
    from src.data.crawlers.nz import NewZealandPHFCrawler
    from src.data.processors.nz import NZMonthlyUpdater
    from src.services.ingestion.artifacts import ArtifactWriter
    from src.services.ingestion.countries import nz
    raw = (Path(__file__).resolve().parents[1] / "fixtures/ingestion/NZ/report.zip").read_bytes()
    report = {"type": "individual", "year": 2026, "month": 3, "date": date(2026, 3, 1), "key": "2026-03", "url": "https://example.org/report", "download_url": None, "title": "March 2026 report"}
    monkeypatch.setattr(NewZealandPHFCrawler, "fetch_report_index", lambda self, **kwargs: [report])
    monkeypatch.setattr(NewZealandPHFCrawler, "resolve_download_url", lambda self, report: "https://example.org/report.zip")
    monkeypatch.setattr(NewZealandPHFCrawler, "get", lambda *args, **kwargs: SimpleNamespace(content=raw))
    writer = ArtifactWriter(str(uuid4()), 1, "nz")
    output = Path("unused-nz.csv")
    saved = await nz.collect(writer, source="phf_monthly", months=[(2026, 3)], output_csv=output)
    async with db() as session:
        registered = (await session.execute(select(SourceArtifact))).scalar_one()
    assert "date" not in registered.metadata_["report"]
    assert registered.sha256 == hashlib.sha256(raw).hexdigest()
    result = nz.organize(saved, updater=NZMonthlyUpdater(output_csv=output), source="phf_monthly", months=[(2026, 3)], output_csv=output)
    assert {row["Cases"] for row in result.rows} == {"0", "5"}


@pytest.mark.parametrize("code", ["au", "hk", "tw", "nz", "fi", "at", "se", "ca_on"])
async def test_monthly_artifact_failure_cannot_become_successful_cache_recovery(code, tmp_path, monkeypatch):
    from datetime import date, datetime, timezone
    from importlib import import_module
    from types import SimpleNamespace
    from src.data.crawlers.au import AustraliaNINDSSCrawler
    from src.data.crawlers.hk import HongKongCHPCrawler
    from src.data.crawlers.tw import TaiwanNIDSSCrawler, TWDiseaseSource
    from src.data.crawlers.nz import NewZealandPHFCrawler
    from src.data.crawlers.fi import FinlandTHLCrawler, parse_dimension_catalog
    from src.data.crawlers.at import AustriaAGESRadarCrawler, AGESIssue
    from src.data.crawlers.se import SwedenSmiNetCrawler
    fixtures = Path(__file__).resolve().parents[1] / "fixtures/ingestion"
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "prior.csv"
    output.write_bytes(b"previous successful snapshot")
    class Writer:
        task_uuid, attempt = str(uuid4()), 1
        def __init__(self): self.artifacts = []
        async def write(self, payload, *, kind, **kwargs):
            if kind != "prior_csv":
                raise OSError("artifact storage unavailable")
            artifact = SimpleNamespace(kind=kind)
            self.artifacts.append(artifact)
            return artifact
    options = {"source": "all", "months": [(2026, 3)], "output_csv": output}
    response = SimpleNamespace(content=b"source bytes", url="https://example.org/source", encoding="utf-8", text="source text", headers={})
    if code == "au":
        def config(self):
            self._config = {"apiUrl": response.url, "datasetId": "fixture", "reportId": "fixture", "modelId": 1}
            return True
        monkeypatch.setattr(AustraliaNINDSSCrawler, "_load_config", config)
        monkeypatch.setattr(AustraliaNINDSSCrawler, "get_all_diseases", lambda self: ["Hepatitis (NEC)"])
        monkeypatch.setattr(AustraliaNINDSSCrawler, "request_payload_bytes", lambda *args, **kwargs: response.content)
    elif code == "hk":
        monkeypatch.setattr(HongKongCHPCrawler, "get", lambda *args, **kwargs: response)
    elif code == "tw":
        monkeypatch.setattr(TaiwanNIDSSCrawler, "fetch_disease_index", lambda self: [TWDiseaseSource("042", "AIDS", response.url, "")])
        monkeypatch.setattr(TaiwanNIDSSCrawler, "download_csv_bytes", lambda *args: response.content)
    elif code == "nz":
        monkeypatch.setattr(NewZealandPHFCrawler, "fetch_report_index", lambda *args, **kwargs: [{"type": "individual", "year": 2026, "month": 3, "url": response.url}])
        monkeypatch.setattr(NewZealandPHFCrawler, "resolve_download_url", lambda *args: response.url)
        monkeypatch.setattr(NewZealandPHFCrawler, "get", lambda *args, **kwargs: response)
    elif code == "fi":
        raw = (fixtures / "FI/dimensions.jsonp").read_bytes()
        def dimensions(self):
            self._dimension_payload = raw
            return parse_dimension_catalog(raw.decode())
        monkeypatch.setattr(FinlandTHLCrawler, "discover_dimensions", dimensions)
        options.update(months=[(2026, 7)], as_of=date(2026, 8, 7))
    elif code == "at":
        issue = AGESIssue(response.url, response.url, date(2026, 3, 1), datetime(2026, 4, 1, tzinfo=timezone.utc))
        monkeypatch.setattr(AustriaAGESRadarCrawler, "discover_issues", lambda self: [issue])
        monkeypatch.setattr(AustriaAGESRadarCrawler, "get", lambda *args, **kwargs: response)
    elif code == "se":
        monkeypatch.setattr(SwedenSmiNetCrawler, "_get_response", lambda *args, **kwargs: response)
    else:
        options["input_file"] = fixtures / "CA-ON/ontario_2026.csv"
    connector = import_module("src.services.ingestion.countries." + code)
    with pytest.raises(OSError, match="artifact storage unavailable"):
        await connector.collect(Writer(), **options)
