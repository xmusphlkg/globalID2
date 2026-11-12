"""Publish one prepared literature generation; orchestration owns acknowledgement."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.generation.literature_publication import _atomic_write
from .literature_binding import bind_literature_candidate
from .papers_content import PapersContentCandidate, publish_papers_content
from .papers_r2 import R2PapersConfig, R2S3Client, R2S3Error
from .papers_reuse import read_committed_papers_receipt, write_committed_papers_receipt
from .surveillance_publish import _starting_baseline, _target, publish_surveillance_data, validate_manifest

PREFIX = "site-data/research-publication"


def publish_literature_candidate(*, run: Path, objects_dir: Path, papers: PapersContentCandidate,
                                 config: R2PapersConfig, receipt: Path, client: Any = None,
                                 workers: int = 16, build: dict[str, Any] | None = None) -> dict[str, Any]:
    """Upload pages first and atomically switch the single research pointer.

    The caller captures a baseline before exporting data, freezes a private
    candidate and acknowledges its dirty IDs only after this function returns.
    Interrupted writes leave immutable objects and are safe to retry.
    """
    run = Path(run)
    captured, previous = _starting_baseline(run, config, PREFIX)
    if not captured:
        raise ValueError("Literature publication requires a baseline captured before export")
    manifest, _ = validate_manifest((run / "current.json").read_bytes())
    bound = bind_literature_candidate(manifest, papers, expected_papers_origin=config.public_base_url)
    body = json.dumps(bound, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    active = client or R2S3Client(config)
    try:
        current = active.get_object(f"{PREFIX}/current.json")
        if current not in (previous, body):
            raise R2S3Error("Research publication baseline changed before upload", status=412)
        destination = _target(config, PREFIX)
        if current == body:
            # A crash after the pointer commit must not trigger thousands of
            # redundant conditional uploads when only acknowledgement remains.
            write_committed_papers_receipt(receipt, candidate=papers, pointer=bound, target=destination, build=build)
            return {"release_id": bound["release_id"], "papers": {"generation_sha256": papers.generation,
                    "uploaded_objects": 0, "reused_objects": len({item["key"] for item in papers.entries})},
                    "json": {"release_id": bound["release_id"], "uploaded": 0, "pointer_changed": False}}
        previous_manifest = validate_manifest(previous)[0] if previous is not None else None
        known = (read_committed_papers_receipt(receipt, pointer=previous_manifest, target=destination)
                 if previous_manifest and previous_manifest.get("papers") else set())
        papers_result = publish_papers_content(papers, client=active, previous_objects=known, workers=workers)
        _atomic_write(run / "current.json", body)
        json_result = publish_surveillance_data(run, config, prefix=PREFIX, client=active,
            objects_dir=objects_dir, workers=min(workers, 8), require_starting_baseline=True,
            trust_frozen_objects=True)
        write_committed_papers_receipt(receipt, candidate=papers, pointer=bound, target=destination, build=build)
        return {"release_id": bound["release_id"], "papers": papers_result, "json": json_result}
    finally:
        if client is None:
            active.close()
