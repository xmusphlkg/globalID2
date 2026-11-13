from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from src.services.data_release.literature_binding import bind_literature_candidate
from src.services.data_release.papers_content import catalogue_from_entries
from src.services.data_release.research_monitor import probe_public_research, reserve_sampling_cursor
from src.services.data_release.realtime_monitor import MonitorThresholds, evaluate_channel
from tests.unit.test_realtime_data_monitor import NOW, db_state, manifest_fixture

SITE = "https://site.example"
PAPERS = "https://papers.example"
POINTER = SITE + "/site-data/research-publication/current.json"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_sampling_cursor_is_atomic_and_bound_to_target(tmp_path):
    state = tmp_path / "archive" / "research-sampling.json"
    target = POINTER + "\n" + PAPERS
    assert reserve_sampling_cursor(state, target=target) == 0
    assert reserve_sampling_cursor(state, target=target) == 1
    assert json.loads(state.read_text()) == {
        "schema_version": 1,
        "target_sha256": sha(target.encode()),
        "next_cursor": 2,
    }
    # A changed pointer/origin starts a fresh stable rotation.
    assert reserve_sampling_cursor(state, target=target + "/next") == 0
    assert json.loads(state.read_text())["next_cursor"] == 1


def test_sampling_cursor_advances_before_probe_result(tmp_path):
    state = tmp_path / "research-sampling.json"
    target = POINTER + "\n" + PAPERS
    cursor = reserve_sampling_cursor(state, target=target)
    # The caller reserves the position before starting I/O, so a failed
    # request still leaves the next position available to the next run.
    assert cursor == 0
    assert reserve_sampling_cursor(state, target=target) == 1


@pytest.fixture
def publication(tmp_path):
    payload, pointer = manifest_fixture()
    manifest = json.loads(pointer)
    manifest["release_id"] = "literature-current"
    candidate = catalogue_from_entries(root=tmp_path, origin=PAPERS, release_id=manifest["release_id"],
        entries=[{"logical_key": "research/", "path": "research/index.html",
                  "key": "releases/sha256/objects/" + "a" * 64 + "/" + "b" * 16,
                  "content_type": "text/html", "sha256": "a" * 64, "bytes": 20}])
    bound = bind_literature_candidate(manifest, candidate, expected_papers_origin=PAPERS)
    remote = {POINTER: json.dumps(bound).encode(), PAPERS + "/" + candidate.manifest_key: candidate.manifest,
              SITE + "/site-data/research-publication/objects/" + sha(payload) + ".json": payload}
    remote.update({PAPERS + "/" + key: body for key, body in candidate.shards})
    return remote


async def probe(remote, handler=None, **options):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler or (
            lambda request: httpx.Response(200, content=remote[str(request.url)])))) as client:
        return await probe_public_research(client, POINTER, papers_origin=PAPERS, **options)


@pytest.mark.asyncio
async def test_actual_publisher_binding_and_catalogue_are_verified_with_explicit_scope(publication):
    result = await probe(publication)
    assert result["available"] and result["sample_verified"]
    assert result["manifest_sha256"] == sha(publication[POINTER])
    assert result["verification_scope"] == "research_pointer_catalogue_one_shard_one_json_sample"
    assert result["article_html_verified"] is False
    assert result["verified_shards"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["catalogues/", "catalogue-shards/", "/objects/"])
async def test_corrupt_decoded_bytes_fail_even_on_http_200(publication, target):
    key = next(key for key in publication if target in key)
    publication[key] += b" "
    result = await probe(publication)
    assert not result["available"] and result["error_code"] == "object_checksum_mismatch"


@pytest.mark.asyncio
async def test_surveillance_envelope_without_papers_binding_is_not_research(publication):
    pointer = json.loads(publication[POINTER])
    del pointer["papers"]
    publication[POINTER] = json.dumps(pointer).encode()
    result = await probe(publication)
    assert not result["available"] and result["error_code"] == "invalid_manifest"


@pytest.mark.asyncio
async def test_binding_cannot_redirect_fetch_to_unconfigured_host(publication):
    pointer = json.loads(publication[POINTER])
    pointer["papers"]["papers_manifest_url"] = "https://private.example/secrets"
    publication[POINTER] = json.dumps(pointer).encode()
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=publication[str(request.url)])
    assert not (await probe(publication, handler))["available"]
    assert calls == [POINTER]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [("timeout", "http_timeout"), ("503", "http_status"),
    ("redirect", "http_status"), ("transport", "http_unreachable")])
async def test_network_failure_is_not_health(publication, mode, code):
    async def handler(request):
        if mode == "timeout":
            await asyncio.sleep(1)
        if mode == "transport":
            raise httpx.ConnectError("secret=private", request=request)
        return httpx.Response(302 if mode == "redirect" else 503, headers={"Location": "https://private.example"})
    result = await probe(publication, handler, timeout_seconds=0.01)
    assert not result["available"] and result["error_code"] == code
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
async def test_pointer_switch_during_probe_fails(publication):
    reads = 0
    def handler(request):
        nonlocal reads
        body = publication[str(request.url)]
        if str(request.url) == POINTER:
            reads += 1
            if reads > 1:
                body += b" "
        return httpx.Response(200, content=body)
    assert (await probe(publication, handler))["error_code"] == "research_pointer_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_cli_retains_legacy_default_pair_and_adds_only_enabled_literature(monkeypatch, publication, enabled):
    from scripts import monitor_realtime_data as cli
    config = SimpleNamespace(main_site_origin=SITE, literature_json_enabled=enabled, r2_papers_public_base_url=PAPERS)
    monkeypatch.setattr(cli, "get_config", lambda: SimpleNamespace(data_release=config))
    monkeypatch.setattr(cli, "get_publication_controls", lambda: {})
    payload, body = manifest_fixture()
    calls = []
    def handler(request):
        url = str(request.url)
        calls.append(url)
        return httpx.Response(200, content=publication[url] if url in publication else
            body if url.endswith("current.json") else payload)
    async def database(**kwargs):
        return {channel: {**db_state(), "last_completed": None} for channel in kwargs["channels"]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cli.check_once(cli.parser().parse_args([]), MonitorThresholds(), client=client, database_reader=database)
    assert set(result["channels"]) == ({"surveillance", "situation", "literature"} if enabled else {"surveillance", "situation"})
    assert result["status"] == "ok"
    assert (POINTER in calls) is enabled


def test_inflight_literature_pointer_is_not_mistaken_for_unrelated_commit():
    state = db_state()
    state["active"] = [{"task_uuid": "new", "status": "running", "age_seconds": 5}]
    public = {"available": True, "release_id": "literature-new", "manifest_sha256": "b" * 64,
              "generated_at": NOW.isoformat()}
    result = evaluate_channel(state, public, now=NOW, thresholds=MonitorThresholds())
    assert result["status"] == "ok"
