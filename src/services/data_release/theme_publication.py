"""Frozen theme candidates executed by the existing export-data task queue."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .papers_r2 import R2PapersConfig, R2S3Client, R2S3Error
from .surveillance_publish import _atomic_receipt, _publication_lock, _target

ENTRY = "theme/compat/v1/theme.css"
IMPORT = re.compile(rb'@import url\("../../versions/sha256/([a-f0-9]{64})\.css"\);\n\Z')


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def configuration(service=None, *, frozen: dict | None = None) -> tuple[R2PapersConfig | None, str | None]:
    if service is None:
        from src.services.data_release_service import data_release_service
        service = data_release_service
    release = service._config()
    if frozen is not None:
        # An admitted candidate keeps its reviewed destination even after an
        # operator pauses new publications or changes the default destination.
        source = service._r2_papers_config()
        if source is None:
            return None, "theme_r2_credentials_unavailable"
        return R2PapersConfig(account_id=frozen["account_id"], bucket=frozen["bucket"],
            access_key_id=source.access_key_id, secret_access_key=source.secret_access_key,
            endpoint_url=frozen["endpoint_url"], public_base_url=frozen["public_base_url"], manifest_key=ENTRY), None
    if not getattr(release, "theme_publish_enabled", False):
        return None, "theme_publication_disabled"
    base = str(getattr(release, "theme_public_base_url", "")).strip().rstrip("/")
    bucket = str(getattr(release, "theme_r2_bucket", "")).strip()
    parsed = urlsplit(base)
    if (not bucket or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or parsed.hostname.endswith(".r2.dev")):
        return None, "theme_public_destination_unconfigured"
    if (not getattr(release, "theme_compat_v1_ready", False)
            or str(getattr(release, "theme_stylesheet_url", "")).strip() != f"{base}/{ENTRY}"):
        return None, "theme_compatible_templates_not_verified"
    if bucket in {getattr(release, "r2_papers_bucket", None), getattr(release, "r2_reports_bucket", None)}:
        return None, "theme_requires_dedicated_public_bucket"
    source = service._r2_papers_config()
    if source is None:
        return None, "theme_r2_credentials_unavailable"
    return R2PapersConfig(account_id=source.account_id, bucket=bucket, access_key_id=source.access_key_id,
                         secret_access_key=source.secret_access_key, endpoint_url=source.endpoint_url,
                         public_base_url=base, manifest_key=ENTRY), None


def frozen_destination(config: R2PapersConfig) -> dict:
    return {"account_id": config.account_id, "bucket": config.bucket, "endpoint_url": config.endpoint_url,
            "public_base_url": config.public_base_url}


def destination(config: R2PapersConfig) -> str:
    return sha(f"{config.s3_endpoint}/{config.bucket}/{config.public_base_url}/{ENTRY}".encode())


def public_read(url: str) -> bytes:
    with urlopen(Request(url, headers={"Cache-Control": "no-cache"}), timeout=15) as response:
        if response.geturl() != url:
            raise ValueError("theme_public_redirect_not_allowed")
        if str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower() != "text/css":
            raise ValueError("theme_public_content_type_invalid")
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("theme_public_object_oversized")
        return body


def verify_public(config: R2PapersConfig, key: str, expected: bytes, *, read=public_read, attempts=6) -> None:
    for attempt in range(attempts):
        try:
            if read(f"{config.public_base_url}/{key}") == expected:
                return
        except (OSError, ValueError):
            pass
        if attempt + 1 < attempts:
            time.sleep(min(2 ** attempt, 16))
    raise RuntimeError("theme_public_verification_pending")


def prepare(*, root: Path, directory: Path, config: R2PapersConfig, client=None,
            rollback_directory: Path | None = None) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    builder_digest = sha((root / "astro-site/scripts/build-theme.mjs").read_bytes())
    source_digest = sha((root / "shared/site-theme-v1.json").read_bytes()) if rollback_directory is None else None
    if rollback_directory is None:
        process = subprocess.run(["node", str(root / "astro-site/scripts/build-theme.mjs"),
                                  str(root / "shared/site-theme-v1.json"), str(directory)],
                                 cwd=root, capture_output=True, timeout=60)
        if process.returncode:
            raise ValueError("theme_candidate_compilation_failed")
    else:
        old = validate_candidate(rollback_directory)
        for entry in old["objects"]:
            path = directory / entry["key"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((rollback_directory / entry["key"]).read_bytes())
        _atomic_receipt(directory / "theme-candidate.json", old)
    manifest = validate_candidate(directory)
    if (builder_digest != sha((root / "astro-site/scripts/build-theme.mjs").read_bytes())
            or source_digest is not None and source_digest != sha((root / "shared/site-theme-v1.json").read_bytes())):
        raise ValueError("theme_source_changed_during_preparation")
    active = client or R2S3Client(config)
    try:
        previous = active.get_object(ENTRY)
        if previous is not None and not IMPORT.fullmatch(previous):
            raise ValueError("theme_existing_pointer_incompatible")
    finally:
        if client is None:
            active.close()
    receipt = {"schema": 1, "destination": destination(config),
               "baseline_sha256": sha(previous) if previous is not None else None,
               "previous_b64": base64.b64encode(previous).decode() if previous is not None else None,
               "content_sha256": manifest["content_sha256"],
               "builder_sha256": builder_digest, "source_sha256": source_digest}
    _atomic_receipt(directory / "theme-prepared.json", receipt)
    return receipt


def validate_candidate(directory: Path) -> dict:
    manifest = json.loads((directory / "theme-candidate.json").read_bytes())
    digest = manifest.get("content_sha256")
    if (manifest.get("schema_version") != 1 or manifest.get("compatibility") != "v1"
            or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
        raise ValueError("theme_candidate_invalid")
    expected = [f"theme/versions/sha256/{digest}.css", ENTRY]
    if not isinstance(manifest.get("objects"), list) or [item.get("key") for item in manifest["objects"]] != expected:
        raise ValueError("theme_candidate_objects_invalid")
    for item in manifest["objects"]:
        path = directory / item["key"]
        if path.is_symlink():
            raise ValueError("theme_candidate_symlink")
        body = path.read_bytes()
        if len(body) != item["bytes"] or sha(body) != item["sha256"]:
            raise ValueError("theme_candidate_checksum_mismatch")
    if (directory / ENTRY).read_bytes() != f'@import url("../../versions/sha256/{digest}.css");\n'.encode():
        raise ValueError("theme_candidate_pointer_invalid")
    return manifest


def publish(*, directory: Path, config: R2PapersConfig, plan_id: str, client=None, read=public_read) -> dict:
    manifest = validate_candidate(directory)
    prepared = json.loads((directory / "theme-prepared.json").read_bytes())
    if prepared["destination"] != destination(config) or prepared["content_sha256"] != manifest["content_sha256"]:
        raise ValueError("theme_destination_or_candidate_changed")
    previous = base64.b64decode(prepared["previous_b64"], validate=True) if prepared["previous_b64"] is not None else None
    if (sha(previous) if previous is not None else None) != prepared["baseline_sha256"]:
        raise ValueError("theme_baseline_corrupt")
    active = client or R2S3Client(config)
    started = time.monotonic()
    receipt_path = directory / "theme-receipt.json"
    receipt = {"schema": 1, "plan_id": plan_id, "version": manifest["content_sha256"], "compatibility": "v1",
               "content_render_count": 0, "html_assembly_count": 0, "template_compile_count": 0,
               "theme_compile_count": 1, "pages_deployment_required": False,
               "uploaded_objects": 0, "uploaded_bytes": 0, "authoritative_committed": False,
               "public_verified": False, "database_confirmed": False}
    if receipt_path.is_file():
        existing = json.loads(receipt_path.read_bytes())
        if existing.get("plan_id") != plan_id or existing.get("version") != manifest["content_sha256"]:
            raise ValueError("theme_receipt_identity_mismatch")
        receipt.update(existing)
    try:
        with _publication_lock(directory, config, ENTRY):
            entry = (directory / ENTRY).read_bytes()
            current = active.get_object(ENTRY)
            if current not in (previous, entry):
                raise R2S3Error("theme_baseline_conflict", status=412)
            immutable = manifest["objects"][0]
            body = (directory / immutable["key"]).read_bytes()
            remote = active.get_object(immutable["key"])
            if remote is None:
                try:
                    active.put_object_conditional(immutable["key"], body, previous=None,
                        content_type="text/css; charset=utf-8", cache_control="public, max-age=31536000, immutable")
                    receipt["uploaded_objects"] += 1
                    receipt["uploaded_bytes"] += len(body)
                except R2S3Error as exc:
                    if exc.status != 412 or active.get_object(immutable["key"]) != body:
                        raise
            elif remote != body:
                raise ValueError("theme_immutable_object_conflict")
            verify_public(config, immutable["key"], body, read=read)
            if current != entry:
                active.put_object_conditional(ENTRY, entry, previous=previous, content_type="text/css; charset=utf-8",
                    cache_control="public, max-age=0, s-maxage=30, must-revalidate")
                receipt["uploaded_objects"] += 1
                receipt["uploaded_bytes"] += len(entry)
            receipt.update(authoritative_committed=True, committed_at=receipt.get("committed_at") or datetime.now(timezone.utc).isoformat())
            _atomic_receipt(receipt_path, receipt)
            verify_public(config, ENTRY, entry, read=read)
            if active.get_object(ENTRY) != entry:
                raise R2S3Error("theme_superseded_during_verification", status=412)
            receipt.update(public_verified=True, public_verified_at=datetime.now(timezone.utc).isoformat(),
                           duration_seconds=time.monotonic() - started)
            _atomic_receipt(receipt_path, receipt)
            return receipt
    finally:
        if client is None:
            active.close()


async def execute(task, *, runtime, service) -> dict:
    import asyncio
    from .publication_execution import load_plan
    frozen = task.input_data["publication_plan"]
    plan = load_plan(runtime.get_config().data_dir, frozen["plan_id"], frozen["plan_digest"])
    config, reason = configuration(service, frozen=plan["_execution"]["theme_destination"])
    if config is None:
        raise ValueError(reason)
    directory = runtime.get_config().data_dir / "publication-plans" / plan["plan_id"]
    if json.loads((directory / "theme-prepared.json").read_bytes()) != plan["_execution"]["theme_prepared"]:
        raise ValueError("theme_frozen_preparation_mismatch")
    result = await asyncio.to_thread(publish, directory=directory, config=config, plan_id=plan["plan_id"])
    return {"status": "published", "publication_kind": "theme", "plan_id": plan["plan_id"], **result}
