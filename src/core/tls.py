"""TLS helpers for upstream servers that omit intermediate certificates."""

from __future__ import annotations

import hashlib
import re
import socket
import ssl
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

from src.core import get_logger

logger = get_logger(__name__)

_MISSING_ISSUER_MARKERS = (
    "unable to get local issuer certificate",
    "unable to verify the first certificate",
)


def is_missing_issuer_ssl_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    if (
        "certificate_verify_failed" not in text
        and "certificateverifyfailed" not in text
    ):
        return False
    return any(marker in text for marker in _MISSING_ISSUER_MARKERS)


def build_augmented_ca_bundle_for_url(
    url: str,
    *,
    extra_ca_certs: Iterable[Path | str] = (),
    timeout: float = 10.0,
) -> Optional[str]:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        return None

    port = parsed.port or (443 if parsed.scheme == "https" else None)
    if port is None:
        return None

    extra_pems = _read_extra_ca_certs(extra_ca_certs)
    dynamic_pems = _discover_aia_intermediate_pems(hostname, port, timeout=timeout)
    if not extra_pems and not dynamic_pems:
        return None

    base_bundle = Path(requests.certs.where()).read_bytes().rstrip()
    payload = base_bundle + b"\n" + b"\n".join(extra_pems + dynamic_pems) + b"\n"
    digest = hashlib.sha256(payload).hexdigest()[:16]
    safe_host = re.sub(r"[^A-Za-z0-9_.-]+", "_", hostname)
    bundle_path = (
        Path(tempfile.gettempdir())
        / "globalid_tls_bundles"
        / f"{safe_host}_{digest}.pem"
    )
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    if not bundle_path.exists() or bundle_path.read_bytes() != payload:
        bundle_path.write_bytes(payload)
    return str(bundle_path)


@dataclass
class RequestsTLSChainFallback:
    """Retry requests with a host-specific CA bundle when intermediates are omitted."""

    extra_ca_certs: Iterable[Path | str] = ()
    timeout: float = 10.0
    log_label: str = "TLS"
    _verify_by_host: dict[str, str] = field(default_factory=dict)

    def request(
        self, session: requests.Session, method: str, url: str, **kwargs
    ) -> requests.Response:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        requested_verify = kwargs.pop("verify", True)
        verify = (
            self._verify_by_host.get(hostname, requested_verify)
            if requested_verify is True
            else requested_verify
        )

        try:
            return session.request(method, url, verify=verify, **kwargs)
        except requests.exceptions.SSLError as exc:
            if verify is not True or not is_missing_issuer_ssl_error(exc):
                raise

            bundle = build_augmented_ca_bundle_for_url(
                url,
                extra_ca_certs=self.extra_ca_certs,
                timeout=self.timeout,
            )
            if not bundle:
                raise

            if hostname:
                self._verify_by_host[hostname] = bundle
            logger.warning(
                f"{self.log_label} TLS chain fallback enabled | "
                f"host={hostname or '<unknown>'} url={url} error={exc}"
            )
            return session.request(method, url, verify=bundle, **kwargs)


def _read_extra_ca_certs(paths: Iterable[Path | str]) -> list[bytes]:
    certs: list[bytes] = []
    for value in paths:
        path = Path(value)
        if path.exists():
            certs.append(path.read_bytes().strip())
    return certs


def _discover_aia_intermediate_pems(
    hostname: str, port: int, *, timeout: float
) -> list[bytes]:
    try:
        current_cert = _fetch_leaf_certificate(hostname, port, timeout=timeout)
    except Exception as exc:
        logger.debug(
            f"TLS AIA discovery failed to fetch leaf certificate | "
            f"host={hostname} error={exc}"
        )
        return []

    pems: list[bytes] = []
    seen: set[bytes] = set()
    for _ in range(4):
        next_cert = _fetch_issuer_from_aia(current_cert, timeout=timeout)
        if next_cert is None:
            break
        fingerprint = next_cert.fingerprint(hashes.SHA256())
        if fingerprint in seen:
            break
        seen.add(fingerprint)
        if next_cert.subject == next_cert.issuer:
            break
        pems.append(next_cert.public_bytes(serialization.Encoding.PEM).strip())
        current_cert = next_cert
    return pems


def _fetch_leaf_certificate(
    hostname: str, port: int, *, timeout: float
) -> x509.Certificate:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((hostname, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as tls_sock:
            cert_bytes = tls_sock.getpeercert(binary_form=True)
    if not cert_bytes:
        raise RuntimeError(f"No peer certificate returned by {hostname}:{port}")
    return x509.load_der_x509_certificate(cert_bytes)


def _fetch_issuer_from_aia(
    cert: x509.Certificate, *, timeout: float
) -> Optional[x509.Certificate]:
    for issuer_url in _ca_issuer_urls(cert):
        try:
            response = requests.get(issuer_url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.debug(
                f"TLS AIA issuer download failed | url={issuer_url} error={exc}"
            )
            continue
        for candidate in _certs_from_content(response.content):
            if candidate.subject == cert.issuer:
                return candidate
    return None


def _ca_issuer_urls(cert: x509.Certificate) -> list[str]:
    try:
        aia = cert.extensions.get_extension_for_oid(
            ExtensionOID.AUTHORITY_INFORMATION_ACCESS
        ).value
    except x509.ExtensionNotFound:
        return []
    urls: list[str] = []
    for item in aia:
        if item.access_method != AuthorityInformationAccessOID.CA_ISSUERS:
            continue
        location = item.access_location
        value = getattr(location, "value", None)
        if isinstance(value, str) and value.lower().startswith(("http://", "https://")):
            urls.append(value)
    return urls


def _certs_from_content(content: bytes) -> list[x509.Certificate]:
    if b"-----BEGIN CERTIFICATE-----" in content:
        return list(x509.load_pem_x509_certificates(content))
    try:
        return [x509.load_der_x509_certificate(content)]
    except ValueError:
        return []
