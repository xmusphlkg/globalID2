"""Incremental R2 publisher for the papers hosting channel."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .papers_bundle import DEFAULT_PAPERS_ORIGIN, build_papers_manifest

MANIFEST_KEY = "papers-manifest.json"

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
    """Connection settings for the papers R2 bucket."""

    account_id: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    public_base_url: str = DEFAULT_PAPERS_ORIGIN
    endpoint_url: str | None = None

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
        if not (account_id and bucket and access_key_id and secret_access_key):
            return None
        return cls(
            account_id=account_id,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            public_base_url=public_base_url.rstrip("/"),
            endpoint_url=endpoint_url,
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


class R2S3Client:
    """Minimal SigV4 S3 client for Cloudflare R2 object operations."""

    def __init__(
        self,
        config: R2PapersConfig,
        *,
        opener: Callable[[Request, float | None], Any] | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.config = config
        self._opener = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
        self.timeout_seconds = timeout_seconds
        self.region = "auto"
        self.service = "s3"

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
        query: str = "",
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
        if content_type:
            headers["content-type"] = content_type
        if cache_control:
            headers["cache-control"] = cache_control
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
        return request

    def _request(
        self,
        method: str,
        key: str,
        *,
        body: bytes = b"",
        content_type: str | None = None,
        cache_control: str | None = None,
        query: str = "",
    ) -> tuple[int, bytes]:
        request = self._signed_request(
            method,
            key,
            body=body,
            content_type=content_type,
            cache_control=cache_control,
            query=query,
        )
        try:
            with self._opener(request, self.timeout_seconds) as response:
                return int(getattr(response, "status", 200)), response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise R2S3Error(
                f"R2 {method} {key!r} failed with HTTP {exc.code}: {detail}",
                status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise R2S3Error(f"R2 {method} {key!r} transport failed: {exc}") from exc

    def put_object(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        status, _payload = self._request(
            "PUT",
            key,
            body=body,
            content_type=content_type,
            cache_control=cache_control,
        )
        if status not in {200, 201}:
            raise R2S3Error(f"R2 PUT {key!r} returned HTTP {status}")

    def delete_object(self, key: str) -> None:
        status, _payload = self._request("DELETE", key)
        if status not in {200, 204}:
            raise R2S3Error(f"R2 DELETE {key!r} returned HTTP {status}")

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
        return payload

    def head_bucket(self) -> bool:
        """Return True when the bucket accepts authenticated requests."""

        try:
            status, _payload = self._request("GET", "", query="max-keys=1")
        except R2S3Error:
            return False
        return status in {200, 204}


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
    return "application/octet-stream"


def _cache_control_for(path: str) -> str:
    """Cache policy per object type; R2 custom domains ignore Pages ``_headers``.

    Keys are stable names (not content-addressed), so nothing is ``immutable``.
    Values mirror the main site's ``_headers`` rules.
    """

    lowered = path.lower()
    if lowered == MANIFEST_KEY:
        return "no-store, max-age=0"
    if lowered.endswith(".html"):
        return "public, max-age=0, must-revalidate"
    if lowered.endswith(".json"):
        return "public, max-age=300, stale-while-revalidate=86400"
    return "public, max-age=86400"


def load_remote_manifest(client: R2S3Client) -> dict[str, Any]:
    """Load the previously published papers manifest, or an empty one."""

    payload = client.get_object(MANIFEST_KEY)
    if payload is None:
        return {"schema_version": 1, "files": []}
    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise R2S3Error(f"Remote {MANIFEST_KEY} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise R2S3Error(f"Remote {MANIFEST_KEY} must be a JSON object")
    files = data.get("files")
    if files is None:
        data["files"] = []
    elif not isinstance(files, list):
        raise R2S3Error(f"Remote {MANIFEST_KEY} files must be a list")
    return data


def publish_papers_site(
    papers_dir: Path,
    config: R2PapersConfig,
    *,
    client: R2S3Client | None = None,
    verify_public: bool = True,
    public_opener: Callable[[Request, float | None], Any] | None = None,
) -> PapersPublishResult:
    """Incrementally sync ``papers_dir`` to R2 using the papers manifest."""

    if not papers_dir.is_dir():
        raise FileNotFoundError(f"Papers site directory not found: {papers_dir}")

    local_manifest_path = papers_dir / MANIFEST_KEY
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

    active = client or R2S3Client(config)
    remote_manifest = load_remote_manifest(active)
    remote_by_key = {
        str(item.get("key")): item
        for item in remote_manifest.get("files") or []
        if isinstance(item, dict) and item.get("key") is not None
    }
    local_files = [
        item for item in local_manifest.get("files") or [] if isinstance(item, dict)
    ]
    local_by_key = {str(item["key"]): item for item in local_files if "key" in item}

    if not local_by_key:
        # An empty bundle would turn the orphan sweep below into a full wipe.
        raise R2S3Error(
            "Local papers manifest is empty; refusing to sync because every "
            "remote object would be deleted."
        )

    uploaded = 0
    unchanged = 0
    bytes_uploaded = 0
    for key, item in local_by_key.items():
        source_path = papers_dir / str(item["path"])
        remote = remote_by_key.get(key)
        if (
            remote
            and int(remote.get("bytes") or -1) == int(item.get("bytes") or -2)
            and str(remote.get("sha256") or "") == str(item.get("sha256") or "")
        ):
            unchanged += 1
            continue
        body = source_path.read_bytes()
        active.put_object(
            key,
            body,
            content_type=_content_type_for(str(item["path"])),
            cache_control=_cache_control_for(str(item["path"])),
        )
        uploaded += 1
        bytes_uploaded += len(body)

    removed = 0
    for key in sorted(set(remote_by_key) - set(local_by_key)):
        if key == MANIFEST_KEY:
            continue
        active.delete_object(key)
        removed += 1

    manifest_body = json.dumps(
        local_manifest, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    active.put_object(
        MANIFEST_KEY,
        manifest_body,
        content_type="application/json; charset=utf-8",
        cache_control=_cache_control_for(MANIFEST_KEY),
    )

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
            opener = public_opener or (lambda request, timeout: urlopen(request, timeout=timeout))
            request = Request(sample_url, method="HEAD")
            try:
                with opener(request, 60.0) as response:
                    sample_ok = int(getattr(response, "status", 200)) in {200, 204}
            except urllib.error.HTTPError as exc:
                # Some edges reject HEAD; fall back to a tiny GET range via GET.
                if exc.code == 405:
                    get_request = Request(sample_url, method="GET")
                    try:
                        with opener(get_request, 60.0) as response:
                            sample_ok = int(getattr(response, "status", 200)) == 200
                    except (OSError, ValueError, R2S3Error):
                        sample_ok = False
                else:
                    sample_ok = False
            except (OSError, ValueError, R2S3Error):
                sample_ok = False
            if not sample_ok:
                raise R2S3Error(
                    f"Public papers verification failed for sample URL: {sample_url}"
                )

    return PapersPublishResult(
        uploaded=uploaded,
        removed=removed,
        unchanged=unchanged,
        bytes_uploaded=bytes_uploaded,
        manifest_url=f"{config.public_base_url.rstrip('/')}/{MANIFEST_KEY}",
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
        payload["bucket_access_ok"] = client.head_bucket()
        if not payload["bucket_access_ok"]:
            payload["error"] = "R2 papers bucket probe failed."
    except Exception as exc:  # noqa: BLE001 - surface any probe failure
        payload["error"] = str(exc)
    return payload
