"""Read durable, publicly verified Research commits without changing a release."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .surveillance_publish import validate_manifest

IDENTIFIER = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.:-]{0,159}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


def _read(run: Path, name: str) -> bytes:
    path = run / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Invalid Research receipt")
    return path.read_bytes()


def verified_research_commit(root: Path, *, task_uuid: str, release_id: str,
                             created_at: datetime, completed_at: datetime,
                             papers_origin: str, site: bool = False) -> dict | None:
    """Only a completed task's matching pointer, authority and canaries count.

    The caller obtains task identity/times from a COMPLETED database row. The
    commit timestamp, rather than completion time, orders site and Literature
    publications: Pages deployment can finish after a newer Literature commit.
    """
    if (not isinstance(task_uuid, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", task_uuid)
            or not isinstance(release_id, str) or not IDENTIFIER.fullmatch(release_id)):
        return None
    if not site and release_id != f"literature-{task_uuid}":
        return None
    root = Path(root)
    run = root / "exports" / ("site-research-runs" if site else "research-runs") / task_uuid
    if site:
        run /= release_id
    try:
        if any(path.is_symlink() for path in (run, *run.parents) if path != root and root in path.parents):
            return None
        body = _read(run, "current.json")
        pointer, _ = validate_manifest(body)
        digest = hashlib.sha256(body).hexdigest()
        authority = json.loads(_read(run, "authority-committed.json"))
        published = json.loads(_read(run, "published.json"))
        papers = pointer["papers"]
        generation = papers["papers_generation_sha256"]
        verification = published["public_verification"]
        committed = datetime.fromisoformat(authority["committed_at"].replace("Z", "+00:00"))
        if committed.tzinfo is None:
            return None
        committed = committed.astimezone(timezone.utc)
        if (not created_at <= committed <= completed_at
                or pointer["release_id"] != release_id or authority.get("schema") != 1
                or authority.get("release_id") != release_id or authority.get("manifest_sha256") != digest
                or authority.get("authoritative_committed") is not True
                or authority.get("public_verification_required") is not True
                or authority.get("generation") != generation
                or not isinstance(generation, str) or not SHA256.fullmatch(generation)
                or papers.get("papers_origin") != papers_origin.rstrip("/")
                or published.get("release_id") != release_id
                or published["json"].get("release_id") != release_id
                or published["json"].get("manifest_sha256", digest) != digest
                or published["papers"].get("generation_sha256") != generation
                or verification.get("status") != "verified"
                or verification.get("generation") != generation):
            return None
        canaries = verification["canaries"]
        if not isinstance(canaries, list) or len(canaries) != 3:
            return None
        locales = set()
        for canary in canaries:
            locale = canary["locale"]
            if locale not in {"en", "zh", "fr"} or locale in locales:
                return None
            prefix = "" if locale == "en" else locale + "/"
            canonical = re.escape(papers_origin.rstrip("/") + "/" + prefix + "research/articles/")
            if (not re.fullmatch(canonical + r"[a-z0-9][a-z0-9-]{0,239}/", canary["url"])
                    or not SHA256.fullmatch(canary["html_sha256"])):
                return None
            locales.add(locale)
        return {"task_uuid": task_uuid, "release_id": release_id, "manifest_sha256": digest,
                "papers_generation_sha256": generation, "committed_at": committed.isoformat(),
                "public_verified": True, "publication_kind": "site" if site else "literature"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
