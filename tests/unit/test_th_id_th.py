"""Archive provenance, sparse week semantics, and collision-free metric identity."""
import csv
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.data.crawlers.th_id_th import COLUMNS, EXCLUDED_STEMS, IDTHContractError, git_blob_sha, parse_weekly_csv, selected_files
from src.data.processors.th_id_th import ThailandIDTHHistoryUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.ingestion.countries.th_id_th import cached_blob, organize

COMMIT = "a" * 40
PATH = "Data/WeeklyCasesData/2568/Measles.csv"


def payload(rows=None):
    stream = io.StringIO(); writer = csv.writer(stream); writer.writerow(COLUMNS)
    writer.writerows(rows or [["1", "1", "%all%", "all", "3"], ["53", "53", "%all%", "all", "5"], ["%all%", "all", "%all%", "all", "8"], ["1", "1", "1", "province", "999"]])
    return stream.getvalue().encode()


def parse(data=None, path=PATH):
    data = data or payload()
    return parse_weekly_csv(data, path=path, commit=COMMIT, blob_sha=git_blob_sha(data), retrieved_at="2026-11-15T00:00:00Z")


def test_sparse_non_iso_week53_and_national_values_preserved():
    rows = parse()
    assert [(r["SourceWeek"], r["Cases"]) for r in rows] == [("1", "3"), ("53", "5")]
    assert {r["Date"] for r in rows} == {"2025-01-01"}
    assert all(r["PeriodStart"] == r["PeriodEnd"] == "" for r in rows)
    assert all(r["CalendarStatus"] == "unverified_historical_calendar" and r["TimeBasis"] == "source_year_week_unverified_calendar" for r in rows)
    assert rows[1]["SourceRepositoryCommit"] == COMMIT and rows[1]["SourceRepositoryPath"] == PATH
    assert all(json.loads(r["Dimensions"])["source_week"] in {1, 53} for r in rows)


def test_cases_deaths_have_distinct_series_definition_identity():
    cases, deaths = parse()[0], parse(path=PATH.replace("WeeklyCasesData", "WeeklyDeathsData"))[0]
    assert cases["SourceDiseaseCode"] == deaths["SourceDiseaseCode"]
    assert cases["DefinitionVersion"] != deaths["DefinitionVersion"]
    assert cases["Measure"] == "case_notifications" and deaths["Measure"] == "death_notifications"


@pytest.mark.parametrize("rows", [
    [["1", "1", "%all%", "all", "3"], ["%all%", "all", "%all%", "all", "9"]],
    [["1", "1", "%all%", "all", "3"], ["1", "1", "%all%", "all", "3"], ["%all%", "all", "%all%", "all", "6"]],
    [["1", "1", "%all%", "all", ""], ["%all%", "all", "%all%", "all", "0"]],
])
def test_missing_duplicate_or_non_reconciling_national_cells_fail(rows):
    with pytest.raises(IDTHContractError): parse(payload(rows))


def test_blob_integrity_and_tree_selection_fail_closed():
    with pytest.raises(IDTHContractError, match="integrity"):
        parse_weekly_csv(payload(), path=PATH, commit=COMMIT, blob_sha="b" * 40)
    with pytest.raises(IDTHContractError, match="truncated"):
        selected_files({"truncated": True})
    tree = {"tree": [{"type": "blob", "path": PATH}, {"type": "blob", "path": PATH.replace("/Measles", "/age/Measles")}]}
    assert [item["path"] for item in selected_files(tree)] == [PATH]
    for stem in EXCLUDED_STEMS:
        with pytest.raises(IDTHContractError, match="Excluded"):
            parse(path=PATH.replace("Measles", stem))


def test_local_cache_only_reuses_verified_git_blobs(tmp_path):
    data = payload(); sha = git_blob_sha(data); item = {"path": PATH, "sha": sha, "size": len(data)}
    archive, cache = tmp_path / "archive", tmp_path / "cache"
    p = archive / PATH; p.parent.mkdir(parents=True); p.write_bytes(data)
    assert cached_blob(item, archive_root=archive, cache_root=cache) == data
    p.write_bytes(b"tampered")
    assert cached_blob(item, archive_root=archive, cache_root=cache) is None


async def test_real_store_build_preserves_same_year_weeks_and_raw_provenance(monkeypatch):
    rows = parse(); category = SimpleNamespace(id=1, category_key="archive_measles_cases", source_id="SRC_TH_ID_TH_ARCHIVE", source_code="Measles", canonical_source_label="Measles", definition_version=rows[0]["DefinitionVersion"], source_definition=None, source_definition_uri=None)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: category)), add=lambda _: None, flush=AsyncMock())
    store = SeriesObservationStore()
    monkeypatch.setattr(store, "_resolve_series", lambda **kwargs: [])
    monkeypatch.setattr(store, "_load_holding_series", AsyncMock(return_value=None))
    facts, handled = await store._build_holding_observations(db, rows, "TH", source_id="SRC_TH_ID_TH_ARCHIVE", value_field="Cases", geography_key="country:TH:national")
    assert handled == len(facts) == 2
    assert facts[0]["time"] == facts[1]["time"]
    assert len({(f["time"], f["series_code"], f["geography_key"], f["dimension_key"]) for f in facts}) == 2
    assert facts[1]["dimensions"]["source_week"] == 53
    assert facts[1]["raw_data"]["SourceRepositoryCommit"] == COMMIT
    assert facts[1]["raw_data"]["CalendarStatus"] == "unverified_historical_calendar"


def test_offline_replay_requires_complete_pinned_archive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    def artifact(endpoint, data, **meta):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        path = Path("data/raw/test") / str(len(artifacts));path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(body)
        a = SimpleNamespace(relative_path=str(path), sha256=hashlib.sha256(body).hexdigest(), byte_size=len(body), metadata_={"endpoint": endpoint, **meta});artifacts.append(a);return a
    artifacts=[]; data=payload();sha=git_blob_sha(data);tree_sha="b"*40
    artifact("repository_commit", {"sha": COMMIT, "commit": {"tree": {"sha": tree_sha}}})
    artifact("repository_tree", {"sha": tree_sha, "tree": [{"type":"blob", "path":PATH, "sha":sha}]})
    artifact("collection_plan", {"commit":COMMIT,"tree_sha":tree_sha,"start_year":2020,"end_year":2025,"reused_blobs":1,"downloaded_blobs":0,"excluded":[]})
    with pytest.raises(IDTHContractError, match="incomplete"):
        organize(artifacts, updater=ThailandIDTHHistoryUpdater())
    artifact("weekly_national_csv", data, path=PATH, commit=COMMIT, git_blob_sha=sha, retrieved_at="2026-11-15")
    fetched=organize(artifacts,updater=ThailandIDTHHistoryUpdater())
    assert len(fetched.rows)==2
    assert ThailandIDTHHistoryUpdater.mapping_notifications_enabled is False


async def test_collection_checks_head_tree_and_reuses_unchanged_blob(tmp_path, monkeypatch):
    from src.services.ingestion.countries import th_id_th as connector
    data = payload(); sha = git_blob_sha(data); tree_sha = "b" * 40
    archive = tmp_path / "archive"; p = archive / PATH; p.parent.mkdir(parents=True);p.write_bytes(data)
    urls=[]
    class Crawler:
        session=SimpleNamespace(close=lambda:None)
        def get(self,url):
            urls.append(url)
            if url.endswith('/commits/main'): body={"sha":COMMIT,"commit":{"tree":{"sha":tree_sha}}}
            elif '/git/trees/' in url: body={"sha":tree_sha,"tree":[{"type":"blob","path":PATH,"sha":sha,"size":len(data)}]}
            else: pytest.fail("unchanged cached blob was downloaded")
            return SimpleNamespace(content=json.dumps(body).encode())
    monkeypatch.setattr(connector,"ThailandIDTHCrawler",Crawler)
    monkeypatch.setattr(connector,"CACHE_ROOT",tmp_path/'cache')
    class Writer:
        artifacts=[]
        async def write(self,body,**kwargs):
            self.artifacts.append(SimpleNamespace(body=body,metadata_=kwargs.pop("metadata"),**kwargs))
    writer=Writer();await connector.collect(writer,archive_root=archive)
    assert len(urls)==2
    plan=json.loads(writer.artifacts[-1].body)
    assert plan['reused_blobs']==1 and plan['downloaded_blobs']==0
    monkeypatch.setattr(connector, 'read_artifact', lambda artifact: artifact.body)
    fetched=connector.organize(writer.artifacts, updater=ThailandIDTHHistoryUpdater())
    assert len(fetched.rows)==2 and fetched.source_audit['national_week_observations']==2
    bundle=next(a for a in writer.artifacts if a.metadata_['endpoint']=='weekly_national_bundle')
    bundle.metadata_['members'][0]['sha256']='0'*64
    with pytest.raises(IDTHContractError, match='SHA256'):
        connector.organize(writer.artifacts, updater=ThailandIDTHHistoryUpdater())
