"""Bind data and Papers metadata to the same publication generation."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from src.core import get_logger
from .papers_content import PapersContentCandidate
from .surveillance_publish import validate_manifest

logger = get_logger(__name__)


def bind_literature_candidate(manifest: dict[str, Any], papers: PapersContentCandidate,
                              *, expected_papers_origin: str) -> dict[str, Any]:
    """Bind version metadata without scanning every paper or reading page bytes."""
    validate_manifest(json.dumps(manifest).encode())
    if not isinstance(papers, PapersContentCandidate) or papers.origin != expected_papers_origin:
        raise ValueError("Invalid Papers publication origin")
    if not papers.release_id or papers.release_id != manifest.get("release_id"):
        raise ValueError("Papers and literature release identities differ")
    binding = {"papers_origin": papers.origin, "papers_generation_sha256": papers.generation,
               "papers_manifest_url": papers.origin + "/" + papers.manifest_key,
               "papers_manifest_sha256": hashlib.sha256(papers.manifest).hexdigest(),
               "papers_file_count": len(papers.entries)}
    if "papers" in manifest and manifest["papers"] != binding:
        raise ValueError("Literature candidate already has a different Papers binding")
    logger.info("Research publication bound: release={} data_files={} paper_files={} generation={}",
                papers.release_id, len(manifest["files"]), len(papers.entries), papers.generation)
    return {**manifest, "papers": binding}
