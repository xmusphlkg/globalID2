"""Country recovery never hides artifact failures or bypasses source quality."""
import asyncio
import csv
import hashlib
import io
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
import requests
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core import database
from src.data.crawlers.br import BrazilSINANCrawler, SINANFile
from src.data.crawlers.ch import CHSeriesSource, SwitzerlandIDDCrawler
from src.data.crawlers.kr import KoreaKDCAOpenAPICrawler, MIN_ZERO_BATCH_DISEASES
from src.data.processors.br import BRMonthlyUpdater
from src.data.processors.ch import CHMonthlyUpdater
from src.data.processors.kr import KRMonthlyUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.domain.collection import SourceArtifact
from src.services.ingestion import artifacts as artifact_module
from src.services.ingestion.artifacts import ArtifactWriter, read_artifact
from src.services.ingestion.capture import capture_sync
from src.services.ingestion.countries import br, ch, kr
from tests.contract.compare import assert_observations_match

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion"
MODULES = {"BR": br, "KR": kr, "CH": ch}
CRAWLERS = {"BR": BrazilSINANCrawler, "KR": KoreaKDCAOpenAPICrawler, "CH": SwitzerlandIDDCrawler}
UPDATERS = {"BR": BRMonthlyUpdater, "KR": KRMonthlyUpdater, "CH": CHMonthlyUpdater}
SOURCE_IDS = {"BR": "SRC_BR_SINAN", "KR": "SRC_KR_KDCA", "CH": "SRC_CH_FOPH_IDD"}
MONTHS = [(2025, 1), (2025, 2)]
SINAN = SINANFile("AIDA", "AIDS in adults", 2025, "AIDABR25.dbc", "ftp://fixture/AIDABR25.dbc", "final", 0, None)
SERIES = CHSeriesSource("hiv", "Human Immunodeficiency Virus (HIV)", "hiv/cases/value/month", "month", {}, "CHFL", "2025-03-01")


def fail(*args, **kwargs):
    raise RuntimeError("source unavailable")


def options(country, history):
    result = dict(source="all", months=MONTHS, history_csv=history)
    if country == "CH":
        result["country_code"] = country
    return result


def history_bytes(country, *, months=(1, 2), count=1, cases=4):
    rows = []
    for month in months:
        for index in range(count):
            label = {"BR": "AIDS in adults", "KR": "매독", "CH": SERIES.display_name}[country]
            rows.append(dict(Date=f"2025-{month:02d}-01", Disease=label if index == 0 else f"disease-{index}", DiseaseCode={"BR": "AIDA", "KR": "", "CH": "hiv"}[country], Cases=str(cases), LocalCases=str(cases), ImportedCases="0", PeriodType="month", PeriodValue=f"2025{month:02d}", Geography="CHFL", DataComplete="TRUE", DatasetStatus="final"))
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def saved(payload, kind, *, metadata=None, url="https://fixture/source"):
    path = Path("data/raw/test") / uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return SimpleNamespace(relative_path=path.as_posix(), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), kind=kind, source_url=url, metadata_=metadata or {})


def bad_live(country):
    if country == "BR":
        return saved(b"invalid DBC", "sinan_dbc", metadata={"file": asdict(SINAN)})
    if country == "CH":
        return saved(b"invalid JSON", "idd_series", metadata={"series": asdict(SERIES)})
    return saved(b"invalid JSON", "source_json", url="https://fixture/selectBassDissStatsList", metadata={"portal_year": 2025})


@pytest_asyncio.fixture
async def artifact_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifacts.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(SourceArtifact.__table__.create)
    monkeypatch.setattr(database, "_session_maker", async_sessionmaker(engine, expire_on_commit=False))
    yield
    await engine.dispose()


def stub_collection(country, monkeypatch):
    if country == "BR":
        monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", lambda self: [SINAN])
        monkeypatch.setattr(BrazilSINANCrawler, "_download_file", lambda *a: FIXTURES / "BR/AIDABR25.dbc")
    elif country == "CH":
        monkeypatch.setattr(SwitzerlandIDDCrawler, "fetch_series_sources", lambda self: [SERIES])
        monkeypatch.setattr(SwitzerlandIDDCrawler, "post", lambda *a, **kw: SimpleNamespace(content=(FIXTURES / "CH/series.json").read_bytes()))
    else:
        original = KoreaKDCAOpenAPICrawler.__init__
        def init(self, *args, **kwargs):
            original(self, *args, **{**kwargs, "service_key": ""})
        monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "__init__", init)
        monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "_manual_source_paths", lambda *a, **kw: [])
        def portal(self, year):
            payload = capture_sync((FIXTURES / "KR/portal_2025.json").read_bytes(), self.portal_stats_url, "application/json")
            return json.loads(payload)["value"]
        monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "_fetch_bass_stats_list", portal)


@pytest.mark.parametrize("country", MODULES)
@pytest.mark.parametrize("failure", ["disk", "registration"])
async def test_collect_persistence_failure_is_fatal_with_history(artifact_db, monkeypatch, country, failure):
    stub_collection(country, monkeypatch)
    history = Path("history.csv")
    history.write_bytes(history_bytes(country))
    writer = ArtifactWriter(str(uuid4()), 1, country.lower())
    if failure == "disk":
        original = artifact_module._save_atomic
        def save(path, payload):
            if writer.artifacts:
                raise OSError("artifact disk failure")
            original(path, payload)
        monkeypatch.setattr(artifact_module, "_save_atomic", save)
    else:
        original = artifact_module.get_db
        @asynccontextmanager
        async def get_db():
            if writer.artifacts:
                raise RuntimeError("artifact registration failure")
            async with original() as db:
                yield db
        monkeypatch.setattr(artifact_module, "get_db", get_db)
    with pytest.raises((RuntimeError, OSError)) as caught:
        await MODULES[country].collect(writer, **options(country, history))
    cause = caught.value.__cause__ or caught.value
    assert f"artifact {failure} failure" in str(cause)
    assert [a.kind for a in writer.artifacts] == ["history_csv"]
    assert len(list(writer.directory.glob("*"))) == 1


async def test_kr_portal_year_metadata_failure_is_fatal(artifact_db, monkeypatch):
    stub_collection("KR", monkeypatch)
    history = Path("history.csv")
    history.write_bytes(history_bytes("KR"))
    @asynccontextmanager
    async def failed_metadata():
        raise RuntimeError("portal metadata registration failed")
        yield
    # ArtifactWriter uses its imported get_db; only the year UPDATE fails.
    monkeypatch.setattr(database, "get_db", failed_metadata)
    writer = ArtifactWriter(str(uuid4()), 1, "kr")
    with pytest.raises(RuntimeError, match="portal metadata registration failed"):
        await kr.collect(writer, **options("KR", history))


@pytest.mark.parametrize("country", MODULES)
async def test_transport_failure_retains_history(artifact_db, monkeypatch, country):
    stub_collection(country, monkeypatch)
    method = {"BR": "_download_file", "KR": "_fetch_bass_stats_list", "CH": "post"}[country]
    monkeypatch.setattr(CRAWLERS[country], method, fail)
    history = Path("history.csv")
    history.write_bytes(history_bytes(country))
    writer = ArtifactWriter(str(uuid4()), 1, country.lower())
    result = await MODULES[country].collect(writer, **options(country, history))
    assert [a.kind for a in result] == ["history_csv"]


@pytest.mark.parametrize("country", MODULES)
@pytest.mark.parametrize("cache", ["valid", "gapped", "unreadable"])
def test_live_parse_failure_uses_only_original_cache_candidates(tmp_path, monkeypatch, country, cache):
    monkeypatch.chdir(tmp_path)
    # Avoid invoking native DBC tooling for the deliberately invalid bytes.
    monkeypatch.setattr(BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(fail))
    history = tmp_path / "history.csv"
    payload = b"\xff\xfe\xff" if cache == "unreadable" else history_bytes(country, months=(2,) if cache == "gapped" else (1, 2))
    history.write_bytes(payload)
    cached = saved(payload, "history_csv")
    updater = UPDATERS[country](output_csv=history)
    method = {"BR": "crawl_monthly_national", "KR": "crawl_monthly_national", "CH": "crawl_national"}[country]
    monkeypatch.setattr(CRAWLERS[country], method, fail)
    # Independent existing updater selects or rejects the same prior snapshot.
    kwargs = dict(source="all", months=MONTHS)
    accepted = cache == "valid" or (country == "CH" and cache == "gapped")
    if not accepted:
        with pytest.raises(RuntimeError, match="source unavailable"):
            updater.refresh_source(**kwargs)
        with pytest.raises((RuntimeError, ValueError)):
            MODULES[country].organize([cached, bad_live(country)], updater=updater, **options(country, history))
        return
    expected = updater.refresh_source(**kwargs)
    actual = MODULES[country].organize([cached, bad_live(country)], updater=updater, **options(country, history))
    assert actual.rows == expected.rows
    assert any("recovery" in entry for entry in actual.script_logs)
    store = SeriesObservationStore()
    before = store.build_observations(expected.rows, country, source_id=SOURCE_IDS[country]).observations
    after = store.build_observations(actual.rows, country, source_id=SOURCE_IDS[country]).observations
    assert after
    assert_observations_match(after, before)


@pytest.mark.parametrize("country", MODULES)
@pytest.mark.parametrize("tamper", ["history", "live"])
def test_corrupt_artifact_never_becomes_cache_recovery(tmp_path, monkeypatch, country, tamper):
    monkeypatch.chdir(tmp_path)
    history = saved(history_bytes(country), "history_csv")
    live = bad_live(country)
    Path((history if tamper == "history" else live).relative_path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Artifact integrity check failed"):
        MODULES[country].organize([history, live], updater=UPDATERS[country](output_csv=Path("history.csv")), **options(country, "history.csv"))


@pytest.mark.parametrize("valid_cache", [True, False])
def test_kr_rejects_broad_zero_live_and_checks_cache_quality(tmp_path, monkeypatch, valid_cache):
    monkeypatch.chdir(tmp_path)
    cached = saved(history_bytes("KR", count=MIN_ZERO_BATCH_DISEASES, cases=4 if valid_cache else 0), "history_csv")
    payload = json.dumps({"value": [{"SUBTITLE": f"disease-{i}", "COLUMN2": "0", "COLUMN3": "0"} for i in range(MIN_ZERO_BATCH_DISEASES)]}).encode()
    live = saved(payload, "source_json", url="https://fixture/selectBassDissStatsList", metadata={"portal_year": 2025})
    updater = KRMonthlyUpdater(output_csv=Path("history.csv"))
    if valid_cache:
        actual = kr.organize([cached, live], updater=updater, **options("KR", "history.csv"))
        assert len(actual.rows) == 2 * MIN_ZERO_BATCH_DISEASES
        assert {row["Cases"] for row in actual.rows} == {"4"}
        assert any("every count is zero" in entry for entry in actual.script_logs)
    else:
        with pytest.raises(ValueError, match="every count is zero"):
            kr.organize([cached, live], updater=updater, **options("KR", "history.csv"))


@pytest.mark.parametrize("persistence_fails", [False, True])
async def test_kr_ssl_fallback_captures_exact_response_for_offline_organize(artifact_db, monkeypatch, persistence_fails):
    crawler = KoreaKDCAOpenAPICrawler(service_key="", save_raw=False)
    crawler.delay = 0
    payload = (FIXTURES / "KR/portal_2025.json").read_bytes()
    response = requests.Response()
    response.status_code = 200
    response._content = payload
    response.url = crawler.portal_stats_url.replace("https://", "http://")
    response.headers["content-type"] = "application/json"
    def ssl_error(*a, **kw):
        raise requests.exceptions.SSLError("TLS chain")
    monkeypatch.setattr(crawler, "post", ssl_error)
    monkeypatch.setattr(crawler.session, "post", lambda *a, **kw: response)
    monkeypatch.setattr(crawler, "_manual_source_paths", lambda **kw: [])
    monkeypatch.setattr(kr, "KoreaKDCAOpenAPICrawler", lambda **kw: crawler)
    writer = ArtifactWriter(str(uuid4()), 1, "kr")
    try:
        if persistence_fails:
            async def failed_write(*a, **kw):
                raise OSError("SSL fallback artifact failed")
            monkeypatch.setattr(writer, "write", failed_write)
            monkeypatch.setattr(response, "json", lambda: pytest.fail("parsed before persistence"))
            with pytest.raises(RuntimeError, match="Source artifact persistence failed"):
                await kr.collect(writer, **options("KR", "history.csv"))
            return
        collected = await kr.collect(writer, **options("KR", "history.csv"))
        [artifact] = writer.artifacts
        assert read_artifact(artifact) == payload
        assert artifact.source_url == response.url
        assert artifact.metadata_["portal_year"] == 2025
        monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "post", fail)
        actual = kr.organize(collected, updater=KRMonthlyUpdater(output_csv=Path("history.csv")), **options("KR", "history.csv"))
        assert sorted(row["Cases"] for row in actual.rows) == ["0", "0", "2", "4"]
    finally:
        crawler.session.close()


@pytest.mark.parametrize("country", ["BR", "CH"])
async def test_discovery_capture_failure_is_not_swallowed_by_cache(artifact_db, monkeypatch, country):
    stub_collection(country, monkeypatch)
    def discovery(*a, **kw):
        # Legacy discovery helpers may swallow errors while trying another URL.
        try:
            capture_sync(b"index", "https://fixture/index.json", "application/json")
        except Exception:
            pass
        raise RuntimeError("discovery exhausted")
    method = "fetch_file_index" if country == "BR" else "fetch_series_sources"
    monkeypatch.setattr(CRAWLERS[country], method, discovery)
    history = Path("history.csv")
    history.write_bytes(history_bytes(country))
    writer = ArtifactWriter(str(uuid4()), 1, country.lower())
    original = writer.write
    async def write(payload, **kwargs):
        if kwargs["kind"] != "history_csv":
            raise OSError("index artifact failure")
        return await original(payload, **kwargs)
    monkeypatch.setattr(writer, "write", write)
    with pytest.raises(RuntimeError, match="Source artifact persistence failed"):
        await MODULES[country].collect(writer, **options(country, history))


@pytest.mark.parametrize("channel", ["portal", "openapi"])
async def test_kr_partial_fetch_cannot_be_selected_as_live(artifact_db, monkeypatch, channel):
    stub_collection("KR", monkeypatch)
    history = Path("history.csv")
    history.write_bytes(history_bytes("KR"))
    def partial(self, *args, **kwargs):
        url = self.portal_stats_url if channel == "portal" else "https://fixture/PeriodRegion"
        capture_sync((FIXTURES / "KR/portal_2025.json").read_bytes(), url, "application/json")
        raise requests.ConnectionError("next page/year unavailable")
    method = "_fetch_bass_stats_list" if channel == "portal" else "fetch_period_region_monthly"
    monkeypatch.setattr(KoreaKDCAOpenAPICrawler, method, partial)
    if channel == "openapi":
        original = KoreaKDCAOpenAPICrawler.__init__
        def init(self, *a, **kw):
            original(self, *a, **kw)
            self.service_key = "fixture"
        monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "__init__", init)
    writer = ArtifactWriter(str(uuid4()), 1, "kr")
    selected = await kr.collect(writer, **options("KR", history))
    assert len(selected) == len(writer.artifacts) == 2
    assert selected[1].metadata_["live_fetch_failed"] is True
    # Reload persisted metadata, as an offline replay does.
    async with database.get_db() as db:
        live = await db.get(SourceArtifact, selected[1].id)
    assert live.metadata_["live_fetch_failed"] is True
    updater = KRMonthlyUpdater(output_csv=history)
    actual = kr.organize([selected[0], live], updater=updater, **options("KR", history))
    assert actual.rows == updater._load_rows(history)


def test_br_contiguous_cache_prefix_remains_valid_when_later_month_unavailable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(BrazilSINANCrawler, "crawl_monthly_national", fail)
    history = tmp_path / "history.csv"
    payload = history_bytes("BR", months=(1,))
    history.write_bytes(payload)
    updater = BRMonthlyUpdater(output_csv=history)
    expected = updater.refresh_source(months=MONTHS)
    actual = br.organize([saved(payload, "history_csv"), bad_live("BR")], updater=updater, **options("BR", history))
    assert actual.rows == expected.rows
    assert [r["Month"] for r in actual.rows] == ["1"]


@pytest.mark.parametrize("country", MODULES)
def test_invalid_live_without_cache_fails(tmp_path, monkeypatch, country):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(fail))
    with pytest.raises((RuntimeError, ValueError)):
        MODULES[country].organize([bad_live(country)], updater=UPDATERS[country](output_csv=Path("history.csv")), **options(country, "history.csv"))


def test_ch_normalization_failure_can_recover_valid_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    updater = CHMonthlyUpdater(output_csv=Path("history.csv"))
    original = updater._load_rows
    def load(path):
        if path.name == "normalized.csv":
            raise ValueError("live normalization failed")
        return original(path)
    monkeypatch.setattr(updater, "_load_rows", load)
    history = saved(history_bytes("CH"), "history_csv")
    live = saved((FIXTURES / "CH/series.json").read_bytes(), "idd_series", metadata={"series": asdict(SERIES)})
    result = ch.organize([history, live], updater=updater, **options("CH", "history.csv"))
    assert {r["Cases"] for r in result.rows} == {"4"}
    assert any("recovery" in entry for entry in result.script_logs)


async def test_kr_ssl_fallback_failed_status_is_not_parsed(artifact_db, monkeypatch):
    crawler = KoreaKDCAOpenAPICrawler(service_key="", save_raw=False)
    crawler.delay = 0
    def ssl_error(*a, **kw):
        raise requests.exceptions.SSLError("TLS chain")
    response = requests.Response()
    response.status_code = 503
    response._content = (FIXTURES / "KR/portal_2025.json").read_bytes()
    response.url = crawler.portal_stats_url
    monkeypatch.setattr(crawler, "post", ssl_error)
    monkeypatch.setattr(crawler.session, "post", lambda *a, **kw: response)
    try:
        with pytest.raises(RuntimeError, match="stats request failed"):
            await asyncio.to_thread(crawler._fetch_bass_stats_list, 2025)
    finally:
        crawler.session.close()
