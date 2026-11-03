from pathlib import Path
import json
from types import SimpleNamespace

import httpx
import pytest

from src.services.data_release import shared_assets


def test_shared_asset_transport_retries_but_bad_content_does_not():
    from src.services.data_release.resilience import classify_release_failure

    transient = shared_assets.SharedAssetVerificationError("connection reset by peer")
    assert classify_release_failure(transient).retryable
    invalid = shared_assets.SharedAssetVerificationError("Shared asset checksum mismatch")
    assert not classify_release_failure(invalid).retryable


def put(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_candidate_preserves_prior_pages_and_all_prior_assets(tmp_path):
    old, new, candidate = (tmp_path / name for name in ("old", "new", "candidate"))
    put(old, "index.html", b"old homepage")
    put(old, "release.json", b"old release")
    put(old, "_astro/old.hash.css", b"old style")
    put(new, "index.html", b"new homepage")
    put(new, "_astro/new.hash.css", b"new style")
    added = shared_assets.new_shared_assets(old, new)
    assert added == [Path("_astro/new.hash.css")]
    shared_assets.prepare_shared_assets(old, new, candidate, added)
    assert (candidate / "index.html").read_bytes() == b"old homepage"
    assert (candidate / "release.json").read_bytes() == b"old release"
    assert (candidate / "_astro/old.hash.css").read_bytes() == b"old style"
    assert (candidate / added[0]).read_bytes() == b"new style"
    assert not (old / added[0]).exists()
    assert shared_assets.new_shared_assets(candidate, new) == []


def test_same_url_content_change_and_budget_fail_closed(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    put(old, "_astro/a.css", b"old")
    put(new, "_astro/a.css", b"different")
    with pytest.raises(RuntimeError, match="same URL"):
        shared_assets.new_shared_assets(old, new)
    (new / "_astro/a.css").unlink()
    put(new, "_astro/b.css", b"new")
    with pytest.raises(RuntimeError, match="budget"):
        shared_assets.prepare_shared_assets(
            old, new, tmp_path / "candidate", [Path("_astro/b.css")], budget=2
        )
    assert not (tmp_path / "candidate").exists()
    assert (old / "_astro/a.css").read_bytes() == b"old"


def test_snapshot_requires_exact_live_identity_and_content(tmp_path):
    from src.services.data_release.pipeline import _built_site_digest

    source, snapshot = tmp_path / "source", tmp_path / "snapshot"
    put(source, "index.html", b"deployed")
    put(source, "_astro/a.js", b"asset")
    identity = {"release_id": "r1", "build_digest": _built_site_digest(source)}
    put(source, "release.json", json.dumps(identity).encode())
    shared_assets.retain_verified_snapshot(source, snapshot, identity)
    assert shared_assets.verified_snapshot(snapshot, identity)
    assert not shared_assets.verified_snapshot(snapshot, {**identity, "release_id": "r2"})
    put(source, "index.html", b"changed")
    assert shared_assets.verified_snapshot(snapshot, identity)
    put(snapshot, "index.html", b"corrupted")
    assert not shared_assets.verified_snapshot(snapshot, identity)


def test_missing_previous_snapshot_fails_closed(tmp_path):
    with pytest.raises(RuntimeError, match="verified previous"):
        shared_assets.new_shared_assets(None, tmp_path)


@pytest.mark.asyncio
async def test_local_papers_assets_require_valid_checksums_before_skipping_compatibility(tmp_path):
    import hashlib
    papers = tmp_path / 'exports/papers-site'
    put(papers, '_astro/site.css', b'style')
    put(papers, 'papers-manifest.json', json.dumps({
        'local_build_assets': True,
        'files': [{'path': '_astro/site.css', 'sha256': hashlib.sha256(b'style').hexdigest()}],
    }).encode())
    runtime = SimpleNamespace(root_dir=tmp_path)
    kwargs = dict(previous=None, project_name='site', deployment_branch='main', release_identity={}, papers_origin='https://papers.test')
    await shared_assets.publish_shared_assets(None, None, runtime, **kwargs)
    put(papers, '_astro/site.css', b'corrupt')
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        await shared_assets.publish_shared_assets(None, None, runtime, **kwargs)


@pytest.mark.asyncio
async def test_compatibility_deploy_rejects_unverified_previous(tmp_path, monkeypatch):
    old = tmp_path / "old"
    put(old, "index.html", b"development build")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        shared_assets.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"release_id": "live", "build_digest": "digest"})
        )),
    )
    service = SimpleNamespace(_config=lambda: SimpleNamespace(main_site_origin="https://main.test"))
    runtime = SimpleNamespace(root_dir=tmp_path, astro_dir=tmp_path / "astro")
    with pytest.raises(RuntimeError, match="No verified previous Pages snapshot"):
        await shared_assets.publish_shared_assets(
            service, SimpleNamespace(), runtime, previous=old, project_name="site",
            deployment_branch="main", release_identity={}, papers_origin="https://papers.test",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "allow_origin", "ok"),
    [
        (b"style", "https://papers.test", True),
        (b"style", "*", True),
        (b"wrong", "https://papers.test", False),
        (b"style", "https://reports.test", False),
    ],
)
async def test_verification_checks_public_bytes_and_cors(
    tmp_path, monkeypatch, content, allow_origin, ok
):
    put(tmp_path, "_astro/new.css", b"style")
    real_client = httpx.AsyncClient
    response = httpx.Response(
        200,
        content=content,
        headers={"access-control-allow-origin": allow_origin},
    )
    monkeypatch.setattr(
        shared_assets.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: response)
        ),
    )

    async def no_sleep(_):
        pass

    monkeypatch.setattr(shared_assets.asyncio, "sleep", no_sleep)
    call = shared_assets.verify_shared_assets(
        tmp_path,
        [Path("_astro/new.css")],
        main_origin="https://main.test",
        papers_origin="https://papers.test",
    )
    if ok:
        await call
    else:
        with pytest.raises(RuntimeError, match="mismatch"):
            await call
