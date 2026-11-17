"""Explicit, official-host-only HTTPS transport for Colombia INS originals."""
from __future__ import annotations

import importlib.util
import argparse
import hashlib
import json
import os
from urllib.parse import urljoin, urlsplit

import requests

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
PREFLIGHT_URL = "https://www.ins.gov.co/buscador-eventos/Paginas/Vista-Boletin-Epidemilogico.aspx"
MAX_PREFLIGHT_BYTES = 8 * 1024 * 1024


class INSTransportError(RuntimeError):
    pass


def _official_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        raise INSTransportError("INS source URL must use an official HTTPS host") from None
    if (parsed.scheme != "https" or parsed.username is not None or
            parsed.password is not None or
            not (host == "ins.gov.co" or host.endswith(".ins.gov.co"))):
        raise INSTransportError("INS source URL must use an official HTTPS host")
    return url


def _proxy_from_environment() -> str | None:
    http_proxy = os.environ.get("CO_INS_HTTP_PROXY")
    socks_proxy = os.environ.get("CO_INS_SOCKS_PROXY")
    if http_proxy and socks_proxy:
        raise INSTransportError("Configure only one CO INS proxy transport")
    if socks_proxy:
        try:
            parsed = urlsplit(socks_proxy)
            valid = parsed.scheme == "socks5h" and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid:
            raise INSTransportError("CO_INS_SOCKS_PROXY must use socks5h://")
        if importlib.util.find_spec("socks") is None:
            raise INSTransportError("CO_INS_SOCKS_PROXY requires PySocks")
        return socks_proxy
    if http_proxy:
        try:
            parsed = urlsplit(http_proxy)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid:
            raise INSTransportError("CO_INS_HTTP_PROXY must use http:// or https://")
        return http_proxy
    return None


def ins_transport_readiness() -> dict[str, bool | str]:
    """Expose configuration readiness without disclosing outlet identity."""
    http_proxy = os.environ.get("CO_INS_HTTP_PROXY")
    socks_proxy = os.environ.get("CO_INS_SOCKS_PROXY")
    if http_proxy and socks_proxy:
        return {"configured": True, "proxy_kind": "conflict", "dependency_ready": False}
    if socks_proxy:
        try:
            parsed = urlsplit(socks_proxy)
            ready = (parsed.scheme == "socks5h" and bool(parsed.hostname)
                     and importlib.util.find_spec("socks") is not None)
        except ValueError:
            ready = False
        return {"configured": True, "proxy_kind": "socks", "dependency_ready": ready}
    if http_proxy:
        try:
            parsed = urlsplit(http_proxy)
            ready = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            ready = False
        return {"configured": True, "proxy_kind": "http", "dependency_ready": ready}
    return {"configured": False, "proxy_kind": "direct", "dependency_ready": True}


class ColombiaINSOfficialTransport:
    """Fetch complete responses through a chosen outlet without ambient proxies.

    Source byte validation belongs to the caller. A failed proxy or TLS request
    raises; it never falls back to a different outlet or returns empty bytes.
    """

    def __init__(self, *, session: requests.Session | None = None, timeout: float = 90):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "GlobalID/2.0 (INS official bulletin archive)"})
        self.timeout = timeout
        self.proxy = _proxy_from_environment()

    def get(self, url: str, *, params=None, headers=None, timeout=None, stream=False) -> requests.Response:
        current = _official_url(url)
        for redirect in range(MAX_REDIRECTS + 1):
            try:
                response = self.session.get(
                    current, params=params if redirect == 0 else None,
                    headers=headers, timeout=timeout or self.timeout,
                    stream=stream, allow_redirects=False, verify=True,
                    proxies={"https": self.proxy} if self.proxy else {},
                )
            except requests.RequestException as exc:
                # Requests errors can echo a credential-bearing proxy URL.
                raise INSTransportError(f"INS HTTPS request failed: {type(exc).__name__}") from None
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise INSTransportError("INS redirect has no Location header")
                current = _official_url(urljoin(response.url or current, location))
                continue
            if response.status_code != 200:
                status = response.status_code
                response.close()
                raise INSTransportError(f"INS HTTPS response status {status}")
            return response
        raise INSTransportError("INS HTTPS redirect limit exceeded")

    def close(self) -> None:
        self.session.close()


def preflight(url: str = PREFLIGHT_URL) -> dict[str, str | int]:
    """Fetch and validate one complete official archive HTML response."""
    transport = ColombiaINSOfficialTransport()
    try:
        response = transport.get(url, stream=True)
        try:
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                raise INSTransportError("INS archive response failed HTML identity check")
            declared_length = response.headers.get("Content-Length")
            expected_length = None
            if declared_length is not None:
                try:
                    expected_length = int(declared_length)
                except ValueError:
                    raise INSTransportError("INS archive response has invalid length") from None
                if expected_length < 0 or expected_length > MAX_PREFLIGHT_BYTES:
                    raise INSTransportError("INS archive response exceeds size limit")
            chunks = []
            byte_size = 0
            digest = hashlib.sha256()
            try:
                for chunk in response.iter_content(chunk_size=65536):
                    byte_size += len(chunk)
                    if byte_size > MAX_PREFLIGHT_BYTES:
                        raise INSTransportError("INS archive response exceeds size limit")
                    digest.update(chunk)
                    chunks.append(chunk)
            except requests.RequestException as exc:
                raise INSTransportError(f"INS HTTPS response read failed: {type(exc).__name__}") from None
            payload = b"".join(chunks)
            if (len(payload) < 1024 or b"bolet" not in payload.lower()
                    or b"</html" not in payload.lower()):
                raise INSTransportError("INS archive response failed HTML identity check")
            if (expected_length is not None and not response.headers.get("Content-Encoding")
                    and byte_size != expected_length):
                raise INSTransportError("INS archive response length mismatch")
            return {"url": response.url, "byte_size": byte_size,
                    "sha256": digest.hexdigest()}
        finally:
            response.close()
    finally:
        transport.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        print(json.dumps(preflight(), sort_keys=True))
    else:
        print(json.dumps(ins_transport_readiness(), sort_keys=True))
