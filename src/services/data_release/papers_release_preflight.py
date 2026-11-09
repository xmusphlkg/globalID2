"""Read-only preparation for a future protected Papers/Pages cutover.

This module neither grants exclusive publication rights nor deploys anything.
A bucket lock can be changed by an administrator after it is read; callers must
also enforce configuration ownership and a cross-publisher cutover fence.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

import httpx

from .papers_immutable import (
    PREFIX, ImmutablePapersCandidate, ImmutablePapersError, bind_pages_release,
    prepare_immutable_papers,
)
from .release_binding import PAPERS_FIELDS, papers_binding_matches


def require_indefinite_bucket_lock(document: Any) -> None:
    """Require an enabled indefinite lock covering every generation key."""
    if not isinstance(document, dict) or document.get("success") is not True:
        raise ImmutablePapersError("immutable_papers_lock_lookup_failed")
    result = document.get("result")
    rules = result.get("rules") if isinstance(result, dict) else None
    if not isinstance(rules, list):
        raise ImmutablePapersError("immutable_papers_lock_lookup_failed")
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("enabled") is not True:
            continue
        prefix = rule.get("prefix", "")
        condition = rule.get("condition")
        if (isinstance(prefix, str) and PREFIX.startswith(prefix)
                and isinstance(condition, dict) and condition.get("type") == "Indefinite"):
            return
    raise ImmutablePapersError("immutable_papers_indefinite_lock_required")


def inspect_bucket_lock(*, account_id: str, bucket: str, token: str,
                        jurisdiction: str = "default", transport: httpx.BaseTransport | None = None) -> None:
    """Read the Cloudflare lock configuration; never change it."""
    if (not re.fullmatch(r"[a-fA-F0-9]{32}", account_id or "")
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,62}[a-z0-9]", bucket or "")
            or not token or jurisdiction not in {"default", "eu", "us", "fedramp", "fedramp-high"}):
        raise ImmutablePapersError("immutable_papers_lock_configuration_invalid")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/lock"
    try:
        with httpx.Client(transport=transport, timeout=20, follow_redirects=False) as client:
            with client.stream("GET", url, headers={"Authorization": f"Bearer {token}",
                                                    "cf-r2-jurisdiction": jurisdiction}) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1024 * 1024:
                        raise ValueError("lock response too large")
        document = json.loads(raw)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ImmutablePapersError("immutable_papers_lock_lookup_failed") from exc
    require_indefinite_bucket_lock(document)


def prepare_bound_release(*, papers_dir: Path, manifest: Mapping[str, Any],
                          papers_origin: str, pages_identity: Mapping[str, Any]
                          ) -> tuple[ImmutablePapersCandidate, dict[str, Any]]:
    """Freeze both identities before writing release.json or any ready receipt."""
    candidate = prepare_immutable_papers(papers_dir=papers_dir, manifest=manifest,
                                         papers_origin=papers_origin)
    binding = {"papers_origin": candidate.origin,
               "papers_generation_sha256": candidate.generation,
               "papers_manifest_url": candidate.manifest_url,
               "papers_manifest_sha256": hashlib.sha256(candidate.manifest).hexdigest()}
    if (any(key in pages_identity for key in PAPERS_FIELDS)
            and not papers_binding_matches(pages_identity, binding)):
        raise ImmutablePapersError("immutable_pages_existing_binding_mismatch")
    return candidate, bind_pages_release(pages_identity, binding)


def verify_bound_candidate(*, papers_dir: Path, papers_origin: str,
                           dist: Path, identity: Mapping[str, Any]) -> ImmutablePapersCandidate:
    """Recompute a retained candidate before a future recovery or deployment."""
    from .pipeline import _built_site_digest

    try:
        split = json.loads((papers_dir / "papers-manifest.json").read_bytes())
        release = json.loads((dist / "release.json").read_bytes())
        if not isinstance(split, dict) or not isinstance(release, dict):
            raise ValueError("invalid manifests")
        candidate, computed = prepare_bound_release(papers_dir=papers_dir, manifest=split,
                                                    papers_origin=papers_origin, pages_identity=identity)
        if (not all(identity.get(key) == release.get(key) and isinstance(identity.get(key), str)
                    and identity[key] for key in ("release_id", "build_digest", "source_commit", "deployment_branch"))
                or not papers_binding_matches(identity, computed)
                or not papers_binding_matches(computed, release)
                or _built_site_digest(dist) != identity["build_digest"]):
            raise ValueError("candidate identity mismatch")
        return candidate
    except (OSError, ValueError, TypeError, KeyError, ImmutablePapersError) as exc:
        raise ImmutablePapersError("immutable_pages_candidate_invalid") from exc
