from __future__ import annotations

from io import BytesIO

import pytest

from scripts.automation.verify_site_commit import SiteCommitVerificationError, verify_site_commit


class _Response(BytesIO):
    status = 200

    def getcode(self) -> int:
        return self.status


def test_verify_site_commit_accepts_matching_release_meta() -> None:
    commit = "a" * 40

    def opener(request, *, timeout):
        assert request.full_url.startswith("https://example.test/")
        assert timeout == 3
        return _Response(f'<meta name="gids-source-commit" content="{commit}">'.encode())

    assert verify_site_commit(
        "https://example.test/",
        commit,
        attempts=1,
        timeout_seconds=3,
        opener=opener,
    ) == {"status": "verified", "attempts": 1, "source_commit": commit}


def test_verify_site_commit_rejects_stale_release() -> None:
    def opener(request, *, timeout):
        return _Response(b'<meta name="gids-source-commit" content="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">')

    with pytest.raises(SiteCommitVerificationError, match="source_commit_mismatch"):
        verify_site_commit(
            "https://example.test/",
            "a" * 40,
            attempts=1,
            timeout_seconds=3,
            opener=opener,
        )


@pytest.mark.parametrize("url", ["http://example.test", "https://user@example.test", "https://example.test/#fragment"])
def test_verify_site_commit_requires_safe_https_url(url: str) -> None:
    with pytest.raises(SiteCommitVerificationError, match="public_site_url_https_required"):
        verify_site_commit(url, "a" * 40, attempts=1)
