from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from src.core.tls import RequestsTLSChainFallback, is_missing_issuer_ssl_error


def test_missing_issuer_ssl_error_detection():
    error = requests.exceptions.SSLError(
        "HTTPSConnectionPool(host='example.test', port=443): "
        "Max retries exceeded (Caused by SSLError(SSLCertVerificationError(1, "
        "'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "unable to get local issuer certificate (_ssl.c:1000)')))"
    )

    assert is_missing_issuer_ssl_error(error) is True
    assert is_missing_issuer_ssl_error(requests.exceptions.SSLError("ssl eof")) is False


def test_requests_tls_chain_fallback_retries_with_augmented_bundle(
    tmp_path, monkeypatch
):
    bundle = tmp_path / "bundle.pem"
    bundle.write_text("bundle", encoding="utf-8")
    calls = []

    class FakeSession:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if len(calls) == 1:
                raise requests.exceptions.SSLError(
                    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                    "unable to get local issuer certificate"
                )
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        "src.core.tls.build_augmented_ca_bundle_for_url",
        lambda *_args, **_kwargs: str(bundle),
    )

    fallback = RequestsTLSChainFallback(log_label="test")
    response = fallback.request(
        FakeSession(), "GET", "https://example.test/data.csv", timeout=5
    )

    assert response.status_code == 200
    assert calls[0][2]["verify"] is True
    assert calls[1][2]["verify"] == str(bundle)
    assert all(call[2]["verify"] is not False for call in calls)


def test_requests_tls_chain_fallback_does_not_retry_other_ssl_errors(monkeypatch):
    calls = []

    class FakeSession:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            raise requests.exceptions.SSLError("ssl eof")

    monkeypatch.setattr(
        "src.core.tls.build_augmented_ca_bundle_for_url",
        lambda *_args, **_kwargs: pytest.fail("fallback should not build a bundle"),
    )

    fallback = RequestsTLSChainFallback(log_label="test")
    with pytest.raises(requests.exceptions.SSLError, match="ssl eof"):
        fallback.request(
            FakeSession(), "GET", "https://example.test/data.csv", timeout=5
        )

    assert len(calls) == 1
