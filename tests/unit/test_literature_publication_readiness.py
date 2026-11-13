from types import SimpleNamespace

import pytest

from scripts import check_literature_publication_readiness as readiness


def config(enabled=False, origin="https://papers.example"):
    return SimpleNamespace(data_release=SimpleNamespace(
        literature_json_enabled=enabled,
        r2_papers_public_base_url=origin,
    ))


@pytest.mark.asyncio
async def test_existing_public_target_does_not_claim_automatic_publish_when_disabled(monkeypatch):
    monkeypatch.setattr(readiness, "get_config", lambda: config(False))

    async def probe(*args, **kwargs):
        return {"available": True, "sample_verified": True, "article_html_verified": False}

    monkeypatch.setattr(readiness, "probe_public_research", probe)
    result = await readiness.check(pointer_url="https://site.example/current.json")
    assert result["public_probe"]["available"] is True
    assert result["ready"] is False
    assert result["reasons"] == ["literature_json_disabled"]


@pytest.mark.asyncio
async def test_enabled_channel_requires_a_successful_public_probe(monkeypatch):
    monkeypatch.setattr(readiness, "get_config", lambda: config(True))

    async def probe(*args, **kwargs):
        return {"available": False, "error_code": "http_timeout"}

    monkeypatch.setattr(readiness, "probe_public_research", probe)
    result = await readiness.check(pointer_url="https://site.example/current.json")
    assert result["ready"] is False
    assert result["reasons"] == ["http_timeout"]


@pytest.mark.asyncio
async def test_invalid_public_origin_fails_before_network(monkeypatch):
    monkeypatch.setattr(readiness, "get_config", lambda: config(True, "http://private.example"))
    called = False

    async def probe(*args, **kwargs):
        nonlocal called
        called = True
        return {"available": True}

    monkeypatch.setattr(readiness, "probe_public_research", probe)
    result = await readiness.check(pointer_url="https://site.example/current.json")
    assert result["ready"] is False
    assert "papers_public_origin_unconfigured" in result["reasons"]
    assert not called
