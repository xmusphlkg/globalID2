"""Incremental R2 publisher for the papers hosting channel."""

from __future__ import annotations

import fcntl
import base64
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import httpx

from .artifact_index import ArtifactIndex
from .papers_bundle import DEFAULT_PAPERS_ORIGIN, LIBRARY_SHARD_PATH, build_papers_manifest, is_obsolete_research_build_path
from .research_publishing import is_research_card, research_social_cards_enabled
from .r2_object_index import R2ObjectIndex, metadata_matches

MANIFEST_KEY = "papers-manifest.json"
# Cloudflare R2 occasionally drops TLS mid-PUT on large incremental syncs.
# Retry a few times inside the publisher so one EOF does not fail the release.
_TRANSPORT_RETRY_ATTEMPTS = 5
_TRANSPORT_RETRY_BASE_DELAY_SECONDS = 1.0
# Python and zlib changes can change deterministic gzip bytes (and their ETag).
_STATIC_GZIP_RECIPE = f"gzip:level=6:mtime=0:python={sys.version_info[:3]}:zlib={zlib.ZLIB_RUNTIME_VERSION}"

_TOKEN_VERIFY_URL = "https://api.cloudflare.com/client/v4/user/tokens/verify"
_derived_credentials: dict[str, tuple[str, str]] = {}


def derive_r2_credentials_from_api_token(
    api_token: str,
    *,
    opener: Callable[[Request, float | None], Any] | None = None,
) -> tuple[str, str] | None:
    """Derive R2 S3 credentials from a Cloudflare API token.

    Cloudflare accepts the token id as the S3 access key id and the SHA-256 hex
    digest of the token value as the secret, so a token with R2 permission needs
    no separately generated key pair. Returns ``None`` when the token cannot be
    verified. The result is cached per token for the process lifetime.
    """

    token = (api_token or "").strip()
    if not token:
        return None
    cached = _derived_credentials.get(token)
    if cached:
        return cached
    request = Request(_TOKEN_VERIFY_URL, headers={"Authorization": f"Bearer {token}"})
    call = opener or (lambda req, timeout: urlopen(req, timeout=timeout))
    try:
        with call(request, 30.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    result = payload.get("result") or {}
    token_id = str(result.get("id") or "").strip()
    if not payload.get("success") or result.get("status") != "active" or not token_id:
        return None
    credentials = (token_id, hashlib.sha256(token.encode("utf-8")).hexdigest())
    _derived_credentials[token] = credentials
    return credentials


@dataclass(frozen=True)
class R2PapersConfig:
    """Connection settings for one independently published R2 site."""

    account_id: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    public_base_url: str = DEFAULT_PAPERS_ORIGIN
    endpoint_url: str | None = None
    manifest_key: str = MANIFEST_KEY

    @property
    def s3_endpoint(self) -> str:
        if self.endpoint_url:
            return self.endpoint_url.rstrip("/")
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> R2PapersConfig | None:
        payload = values or {}
        account_id = str(payload.get("account_id") or "").strip()
        bucket = str(payload.get("bucket") or "").strip()
        access_key_id = str(payload.get("access_key_id") or "").strip()
        secret_access_key = str(payload.get("secret_access_key") or "").strip()
        public_base_url = str(
            payload.get("public_base_url") or DEFAULT_PAPERS_ORIGIN
        ).strip()
        endpoint_url = str(payload.get("endpoint_url") or "").strip() or None
        manifest_key = str(payload.get("manifest_key") or MANIFEST_KEY).strip()
        if not (account_id and bucket and access_key_id and secret_access_key):
            return None
        return cls(
            account_id=account_id,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            public_base_url=public_base_url.rstrip("/"),
            endpoint_url=endpoint_url,
            manifest_key=manifest_key,
        )


@dataclass(frozen=True)
class PapersPublishResult:
    """Summary of one incremental papers publish."""

    uploaded: int
    removed: int
    unchanged: int
    bytes_uploaded: int
    manifest_url: str
    sample_url: str | None
    sample_ok: bool


class R2S3Error(RuntimeError):
    """S3-compatible R2 transport or protocol failure."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def _sign_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    key_date = _hmac_sha256(("AWS4" + secret).encode("utf-8"), datestamp.encode("utf-8"))
    key_region = _hmac_sha256(key_date, region.encode("utf-8"))
    key_service = _hmac_sha256(key_region, service.encode("utf-8"))
    return _hmac_sha256(key_service, b"aws4_request")


@contextmanager
def _http_open(client: httpx.Client, request: Request, timeout: float):
    """Adapt pooled HTTPX responses to the injectable urllib-style interface."""
    try:
        response = client.request(
            request.get_method(), request.full_url,
            headers=dict(request.header_items()), content=request.data, timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise urllib.error.URLError(str(exc)) from exc
    try:
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url, response.status_code, response.reason_phrase,
                response.headers, io.BytesIO(response.content),
            )
        response.status = response.status_code
        yield response
    finally:
        response.close()


class R2S3Client:
    """Minimal SigV4 S3 client for Cloudflare R2 object operations."""

    def __init__(
        self,
        config: R2PapersConfig,
        *,
        opener: Callable[[Request, float | None], Any] | None = None,
        timeout_seconds: float = 120.0,
        max_total_bytes: int | None = None,
        object_index: R2ObjectIndex | bool | None = None,
    ) -> None:
        self.config = config
        self.manifest_key = config.manifest_key
        self.max_total_bytes = (int(os.environ.get("R2_PAPERS_MAX_TOTAL_BYTES", "8000000000"))
                                if max_total_bytes is None else max_total_bytes)
        if type(self.max_total_bytes) is not int or self.max_total_bytes <= 0:
            raise ValueError("R2 storage budget must be a positive integer")
        if isinstance(object_index, R2ObjectIndex) and (
                object_index.endpoint != config.s3_endpoint.rstrip("/") or object_index.bucket != config.bucket):
            raise ValueError("R2 object index belongs to another endpoint or bucket")
        self.object_index = (None if object_index is False else object_index
            if isinstance(object_index, R2ObjectIndex) else R2ObjectIndex(
                config.s3_endpoint, config.bucket, max_total_bytes=self.max_total_bytes))
        self._owns_index = self.object_index is not None and object_index is not self.object_index
        self._index_mutex = threading.Lock()
        self._index_checked_at = 0.0
        self._publication_mutex = threading.RLock()
        self._publication_depth = 0
        # Preserve production's per-thread pools: one slow transfer must not
        # hold the shared HTTP pool lock for the entire publication.
        self._http = None  # Optional injected HTTP transport for protocol tests.
        self._http_local = threading.local()
        self._http_clients: list[httpx.Client] = []
        self._http_clients_lock = threading.Lock()
        self._http_closed = False
        self._opener = opener or self._pooled_open
        self.timeout_seconds = timeout_seconds
        self.region = "auto"
        self.service = "s3"
        self._operation_lock = threading.Lock()
        self._operation_counts: dict[str, int] = {}

    def operation_counts(self) -> dict[str, Any]:
        """Count transport attempts, including retries; not a billing invoice.

        Reused local receipts never reach the transport. LIST pages each count
        once; HEAD/GET are reads and DeleteObjects is a free operation.
        """
        with self._operation_lock:
            counts = dict(self._operation_counts)
        return {
            "requests": sum(counts.values()),
            "class_a_requests": sum(counts.get(key, 0) for key in ("put", "copy", "list")),
            "class_b_requests": sum(counts.get(key, 0) for key in ("get", "head")),
            "delete_requests": counts.get("delete", 0),
            "by_operation": counts,
        }

    def _record_operation(self, method: str, query: str, headers: dict | None) -> None:
        operation = method.lower()
        if method == "GET" and "list-type" in urllib.parse.parse_qs(query):
            operation = "list"
        elif method == "PUT" and (headers or {}).get("x-amz-copy-source"):
            operation = "copy"
        elif method == "POST" and query == "delete=":
            operation = "delete"
        with self._operation_lock:
            self._operation_counts[operation] = self._operation_counts.get(operation, 0) + 1

    @contextmanager
    def _pooled_open(self, request: Request, timeout: float):
        """Reuse each worker's TLS connections without a shared pool lock."""
        client = self._http or getattr(self._http_local, "client", None)
        if client is None:
            client = httpx.Client(
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=2, keepalive_expiry=30.0),
                follow_redirects=False,
            )
            with self._http_clients_lock:
                closed = self._http_closed
                if not closed:
                    self._http_clients.append(client)
                    self._http_local.client = client
            if closed:
                client.close()
                raise RuntimeError("Cannot send a request, as the client has been closed.")
        with _http_open(client, request, timeout) as response:
            yield response

    def close(self) -> None:
        with self._http_clients_lock:
            self._http_closed = True
            clients, self._http_clients = self._http_clients, []
        if self._http is not None:
            clients.append(self._http)
        error = None
        try:
            for client in clients:
                try:
                    client.close()
                except Exception as exc:
                    error = error or exc
        finally:
            if self._owns_index:
                self.object_index.close()
                self._owns_index = False
        if error is not None:
            raise error

    @contextmanager
    def _index_lock(self, *, exclusive: bool = False):
        """All indexed writers and inventory refreshes share this local fence."""
        if self.object_index is None:
            yield
            return
        identity = self.object_index.target
        path = self.object_index.lock_dir / f"r2-index-{identity}.lock"
        with path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def publication_session(self):
        """Serialize complete publications and single-version cleanup locally.

        This lock is distinct from the inventory lock. Upload pool threads use
        their owning client's session; nested research JSON publication reuses
        it. The caller must join all uploads before leaving the session.
        """
        if self.object_index is None:
            yield
            return
        with self._publication_mutex:
            handle = None
            if self._publication_depth == 0:
                path = self.object_index.lock_dir / f"r2-publication-{self.object_index.target}.lock"
                handle = path.open("a")
                fcntl.flock(handle, fcntl.LOCK_EX)
            self._publication_depth += 1
            try:
                yield
            finally:
                self._publication_depth -= 1
                if handle is not None:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                    handle.close()

    @contextmanager
    def _publication_operation(self):
        """Keep standalone writes/deletes outside another process's cleanup."""
        if self.object_index is None or self._publication_depth:
            yield
            return
        path = self.object_index.lock_dir / f"r2-publication-{self.object_index.target}.lock"
        with path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def refresh_inventory(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        """Reconcile once daily; ordinary publications reuse the durable database.

        Direct/external writers must use this database or trigger a refresh.
        Never clear ambiguous upload reservations without an operator fence.
        """
        if self.object_index is None:
            return self.list_objects()
        with self._index_mutex, self._index_lock(exclusive=True):
            if force or not self.object_index.inventory_is_fresh():
                self.object_index.replace_inventory(self.list_objects())
            self._index_checked_at = time.monotonic()
            return self.object_index.snapshot()

    def _ensure_inventory(self) -> None:
        if self.object_index is None:
            return
        # Double-check after taking the mutex: a whole upload pool may reach
        # this method together. Never materialize the bucket once per worker.
        with self._index_mutex:
            if self._index_checked_at and time.monotonic() - self._index_checked_at <= 300:
                return
            with self._index_lock(exclusive=True):
                if not self.object_index.inventory_is_fresh():
                    self.object_index.replace_inventory(self.list_objects())
                self._index_checked_at = time.monotonic()

    def preflight_objects(self, objects: dict[str, int]) -> None:
        """Reject an immutable candidate's conservative size before any PUT."""
        self._ensure_inventory()
        if self.object_index is not None:
            with self._index_lock():
                self.object_index.preflight(objects, max_total_bytes=self.max_total_bytes)

    def has_immutable_object(self, key: str, *, content_sha256: str,
                             content_type: str, cache_control: str) -> bool:
        """Reuse a decoded-content receipt before rereading local build files."""
        if self.object_index is None or not self._immutable_object(key, cache_control):
            return False
        self._ensure_inventory()
        with self._index_lock():
            return self.object_index.matches_content(key, content_sha256, content_type, cache_control)

    @staticmethod
    def _immutable_object(key: str, cache_control: str | None) -> bool:
        # Never short-circuit compare-and-swap pointers, intents or mutable URLs.
        if "immutable" not in {part.strip() for part in (cache_control or "").split(",")}:
            return False
        digest = r"[a-f0-9]{64}"
        return re.fullmatch(
            rf"(?:releases/sha256/(?:objects/{digest}/[a-f0-9]{{16}}|(?:catalogues|catalogue-shards)/{digest}\.json)"
            rf"|paper-template-v[23]/(?:records|templates|assets)/{digest}\.json"
            rf"|site-data/[a-z0-9-]+/(?:objects|manifests)/{digest}\.json)", key) is not None

    def head_object(self, key: str) -> dict[str, Any] | None:
        """Cheap Class B verification of an object absent from the local index."""
        headers: dict[str, str] = {}
        try:
            status, _ = self._request("HEAD", key, response_headers=headers)
        except R2S3Error as exc:
            if exc.status == 404:
                return None
            raise
        if status == 404:
            return None
        if status != 200:
            raise R2S3Error(f"R2 HEAD {key!r} returned HTTP {status}", status=status)
        try:
            size = int(headers["content-length"])
            etag = headers["etag"].strip('"')
            if size < 0 or not etag:
                raise ValueError("Invalid HEAD metadata")
        except (KeyError, ValueError) as exc:
            raise R2S3Error(f"R2 HEAD {key!r} returned incomplete metadata") from exc
        return {"size": size, "etag": etag, "content_type": headers.get("content-type"),
                "content_encoding": headers.get("content-encoding"), "cache_control": headers.get("cache-control")}

    def _indexed_put(self, key: str, body: bytes, *, content_type: str | None,
                     cache_control: str | None, content_encoding: str | None,
                     condition: dict[str, str] | None = None) -> bool:
        self._ensure_inventory()
        metadata = {"etag": hashlib.md5(body, usedforsecurity=False).hexdigest(),
                    "content_type": content_type, "content_encoding": content_encoding,
                    "cache_control": cache_control}
        immutable = condition == {"if-none-match": "*"} and self._immutable_object(key, cache_control)
        content_digest = (_sha256_hex(gzip.decompress(body) if content_encoding == "gzip" else body)
                          if immutable else None)
        index = self.object_index
        with self._publication_operation(), self._index_lock():
            if immutable and index is not None:
                if (index.matches(key, size=len(body), **metadata)
                        or index.matches_content(key, content_digest, content_type, cache_control)):
                    return False
                remote = self.head_object(key)
                if remote is not None:
                    if self._verify_existing_immutable(key, body, metadata, content_digest, remote):
                        return False
                    # A 412 is treated as reuse by some callers. A mismatched
                    # immutable object must instead fail without that status.
                    raise R2S3Error(f"Immutable R2 object differs from candidate: {key}")
                index.delete(key)
            token = index.reserve(key, len(body), max_total_bytes=self.max_total_bytes) if index is not None else None
            try:
                status, _ = self._request("PUT", key, body=body, content_type=content_type,
                    cache_control=cache_control, content_encoding=content_encoding, conditional_headers=condition)
                if status not in {200, 201}:
                    raise R2S3Error(f"R2 PUT {key!r} returned HTTP {status}", status=status)
            except R2S3Error as exc:
                if index is not None and exc.status is not None and 400 <= exc.status < 500:
                    # The final error may follow an earlier successful PUT
                    # whose response was lost. Reconcile actual bytes first.
                    remote = self.head_object(key)
                    index.resolve(token, remote)
                    if immutable and exc.status == 412:
                        if remote is not None and self._verify_existing_immutable(
                                key, body, metadata, content_digest, remote):
                            return False
                        raise R2S3Error(f"Immutable R2 object changed during upload: {key}") from exc
                # An interrupted PUT may have succeeded. Keep its reservation
                # until a fenced inventory repair confirms the remote state.
                raise
            if index is not None:
                index.commit(token, **metadata, content_sha256=content_digest)
            return True

    def _verify_existing_immutable(self, key: str, body: bytes, expected: dict,
                                   content_digest: str, remote: dict) -> bool:
        """Validate old encodings once, then persist decoded content identity."""
        index = self.object_index
        index.record(key, **remote)
        if index.matches(key, size=len(body), **expected):
            index.record(key, **remote, content_sha256=content_digest)
            return True
        if not metadata_matches(remote, expected, compare_encoding=False):
            return False
        headers: dict[str, str] = {}
        status, payload = self._request("GET", key, response_headers=headers)
        if status != 200 or headers.get("etag", "").strip('"') != remote["etag"]:
            raise R2S3Error(f"R2 object changed during content verification: {key}")
        # HTTPX already decodes gzip; an injected urllib transport may not.
        if headers.get("content-encoding") == "gzip" and payload.startswith(b"\x1f\x8b"):
            payload = gzip.decompress(payload)
        if _sha256_hex(payload) != content_digest:
            return False
        index.record(key, **remote, content_sha256=content_digest)
        return True

    def _host(self) -> str:
        return urllib.parse.urlparse(self.config.s3_endpoint).netloc

    def _object_url(self, key: str) -> str:
        if key == "":
            # Bucket-level requests sign "/<bucket>" with no trailing slash, so
            # the request URL must match the canonical URI exactly.
            return f"{self.config.s3_endpoint}/{self.config.bucket}"
        quoted = "/".join(urllib.parse.quote(part, safe="") for part in key.split("/"))
        return f"{self.config.s3_endpoint}/{self.config.bucket}/{quoted}"

    def _signed_request(
        self,
        method: str,
        key: str,
        *,
        body: bytes = b"",
        content_type: str | None = None,
        cache_control: str | None = None,
        content_encoding: str | None = None,
        query: str = "",
        conditional_headers: dict[str, str] | None = None,
    ) -> Request:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        payload_hash = _sha256_hex(body)
        host = self._host()
        canonical_uri = f"/{self.config.bucket}/{'/'.join(urllib.parse.quote(part, safe='') for part in key.split('/'))}"
        if key == "":
            canonical_uri = f"/{self.config.bucket}"
        canonical_query = query
        headers = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if conditional_headers:
            if set(conditional_headers) - {
                "if-match", "if-none-match", "x-amz-copy-source",
                "x-amz-copy-source-if-match", "x-amz-metadata-directive",
                "cf-copy-destination-if-none-match",
            }:
                raise ValueError("Unsupported R2 conditional header")
            headers.update(conditional_headers)
        if content_type:
            headers["content-type"] = content_type
        if cache_control:
            headers["cache-control"] = cache_control
        if content_encoding:
            headers["content-encoding"] = content_encoding
        if method == "POST" and query == "delete=":
            headers["content-md5"] = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()
        if body:
            headers["content-length"] = str(len(body))
        signed_header_names = ";".join(sorted(headers))
        canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
        canonical_request = (
            f"{method}\n{canonical_uri}\n{canonical_query}\n{canonical_headers}\n"
            f"{signed_header_names}\n{payload_hash}"
        )
        credential_scope = f"{datestamp}/{self.region}/{self.service}/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                _sha256_hex(canonical_request.encode("utf-8")),
            ]
        )
        signature = _hmac_sha256(
            _sign_key(self.config.secret_access_key, datestamp, self.region, self.service),
            string_to_sign.encode("utf-8"),
        ).hex()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.config.access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_header_names}, "
            f"Signature={signature}"
        )
        url = self._object_url(key)
        if query:
            url = f"{url}?{query}"
        request = Request(url, data=body if method in {"PUT", "POST"} else None, method=method)
        for name, value in headers.items():
            if name == "host":
                continue
            request.add_header(name, value)
        request.add_header("Authorization", authorization)
        # CDN transfer compression can turn a strong stored ETag into W/"..."
        # and omit Content-Length on HEAD. Request stored representation metadata.
        request.add_header("Accept-Encoding", "identity")
        return request

    def _request(
        self,
        method: str,
        key: str,
        *,
        body: bytes = b"",
        content_type: str | None = None,
        cache_control: str | None = None,
        content_encoding: str | None = None,
        query: str = "",
        conditional_headers: dict[str, str] | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        last_error: BaseException | None = None
        for attempt in range(1, _TRANSPORT_RETRY_ATTEMPTS + 1):
            request = self._signed_request(
                method,
                key,
                body=body,
                content_type=content_type,
                cache_control=cache_control,
                content_encoding=content_encoding,
                query=query,
                conditional_headers=conditional_headers,
            )
            try:
                self._record_operation(method, query, conditional_headers)
                with self._opener(request, self.timeout_seconds) as response:
                    if response_headers is not None:
                        response_headers.update({str(k).lower(): str(v)
                            for k, v in getattr(response, "headers", {}).items()})
                    return int(getattr(response, "status", 200)), response.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if (int(exc.code) == 429 or 500 <= int(exc.code) <= 599) and attempt < _TRANSPORT_RETRY_ATTEMPTS:
                    last_error = R2S3Error(
                        f"R2 {method} {key!r} failed with HTTP {exc.code}: {detail}",
                        status=exc.code,
                    )
                    time.sleep(_TRANSPORT_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
                    continue
                raise R2S3Error(
                    f"R2 {method} {key!r} failed with HTTP {exc.code}: {detail}",
                    status=exc.code,
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = R2S3Error(f"R2 {method} {key!r} transport failed: {exc}")
                if attempt >= _TRANSPORT_RETRY_ATTEMPTS:
                    raise last_error from exc
                time.sleep(_TRANSPORT_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error

    def put_object(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        cache_control: str | None = None,
        content_encoding: str | None = None,
    ) -> None:
        # The complete manifest remains JSON to callers. Compress its HTTP
        # representation to avoid transferring ~11 MB of repeated paths and
        # metadata on every changed release; ordinary object ETags stay intact.
        if key == self.manifest_key and content_encoding is None and len(body) > 1024:
            encoded = gzip.compress(body, compresslevel=6, mtime=0)
            if len(encoded) < len(body):
                print(f"R2 manifest transport: raw_bytes={len(body)}, encoded_bytes={len(encoded)}", flush=True)
                body, content_encoding = encoded, "gzip"
        self._indexed_put(key, body, content_type=content_type,
            cache_control=cache_control, content_encoding=content_encoding)

    def delete_object(self, key: str) -> None:
        with self._publication_operation(), self._index_lock(exclusive=True):
            try:
                status, _payload = self._request("DELETE", key)
                if status not in {200, 204}:
                    raise R2S3Error(f"R2 DELETE {key!r} returned HTTP {status}")
            except BaseException:
                if self.object_index is not None:
                    self.object_index.invalidate(key)
                    self._index_checked_at = 0
                raise
            if self.object_index is not None:
                self.object_index.delete(key)

    def delete_objects(self, keys: list[str]) -> None:
        """Delete an explicit manifest-derived batch; fail on partial errors."""
        if not keys or len(keys) > 1000 or len(set(keys)) != len(keys):
            raise ValueError("R2 delete batch requires 1–1000 distinct keys")
        root = ElementTree.Element("Delete", xmlns="http://s3.amazonaws.com/doc/2006-03-01/")
        for key in keys:
            ElementTree.SubElement(ElementTree.SubElement(root, "Object"), "Key").text = key
        ElementTree.SubElement(root, "Quiet").text = "true"
        with self._publication_operation(), self._index_lock(exclusive=True):
            try:
                status, payload = self._request("POST", "", query="delete=",
                    body=ElementTree.tostring(root, encoding="utf-8", xml_declaration=True), content_type="application/xml")
                result = ElementTree.fromstring(payload)
                errors = result.findall("{*}Error")
                if status != 200 or errors or result.tag.split('}')[-1] != 'DeleteResult':
                    raise R2S3Error(f"R2 batch deletion failed: HTTP {status}, errors={len(errors)}")
            except BaseException:
                if self.object_index is not None:
                    for key in keys:
                        self.object_index.invalidate(key)
                    self._index_checked_at = 0
                raise
            if self.object_index is not None:
                self.object_index.delete(keys)

    def put_object_conditional(
        self, key: str, body: bytes, *, previous: bytes | None,
        content_type: str = "application/json", cache_control: str = "no-store",
        content_encoding: str | None = None,
    ) -> bool:
        """Atomically replace a single-PUT pointer only at the observed version."""
        condition = ({"if-none-match": "*"} if previous is None else {
            "if-match": '"' + hashlib.md5(previous, usedforsecurity=False).hexdigest() + '"',
        })
        return self._indexed_put(key, body, content_type=content_type,
            cache_control=cache_control, content_encoding=content_encoding, condition=condition)

    def get_object(self, key: str) -> bytes | None:
        try:
            status, payload = self._request("GET", key)
        except R2S3Error as exc:
            if exc.status == 404:
                return None
            raise
        if status == 404:
            return None
        if status != 200:
            raise R2S3Error(f"R2 GET {key!r} returned HTTP {status}")
        # HTTPX decodes Content-Encoding itself; injected/urllib transports
        # may return the encoded representation. Accept both, including legacy
        # uncompressed manifests, without changing the manifest schema.
        if key == self.manifest_key and payload.startswith(b"\x1f\x8b"):
            try:
                payload = gzip.decompress(payload)
            except (OSError, EOFError) as exc:
                raise R2S3Error("Invalid compressed papers manifest") from exc
        return payload

    def head_bucket(self) -> bool:
        """Return True when the bucket accepts authenticated requests."""

        try:
            status, _payload = self._request("HEAD", "")
        except R2S3Error:
            return False
        return status in {200, 204}

    def list_objects(self) -> dict[str, dict[str, Any]]:
        """Read metadata only, including objects left by an interrupted publish.

        This publisher uses single PUTs, whose ETags identify their MD5 content.
        Multipart ETags are never accepted as a content match.
        """
        objects: dict[str, dict[str, Any]] = {}
        token = None
        seen_tokens: set[str] = set()
        while True:
            params = {"list-type": "2", "max-keys": "1000"}
            if token:
                params["continuation-token"] = token
            query = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
            status, body = self._request("GET", "", query=query)
            root = ElementTree.fromstring(body)
            if (status != 200 or root.tag.split("}")[-1] != "ListBucketResult"
                    or root.findtext("{*}IsTruncated") not in {"true", "false"}):
                raise R2S3Error("Incomplete R2 inventory response")
            for item in root.findall("{*}Contents"):
                key = item.findtext("{*}Key")
                if key is not None:
                    if key in objects:
                        raise R2S3Error("Duplicate key in R2 inventory")
                    objects[key] = {
                        "bytes": int(item.findtext("{*}Size") or "0"),
                        "etag": (item.findtext("{*}ETag") or "").strip('"'),
                    }
            if root.findtext("{*}IsTruncated") != "true":
                return objects
            token = root.findtext("{*}NextContinuationToken")
            if not token or token in seen_tokens:
                raise R2S3Error("Invalid R2 listing continuation token")
            seen_tokens.add(token)


def _content_type_for(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".html"):
        return "text/html; charset=utf-8"
    if lowered.endswith(".json"):
        return "application/json; charset=utf-8"
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith(".xml"):
        return "application/xml; charset=utf-8"
    if lowered.endswith(".txt"):
        return "text/plain; charset=utf-8"
    asset_types = {
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
        ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".avif": "image/avif", ".ico": "image/x-icon",
    }
    for suffix, content_type in asset_types.items():
        if lowered.endswith(suffix):
            return content_type
    return "application/octet-stream"


def _cache_control_for(path: str, *, manifest_key: str = MANIFEST_KEY) -> str:
    """Cache policy per object type; R2 custom domains ignore Pages ``_headers``.

    Ordinary keys are mutable. Disease pages and study collections use content
    hashes, so browsers can keep their payload without revalidation.
    """

    lowered = path.lower()
    if lowered == manifest_key.lower():
        return "no-store, max-age=0"
    if LIBRARY_SHARD_PATH.fullmatch(path):
        return "public, max-age=31536000, immutable"
    if lowered.endswith(".html"):
        return "public, max-age=0, must-revalidate"
    if lowered.endswith(".json"):
        return "public, max-age=300, stale-while-revalidate=86400"
    return "public, max-age=86400"


def load_remote_manifest(client: R2S3Client, *, manifest_key: str = MANIFEST_KEY) -> dict[str, Any]:
    """Load the previously published papers manifest, or an empty one."""

    payload = client.get_object(manifest_key)
    if payload is None:
        return {"schema_version": 1, "files": []}
    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise R2S3Error(f"Remote {manifest_key} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise R2S3Error(f"Remote {manifest_key} must be a JSON object")
    files = data.get("files")
    if files is None:
        data["files"] = []
    elif not isinstance(files, list):
        raise R2S3Error(f"Remote {manifest_key} files must be a list")
    return data


def _verify_public_sample(url: str, opener=None) -> None:
    # The public edge rejects urllib's default request (HTTP 403), while the
    # same URL succeeds with HTTPX. Use the same transport family as the uploader
    # and retain status/transport diagnostics rather than turning every failure
    # into an unexplained boolean.
    client = httpx.Client(follow_redirects=True) if opener is None else None
    call = opener or (lambda request, timeout: _http_open(client, request, timeout))
    detail = "unknown failure"
    try:
        for attempt in range(1, _TRANSPORT_RETRY_ATTEMPTS + 1):
            retryable = False
            try:
                try:
                    with call(Request(url, method="HEAD", headers={"User-Agent": "GIDS-Release-Verifier/1.0"}), 30.0) as response:
                        status = int(getattr(response, "status", 200))
                        if status in {200, 204}:
                            return
                except urllib.error.HTTPError as exc:
                    if exc.code != 405:
                        raise
                    with call(Request(url, method="GET", headers={"Range": "bytes=0-0", "User-Agent": "GIDS-Release-Verifier/1.0"}), 30.0) as response:
                        status = int(getattr(response, "status", 200))
                        if status in {200, 206}:
                            return
                detail = f"HTTP {status}"
                retryable = status == 429 or 500 <= status <= 599
            except urllib.error.HTTPError as exc:
                detail = f"HTTP {exc.code}"
                retryable = exc.code == 429 or 500 <= exc.code <= 599
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                detail = f"transport failed: {exc}"
                retryable = True
            except (ValueError, R2S3Error) as exc:
                detail = str(exc)
            if not retryable or attempt == _TRANSPORT_RETRY_ATTEMPTS:
                break
            time.sleep(_TRANSPORT_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
    finally:
        if client is not None:
            client.close()
    raise R2S3Error(f"Public papers verification failed for sample URL: {url}: {detail}")


def _is_static_article(key: str) -> bool:
    return bool(re.fullmatch(r'(?:(?:zh|fr)/)?research/articles/[a-z0-9][a-z0-9-]*/', key))


def publish_papers_site(
    papers_dir: Path,
    config: R2PapersConfig,
    *,
    client: R2S3Client | None = None,
    verify_public: bool = True,
    public_opener: Callable[[Request, float | None], Any] | None = None,
    workers: int = 32,
    lock_dir: Path | None = None,
    max_total_bytes: int | None = None,
    preserve_static_articles: bool = False,
    template_candidate: Path | None = None,
) -> PapersPublishResult:
    """Publish with one writer per target and bounded parallel object transfers."""
    if not 1 <= workers <= 96:
        raise ValueError("R2 upload workers must be between 1 and 96")
    if max_total_bytes is not None and (type(max_total_bytes) is not int or max_total_bytes <= 0):
        raise ValueError("R2 storage budget must be a positive byte count")
    directory = lock_dir or Path(__file__).resolve().parents[3] / "data/cache/r2-publish"
    directory.mkdir(parents=True, exist_ok=True)
    target = _sha256_hex(f"{config.s3_endpoint}/{config.bucket}".encode())
    with (directory / f"{target}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        active = client or R2S3Client(config, max_total_bytes=max_total_bytes)
        artifacts = ArtifactIndex(papers_dir)
        try:
            if template_candidate is not None:
                raise R2S3Error('Paper template v2 publication has been removed')
            result = _publish_papers_site(
                papers_dir, config, client=active, verify_public=verify_public,
                public_opener=public_opener, workers=workers, artifacts=artifacts,
                max_total_bytes=max_total_bytes,
                preserve_static_articles=preserve_static_articles,
            )
            return result
        finally:
            try:
                artifacts.save()
            finally:
                if client is None:
                    active.close()


def _publish_papers_site(
    papers_dir: Path,
    config: R2PapersConfig,
    *,
    client: R2S3Client,
    verify_public: bool,
    public_opener: Callable[[Request, float | None], Any] | None,
    workers: int,
    artifacts: ArtifactIndex,
    max_total_bytes: int | None = None,
    preserve_static_articles: bool = False,
) -> PapersPublishResult:
    started = time.monotonic()

    if not papers_dir.is_dir():
        raise FileNotFoundError(f"Papers site directory not found: {papers_dir}")

    manifest_key = config.manifest_key
    local_manifest_path = papers_dir / manifest_key
    if local_manifest_path.exists():
        local_manifest = json.loads(local_manifest_path.read_text(encoding="utf-8"))
    else:
        local_manifest = build_papers_manifest(
            papers_dir, papers_origin=config.public_base_url
        )
        local_manifest_path.write_text(
            json.dumps(local_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    active = client
    remote_manifest = load_remote_manifest(active, manifest_key=manifest_key)
    remote_by_key = {
        str(item.get("key")): item
        for item in remote_manifest.get("files") or []
        if isinstance(item, dict) and item.get("key") is not None
    }
    local_files = [
        item for item in local_manifest.get("files") or [] if isinstance(item, dict)
    ]
    if any(is_obsolete_research_build_path(str(item.get("path", ""))) for item in local_files):
        raise R2S3Error("Obsolete research files in papers bundle; rebuild the bundle before publishing")
    cards_disabled = manifest_key == MANIFEST_KEY and not research_social_cards_enabled()
    if cards_disabled:
        # Enforce the switch even for bundles built before it was disabled.
        local_files = [item for item in local_files if not is_research_card(str(item.get("key", "")))]
        published_files = sorted(local_files, key=lambda item: str(item.get("key", "")))
        local_manifest = {
            **local_manifest,
            "files": published_files,
            "file_count": len(published_files),
            "article_social_cards_enabled": False,
        }
    if preserve_static_articles:
        if config.manifest_key != MANIFEST_KEY:
            raise R2S3Error('Static article retention requires the canonical Papers manifest')
        if active.get_object('paper-template-v2/current.json') is None:
            raise R2S3Error('Template generation is not active; refusing to skip static article uploads')
        # Article routes are served from JSON in template mode. Keep the last
        # published static pages as a rollback fallback, not as new uploads.
        local_files = [item for item in local_files if not _is_static_article(str(item.get('key', '')))]
        local_manifest = {**local_manifest, 'files': sorted(local_files, key=lambda item: str(item.get('key', ''))), 'file_count': len(local_files)}
    local_by_key = {str(item["key"]): item for item in local_files if "key" in item}

    if not local_by_key:
        # An empty bundle would turn the orphan sweep below into a full wipe.
        raise R2S3Error(
            "Local papers manifest is empty; refusing to sync because every "
            "remote object would be deleted."
        )

    if len(local_by_key) != len(local_files):
        raise R2S3Error("Duplicate or missing keys in local papers manifest")
    source_root = papers_dir.resolve()
    for key, item in local_by_key.items():
        path = (source_root / str(item["path"])).resolve()
        if (not path.is_relative_to(source_root) or not path.is_file()
                or key == manifest_key or ".." in key.split("/")):
            raise R2S3Error(f"Invalid papers manifest path: {item['path']}")
        if path.stat().st_size != int(item["bytes"]):
            raise R2S3Error(f"Papers file size changed after build: {item['path']}")

    print(f"R2 inventory: checking {len(local_files)} files; workers={workers}", flush=True)
    # The shared database captures successful writes before a release commits.
    # A daily complete reconciliation detects changes by external writers.
    if getattr(active, "object_index", None) is not None:
        inventory = active.refresh_inventory()
    else:
        inventory = active.list_objects() if hasattr(active, "list_objects") else None

    if preserve_static_articles:
        if inventory is None:
            raise R2S3Error('Static article retention requires a full R2 inventory')
        previous_files = {str(item.get('key')): item for item in remote_manifest.get('files', []) if isinstance(item, dict) and item.get('key') is not None}
        legacy_pages = {key for key in previous_files if _is_static_article(key)}
        if not legacy_pages:
            raise R2S3Error('No published static article baseline available for fallback')
        legacy_assets = remote_manifest.get('template_legacy_asset_keys')
        if legacy_assets is None:
            legacy_assets = sorted(key for key in previous_files if key.startswith('_astro/'))
        if not isinstance(legacy_assets, list) or any(not isinstance(key, str) or not key.startswith('_astro/') for key in legacy_assets):
            raise R2S3Error('Invalid static fallback asset list')
        required = legacy_pages | set(legacy_assets)
        if any(key not in previous_files or key not in inventory for key in required):
            raise R2S3Error('A static article or fallback asset is missing from R2')
        kept = [previous_files[key] for key in sorted(required - set(local_by_key))]
        all_files = sorted([*local_files, *kept], key=lambda item: str(item['key']))
        local_manifest = {**local_manifest, 'files': all_files, 'file_count': len(all_files), 'template_legacy_asset_keys': sorted(legacy_assets)}

    retained_keys: set[str] = set()
    if inventory is not None and config.manifest_key == MANIFEST_KEY:
        from .papers_retention import retain_remote_dependencies
        local_manifest = retain_remote_dependencies(local_manifest, remote_manifest, inventory)
        retained_keys = {item['key'] for item in local_manifest['files']} - set(local_by_key)
    if max_total_bytes is not None:
        if inventory is None:
            raise R2S3Error("R2 storage budget requires a complete bucket inventory")
        # Raw local sizes conservatively bound compressed uploads. Include
        # other channels and retained generations in this shared bucket.
        obsolete = set(remote_by_key) - set(local_by_key) - retained_keys
        upper_bound = sum(int(item['bytes']) for item in local_files) + sum(
            int(item['bytes']) for key, item in inventory.items()
            if key not in local_by_key and key not in obsolete and key != manifest_key
        ) + len(json.dumps(local_manifest).encode())
        print(f"R2 storage upper bound: {upper_bound} bytes; budget={max_total_bytes}", flush=True)
        if upper_bound > max_total_bytes:
            raise R2S3Error(f"R2 storage budget exceeded before upload: {upper_bound} > {max_total_bytes}")

    uploaded = 0
    unchanged = 0
    bytes_uploaded = 0
    def transfer(item: dict) -> tuple[bool, int]:
        key = str(item["key"])
        source_path = papers_dir / str(item["path"])
        # A stat-bound receipt comes from the bundle writer or a previous
        # validated read. Cache misses (including legacy manifests) read once.
        # Unchanged objects never need their local or remote bodies opened.
        digests = artifacts.cached(source_path)
        body = None
        if digests is None:
            body, digests = artifacts.read(source_path)
        if (digests is None or digests["bytes"] != int(item["bytes"])
                or digests["sha256"] != item["sha256"]
                or (item.get("md5") and digests["md5"] != item["md5"])):
            raise R2S3Error(f"Papers checksum changed after build: {item['path']}")
        content_type = _content_type_for(str(item["path"]))
        compress = digests["bytes"] >= 1024 and content_type.startswith((
            "text/", "application/json", "application/xml", "image/svg+xml",
        ))
        # A previously published uncompressed representation is already a
        # valid object.  Do not rewrite unchanged content merely to migrate
        # it to gzip: that turns a format change into a bulk publication.
        # Check the live inventory as well as the previous manifest so an
        # interrupted upload cannot make an obsolete manifest look current.
        if compress and inventory is not None:
            previous = remote_by_key.get(key) or {}
            stored = inventory.get(key) or {}
            if (previous.get("sha256") == digests["sha256"]
                    and stored.get("bytes") == digests["bytes"]
                    and stored.get("etag") == digests["md5"]):
                return False, 0
        # A warm compressed publish needs only local stat + actual remote ETag.
        # Recompress only when a receipt is missing/stale or the remote differs.
        if compress and inventory is not None:
            representation = artifacts.cached_representation(source_path, _STATIC_GZIP_RECIPE)
            stored = inventory.get(key) or {}
            if (representation is not None and representation["source_sha256"] == digests["sha256"]
                    and stored.get("bytes") == representation["bytes"]
                    and stored.get("etag") == representation["md5"]):
                return False, 0
        if compress:
            if body is None:
                body, current = artifacts.read(source_path)
                if current != digests:
                    raise R2S3Error(f"Papers checksum changed during publish: {item['path']}")
            encoded = gzip.compress(body, compresslevel=6, mtime=0)
            if len(encoded) < len(body):
                body = encoded
            else:
                compress = False
            artifacts.record_representation(
                source_path, _STATIC_GZIP_RECIPE, digests, body,
                encoding="gzip" if compress else None,
            )
        if inventory is not None:
            stored = inventory.get(key) or {}
            etag = str(stored.get("etag") or "")
            expected_bytes = len(body) if compress else digests["bytes"]
            expected_md5 = hashlib.md5(body, usedforsecurity=False).hexdigest() if compress else digests["md5"]
            if (stored.get("bytes") == expected_bytes and re.fullmatch(r"[0-9a-f]{32}", etag)
                    and expected_md5 == etag):
                return False, 0
        else:
            # Compatibility with injected legacy clients; production always
            # checks the actual object inventory, never only the manifest.
            remote = remote_by_key.get(key) or {}
            if not compress and remote.get("bytes") == digests["bytes"] and remote.get("sha256") == item["sha256"]:
                return False, 0
        if body is None:
            body, current = artifacts.read(source_path)
            if current != digests:
                raise R2S3Error(f"Papers checksum changed during publish: {item['path']}")
        kwargs = {"content_type": content_type, "cache_control": _cache_control_for(str(item["path"]))}
        if compress:
            kwargs["content_encoding"] = "gzip"
        active.put_object(key, body, **kwargs)
        return True, len(body)

    # At most 2 * workers futures (and workers file bodies) remain in memory.
    # On failure, drain in-flight writes, cancel queued writes, then leave the
    # manifest and orphan objects alone. The next attempt lists and resumes.
    def transfer_batch(batch):
        nonlocal uploaded, unchanged, bytes_uploaded
        remaining = iter(batch)
        last_progress = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="papers-r2") as pool:
            pending = set()
            try:
                while True:
                    while len(pending) < workers * 2:
                        item = next(remaining, None)
                        if item is None:
                            break
                        pending.add(pool.submit(transfer, item))
                    if not pending:
                        break
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        changed, size = future.result()
                        uploaded += int(changed)
                        unchanged += int(not changed)
                        bytes_uploaded += size
                    if time.monotonic() - last_progress >= 15:
                        print(
                            f"R2 progress: {uploaded + unchanged}/{len(local_files)}; "
                            f"uploaded={uploaded}, reused={unchanged}, bytes={bytes_uploaded}, "
                            f"elapsed={time.monotonic() - started:.1f}s", flush=True,
                        )
                        last_progress = time.monotonic()
            except BaseException:
                for future in pending:
                    future.cancel()
                raise

    # Public indexes are commit pointers: never expose new shard URLs before
    # every referenced object exists. Old shards remain in the local retention
    # window and therefore remain in the release manifest until safe to prune.
    indexes = {"site-data/research/index.json", "site-data/research/catalogue.json", "site-data/reports/index.json"}
    indexes.update(str(item["key"]) for item in local_files if re.fullmatch(
        r"research/catalogue/diseases/[a-z0-9_-]+\.json", str(item["key"]),
    ))
    # Hydration modules and their lazy dependencies must exist before new HTML
    # can refer to them. Older hashed assets remain in the retained generation.
    transfer_batch([item for item in local_files if str(item["key"]).startswith("_astro/")])
    # Disease libraries read these page indexes immediately on hydration. Delay
    # their HTML until the pointers are ready; retain the existing publication
    # batching for independent article pages. Each batch is a parallel barrier.
    html = {str(item["key"]) for item in local_files if re.fullmatch(
        r"(?:(?:zh|fr)/)?research/diseases/[^/]+/index\.html", str(item["path"]),
    )}
    transfer_batch([item for item in local_files if item["key"] not in indexes and item["key"] not in html and not str(item["key"]).startswith("_astro/")])
    transfer_batch([item for item in local_files if item["key"] in indexes])
    transfer_batch([item for item in local_files if item["key"] in html])

    sample_url = None
    sample_ok = True
    if local_files:
        sample = next(
            (
                item
                for item in local_files
                if str(item.get("path", "")).endswith(".html")
                or str(item.get("key", "")).endswith("/")
            ),
            local_files[0],
        )
        sample_url = str(sample.get("url") or "")
        if verify_public and sample_url:
            _verify_public_sample(sample_url, public_opener)

    removed = 0
    obsolete = sorted(set(remote_by_key) - set(local_by_key) - retained_keys - {manifest_key})
    if hasattr(active, 'delete_objects'):
        for start in range(0, len(obsolete), 500):
            batch = obsolete[start:start + 500]
            active.delete_objects(batch)
            removed += len(batch)
    else:
        for key in obsolete:
            active.delete_object(key)
            removed += 1
    manifest_body = json.dumps(
        local_manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if manifest_body != json.dumps(remote_manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"):
        active.put_object(
            manifest_key, manifest_body,
            content_type="application/json; charset=utf-8",
            cache_control=_cache_control_for(manifest_key, manifest_key=manifest_key),
        )
    print(
        f"R2 publish completed in {time.monotonic() - started:.1f}s; "
        f"local files read={artifacts.read_files}, local bytes read={artifacts.read_bytes}",
        flush=True,
    )

    return PapersPublishResult(
        uploaded=uploaded,
        removed=removed,
        unchanged=unchanged,
        bytes_uploaded=bytes_uploaded,
        manifest_url=f"{config.public_base_url.rstrip('/')}/{manifest_key}",
        sample_url=sample_url,
        sample_ok=sample_ok,
    )


def r2_preflight_payload(config: R2PapersConfig | None, *, probe: bool = True) -> dict[str, Any]:
    """Build the control-panel / checks payload for the papers channel."""

    if config is None:
        return {
            "configured": False,
            "bucket": None,
            "public_base_url": DEFAULT_PAPERS_ORIGIN,
            "credentials_present": False,
            "bucket_access_ok": False,
            "error": "R2 papers credentials or bucket are not configured.",
        }
    payload = {
        "configured": True,
        "bucket": config.bucket,
        "public_base_url": config.public_base_url,
        "credentials_present": True,
        "bucket_access_ok": False,
        "error": None,
    }
    if not probe:
        return payload
    try:
        client = R2S3Client(config)
        try:
            payload["bucket_access_ok"] = client.head_bucket()
        finally:
            client.close()
        if not payload["bucket_access_ok"]:
            payload["error"] = "R2 papers bucket probe failed."
    except Exception as exc:  # noqa: BLE001 - surface any probe failure
        payload["error"] = str(exc)
    return payload
