"""Bounded, integrity-checked cache of complete official INS bulletin PDFs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import requests

from src.data.crawlers.co_bes import BESContractError, _official_pdf

MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024


def _read_cache_file(path: Path, limit: int) -> bytes:
    """Never follow receipt/blob symlinks or read unbounded cache files."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise BESContractError("INS bulletin cache file outside limits")
        payload = source.read(limit + 1)
    if len(payload) > limit:
        raise BESContractError("INS bulletin cache file outside limits")
    return payload


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".ins-bes-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _check_pdf(payload: bytes) -> None:
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise BESContractError("Incomplete INS bulletin PDF response")
    if len(payload) > MAX_PDF_BYTES:
        raise BESContractError("INS bulletin PDF exceeds byte limit")


class BESPDFCache:
    def __init__(self, directory: Path | str = "data/cache/co/ins_bes_reports"):
        self.directory = Path(directory)

    def get(self, transport, url: str, *, ttl_hours: float = 24, force: bool = False):
        if _official_pdf(url) != url:
            raise BESContractError("INS bulletin URL is not official")
        self.directory.mkdir(parents=True, exist_ok=True)
        receipt_path = self.directory / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        now = datetime.now(timezone.utc)
        if receipt_path.exists() or receipt_path.is_symlink():
            blob = None
            try:
                receipt = json.loads(_read_cache_file(receipt_path, MAX_RECEIPT_BYTES))
                if not isinstance(receipt, dict):
                    raise BESContractError("INS bulletin cache receipt must be an object")
                digest = receipt.get("sha256")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise BESContractError("INS bulletin cache digest invalid")
                # The receipt never supplies a path, only a validated digest.
                blob = self.directory / (digest + ".pdf")
                final_url = receipt.get("final_url")
                if not isinstance(final_url, str) or _official_pdf(final_url) != final_url:
                    raise BESContractError("INS bulletin cache final URL is not official")
                payload = _read_cache_file(blob, MAX_PDF_BYTES)
                _check_pdf(payload)
                if (receipt.get("url") != url or type(receipt.get("byte_size")) is not int
                        or len(payload) != receipt["byte_size"]
                        or hashlib.sha256(payload).hexdigest() != digest):
                    raise BESContractError("INS bulletin cache integrity failed")
                checked = datetime.fromisoformat(receipt["retrieved_at"])
                if checked.tzinfo is None or checked > now:
                    raise BESContractError("INS bulletin cache retrieval timestamp invalid")
            except (BESContractError, OSError, ValueError, KeyError, TypeError):
                # Move evidence, including symlinks themselves, without following
                # their targets. Hashed/UUID paths never contain source secrets.
                quarantine = self.directory / "quarantine" / receipt_path.stem / uuid4().hex
                quarantine.mkdir(parents=True, exist_ok=True)
                for source, name in ((receipt_path, "receipt.json"), (blob, "payload.pdf")):
                    if source is not None and (source.exists() or source.is_symlink()):
                        os.replace(source, quarantine / name)
            else:
                if not force and now - checked < timedelta(hours=ttl_hours):
                    return payload, receipt | {"cache_status": "hit_verified"}
        response = transport.get(url, stream=True)
        try:
            if _official_pdf(response.url) != response.url:
                raise BESContractError("INS bulletin final URL is not official")
            declared = response.headers.get("Content-Length")
            try:
                length = int(declared) if declared is not None else None
            except ValueError:
                raise BESContractError("INS bulletin Content-Length invalid") from None
            if length is not None and (length < 1 or length > MAX_PDF_BYTES):
                raise BESContractError("INS bulletin Content-Length outside limits")
            chunks = []
            count = 0
            try:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    count += len(chunk)
                    if count > MAX_PDF_BYTES:
                        raise BESContractError("INS bulletin PDF exceeds byte limit")
                    chunks.append(chunk)
            except requests.RequestException as exc:
                raise BESContractError(f"INS bulletin stream failed: {type(exc).__name__}") from None
            payload = b"".join(chunks)
            if length is not None and not response.headers.get("Content-Encoding") and count != length:
                raise BESContractError("INS bulletin Content-Length mismatch")
            _check_pdf(payload)
            digest = hashlib.sha256(payload).hexdigest()
            receipt = {"url": url, "final_url": response.url, "sha256": digest,
                       "byte_size": count, "content_length": length,
                       "retrieved_at": now.isoformat()}
            _atomic_write(self.directory / (digest + ".pdf"), payload)
            _atomic_write(receipt_path, json.dumps(receipt, sort_keys=True).encode())
            return payload, receipt | {"cache_status": "downloaded"}
        finally:
            response.close()
