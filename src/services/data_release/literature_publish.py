"""Publish one prepared literature generation; orchestration owns acknowledgement."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.generation.literature_publication import _atomic_write
from .literature_binding import bind_literature_candidate
from .papers_content import PapersContentCandidate, candidate_upload_sizes, publish_papers_content
from .papers_r2 import R2PapersConfig, R2S3Client, R2S3Error
from .papers_reuse import read_committed_papers_receipt, write_committed_papers_receipt
from .r2_single_version import managed_publication
from .surveillance_publish import (
    _starting_baseline, _target, publication_upload_sizes, publish_surveillance_data, validate_manifest,
)

PREFIX = "site-data/research-publication"


def _record_authority_commit(run: Path, bound: dict, body: bytes, *, verification_required: bool) -> None:
    path = run / "authority-committed.json"
    manifest_sha256 = hashlib.sha256(body).hexdigest()
    if path.is_file():
        existing = json.loads(path.read_bytes())
        if existing.get("release_id") != bound["release_id"] or existing.get("manifest_sha256") != manifest_sha256:
            raise ValueError("Research authority receipt belongs to another candidate")
        return
    _atomic_write(path, json.dumps({
        "schema": 1, "release_id": bound["release_id"], "authoritative_committed": True,
        "generation": bound["papers"]["papers_generation_sha256"],
        "manifest_sha256": manifest_sha256,
        "public_verification_required": verification_required,
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }, sort_keys=True, separators=(",", ":")).encode())


def publish_literature_candidate(*, run: Path, objects_dir: Path, papers: PapersContentCandidate,
                                 config: R2PapersConfig, receipt: Path, client: Any = None,
                                 workers: int = 16, build: dict[str, Any] | None = None,
                                 known_staged_objects: set[str] | None = None,
                                 max_total_bytes: int | None = None) -> dict[str, Any]:
    """Upload pages first and atomically switch the single research pointer.

    The caller captures a baseline before exporting data, freezes a private
    candidate and acknowledges its dirty IDs only after this function returns.
    Interrupted writes leave immutable objects and are safe to retry.
    """
    if build is not None and any(entry.get('paper_template', {}).get('schema') == 3 for entry in papers.entries):
        raise ValueError('Projected Papers cannot use legacy HTML build receipts')
    run = Path(run)
    captured, previous = _starting_baseline(run, config, PREFIX)
    if not captured:
        raise ValueError("Literature publication requires a baseline captured before export")
    manifest, _ = validate_manifest((run / "current.json").read_bytes())
    bound = bind_literature_candidate(manifest, papers, expected_papers_origin=config.public_base_url)
    body = json.dumps(bound, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    verification_required = (any(entry.get("paper_template", {}).get("schema") == 3 for entry in papers.entries)
        or any(item.get('key', '').startswith('paper-template-v3/templates/') for item in papers.template_objects))
    active = client or R2S3Client(config, max_total_bytes=max_total_bytes)
    result = None
    try:
        with managed_publication(active, channel=PREFIX) as publication:
            cleanup_before_upload = 0
            current = active.get_object(f"{PREFIX}/current.json")
            if current not in (previous, body):
                raise R2S3Error("Research publication baseline changed before upload", status=412)
            sizes = candidate_upload_sizes(papers)
            sizes.update(publication_upload_sizes(body, prefix=PREFIX, previous=previous))
            if publication is not None:
                publication.stage(body, sizes)
                # Deferred cross-channel garbage must not fill the budget and
                # prevent every subsequent publication from reaching cleanup.
                if current != body:
                    cleanup_before_upload = publication.cleanup()['deleted_objects']
            destination = _target(config, PREFIX)
            if current == body:
                # A crash after the pointer commit must not trigger thousands of
                # redundant conditional uploads when only acknowledgement remains.
                _record_authority_commit(run, bound, body, verification_required=verification_required)
                write_committed_papers_receipt(receipt, candidate=papers, pointer=bound, target=destination, build=build)
                result = {"release_id": bound["release_id"], "papers": {"generation_sha256": papers.generation,
                        "uploaded_objects": 0, "reused_objects": len(papers.object_keys)},
                        "json": {"release_id": bound["release_id"], "uploaded": 0, "pointer_changed": False}}
                if publication is not None:
                    publication.commit(body)
                    result['retention'] = publication.cleanup()
                    result['retention']['deleted_objects'] += cleanup_before_upload
                return result
            previous_manifest = validate_manifest(previous)[0] if previous is not None else None
            known = (read_committed_papers_receipt(receipt, pointer=previous_manifest, target=destination)
                     if previous_manifest and previous_manifest.get("papers") else set())
            # Migration callers may supply only objects proven by
            # verified_existing_objects(candidate, inventory), never name-only keys.
            known.update(known_staged_objects or ())
            preflight = getattr(active, "preflight_objects", None)
            if callable(preflight):
                # Papers and JSON share one bucket and one pointer commit. Check
                # their combined growth before uploading either half of a release.
                # Each JSON relative key is rooted under PREFIX, independent of the
                # local objects_dir used to read the frozen source bytes.
                preflight(sizes)
            papers_result = publish_papers_content(papers, client=active, previous_objects=known, workers=workers)
            _atomic_write(run / "current.json", body)
            json_result = publish_surveillance_data(run, config, prefix=PREFIX, client=active,
                objects_dir=objects_dir, workers=min(workers, 8), require_starting_baseline=True,
                trust_frozen_objects=True, manage_retention=False)
            _record_authority_commit(run, bound, body, verification_required=verification_required)
            write_committed_papers_receipt(receipt, candidate=papers, pointer=bound, target=destination, build=build)
            result = {"release_id": bound["release_id"], "papers": papers_result, "json": json_result}
            if publication is not None:
                publication.commit(body)
                result['retention'] = publication.cleanup()
                result['retention']['deleted_objects'] += cleanup_before_upload
            return result
    finally:
        operations = getattr(active, 'operation_counts', None)
        if callable(operations):
            counts = operations()
            _atomic_write(run / 'r2-operations.json', json.dumps(counts, sort_keys=True, separators=(',', ':')).encode())
            if result is not None:
                result['r2_operations'] = counts
        if client is None:
            active.close()
