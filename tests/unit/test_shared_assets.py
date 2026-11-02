from pathlib import Path

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
    assert (old / "_astro/a.css").read_bytes() == b"old"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "content", "cors"])
async def test_verification_checks_public_bytes_and_cors(
    tmp_path, monkeypatch, failure
):
    put(tmp_path, "_astro/new.css", b"style")
    real_client = httpx.AsyncClient
    response = httpx.Response(
        200,
        content=b"wrong" if failure == "content" else b"style",
        headers={
            "access-control-allow-origin": "wrong"
            if failure == "cors"
            else "https://papers.test"
        },
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
    if failure:
        with pytest.raises(RuntimeError, match="mismatch"):
            await call
    else:
        await call
