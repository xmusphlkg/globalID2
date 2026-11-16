"""Official INS transport outlet and redirect protections."""
from __future__ import annotations

import requests
import pytest
import hashlib

from src.data.crawlers.co_ins_transport import (
    ColombiaINSOfficialTransport, INSTransportError, MAX_PREFLIGHT_BYTES,
    ins_transport_readiness, preflight,
)


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers = {}
        self.trust_env = True
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        response.url = url
        return response

    def close(self):
        self.closed = True


def response(status=200, *, location=None):
    item = requests.Response()
    item.status_code = status
    item._content = b"official bytes"
    item._content_consumed = True
    if location:
        item.headers["Location"] = location
    return item


def test_direct_request_is_tls_verified_and_ignores_ambient_proxy(monkeypatch):
    monkeypatch.delenv("CO_INS_HTTP_PROXY", raising=False)
    monkeypatch.delenv("CO_INS_SOCKS_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.example:8080")
    session = FakeSession([response()])
    transport = ColombiaINSOfficialTransport(session=session)
    assert transport.get("https://www.ins.gov.co/archive/report.pdf").content == b"official bytes"
    assert session.trust_env is False
    assert session.calls[0][1]["verify"] is True
    assert session.calls[0][1]["proxies"] == {}
    transport.close()
    assert session.closed


def test_explicit_proxy_and_same_host_redirect(monkeypatch):
    monkeypatch.setenv("CO_INS_HTTP_PROXY", "http://user:secret@approved-gateway.local:7890")
    monkeypatch.delenv("CO_INS_SOCKS_PROXY", raising=False)
    session = FakeSession([response(302, location="/archive/report.pdf"), response()])
    transport = ColombiaINSOfficialTransport(session=session)
    assert transport.get("https://www.ins.gov.co/archive/").status_code == 200
    assert session.calls[1][0] == "https://www.ins.gov.co/archive/report.pdf"
    assert session.calls[0][1]["proxies"]["https"].startswith("http://user:")
    assert all(call[1]["verify"] is True for call in session.calls)


def test_official_host_and_redirect_are_fail_closed(monkeypatch):
    monkeypatch.delenv("CO_INS_HTTP_PROXY", raising=False)
    monkeypatch.delenv("CO_INS_SOCKS_PROXY", raising=False)
    session = FakeSession([response(302, location="https://attacker.example/report.pdf")])
    transport = ColombiaINSOfficialTransport(session=session)
    with pytest.raises(INSTransportError, match="official HTTPS host"):
        transport.get("https://www.ins.gov.co/archive/")
    assert len(session.calls) == 1
    with pytest.raises(INSTransportError, match="official HTTPS host"):
        transport.get("https://ins.gov.co.attacker.example/archive/")
    assert len(session.calls) == 1


def test_proxy_failure_does_not_reveal_credentials_or_fallback(monkeypatch):
    proxy = "http://user:secret@approved-gateway.local:7890"
    monkeypatch.setenv("CO_INS_HTTP_PROXY", proxy)
    monkeypatch.delenv("CO_INS_SOCKS_PROXY", raising=False)
    session = FakeSession([requests.exceptions.ProxyError(f"Cannot connect to {proxy}")])
    transport = ColombiaINSOfficialTransport(session=session)
    with pytest.raises(INSTransportError) as error:
        transport.get("https://www.ins.gov.co/archive/report.pdf")
    assert "secret" not in str(error.value)
    assert "approved-gateway" not in str(error.value)
    assert len(session.calls) == 1


def test_partial_response_cannot_be_treated_as_complete_source(monkeypatch):
    monkeypatch.delenv("CO_INS_HTTP_PROXY", raising=False)
    monkeypatch.delenv("CO_INS_SOCKS_PROXY", raising=False)
    transport = ColombiaINSOfficialTransport(session=FakeSession([response(206)]))
    with pytest.raises(INSTransportError, match="status 206"):
        transport.get("https://www.ins.gov.co/archive/report.pdf")


def test_socks_requires_remote_dns_and_installed_support(monkeypatch):
    monkeypatch.delenv("CO_INS_HTTP_PROXY", raising=False)
    monkeypatch.setenv("CO_INS_SOCKS_PROXY", "socks5://user:secret@approved-gateway.local:1080")
    with pytest.raises(INSTransportError, match="socks5h"):
        ColombiaINSOfficialTransport(session=FakeSession([]))
    monkeypatch.setenv("CO_INS_SOCKS_PROXY", "socks5h://user:secret@approved-gateway.local:1080")
    with pytest.raises(INSTransportError, match="PySocks"):
        ColombiaINSOfficialTransport(session=FakeSession([]))


def test_readiness_discloses_no_outlet_identity(monkeypatch):
    monkeypatch.delenv("CO_INS_HTTP_PROXY", raising=False)
    monkeypatch.delenv("CO_INS_SOCKS_PROXY", raising=False)
    assert ins_transport_readiness() == {
        "configured": False, "proxy_kind": "direct", "dependency_ready": True,
    }
    monkeypatch.setenv("CO_INS_HTTP_PROXY", "http://user:secret@approved-gateway.local:7890")
    report = ins_transport_readiness()
    assert report == {"configured": True, "proxy_kind": "http", "dependency_ready": True}
    assert "secret" not in str(report) and "approved-gateway" not in str(report)
    monkeypatch.setenv("CO_INS_SOCKS_PROXY", "socks5h://user:secret@other.local:1080")
    assert ins_transport_readiness() == {
        "configured": True, "proxy_kind": "conflict", "dependency_ready": False,
    }


def test_preflight_records_complete_html_and_closes_response(monkeypatch):
    payload = b"<html>" + b"Boletin " + b"x" * 1024 + b"</html>"
    item = response()
    item._content = payload
    item.headers["Content-Type"] = "text/html; charset=utf-8"
    item.headers["Content-Length"] = str(len(payload))
    transport = ColombiaINSOfficialTransport(session=FakeSession([item]))
    monkeypatch.setattr("src.data.crawlers.co_ins_transport.ColombiaINSOfficialTransport",
                        lambda: transport)
    assert preflight() == {
        "url": "https://www.ins.gov.co/buscador-eventos/Paginas/Vista-Boletin-Epidemilogico.aspx",
        "byte_size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
    }
    assert transport.session.closed
    assert item.raw is None or item.raw.closed


def test_preflight_rejects_declared_oversize_before_reading(monkeypatch):
    item = response()
    item.headers["Content-Type"] = "text/html"
    item.headers["Content-Length"] = str(MAX_PREFLIGHT_BYTES + 1)
    transport = ColombiaINSOfficialTransport(session=FakeSession([item]))
    monkeypatch.setattr("src.data.crawlers.co_ins_transport.ColombiaINSOfficialTransport",
                        lambda: transport)
    with pytest.raises(INSTransportError, match="size limit"):
        preflight()


def test_preflight_sanitizes_stream_failure(monkeypatch):
    item = response()
    item.headers["Content-Type"] = "text/html"
    proxy = "http://user:secret@approved-gateway.local:7890"

    def broken_stream(**_kwargs):
        raise requests.exceptions.ChunkedEncodingError(f"read through {proxy} failed")
        yield b""

    item.iter_content = broken_stream
    transport = ColombiaINSOfficialTransport(session=FakeSession([item]))
    monkeypatch.setattr("src.data.crawlers.co_ins_transport.ColombiaINSOfficialTransport",
                        lambda: transport)
    with pytest.raises(INSTransportError, match="ChunkedEncodingError") as error:
        preflight()
    assert "secret" not in str(error.value)
    assert "approved-gateway" not in str(error.value)
