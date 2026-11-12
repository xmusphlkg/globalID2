"""Local upload receipts for an already committed research generation.

A missing receipt only loses the optimization: create-once uploads safely
resume. The caller must pass the pointer captured from the destination, never
an uncommitted candidate, and write a receipt only after the pointer commits.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from src.core import get_logger
from .papers_content import PapersContentCandidate

logger = get_logger(__name__)


def _identity(pointer: dict[str, Any], target: str) -> dict[str, str]:
    papers = pointer.get("papers")
    if not isinstance(papers, dict) or not isinstance(target, str) or not target:
        raise ValueError("A committed research binding and destination are required")
    fields = {"target": target, "release_id": pointer.get("release_id"),
              "generation": papers.get("papers_generation_sha256"), "origin": papers.get("papers_origin")}
    if any(not isinstance(value, str) or not value for value in fields.values()):
        raise ValueError("Invalid committed research binding")
    return fields


def load_committed_papers_receipt(path: Path, *, pointer: dict[str, Any] | None,
                                  target: str) -> dict[str, Any] | None:
    if pointer is None:
        return None
    identity = _identity(pointer, target)
    try:
        receipt = json.loads(Path(path).read_bytes())
        if (not isinstance(receipt, dict) or receipt.get("schema") != 1
                or receipt.get("identity") != identity or not isinstance(receipt.get("keys"), list)
                or any(not isinstance(key, str) for key in receipt["keys"])):
            raise ValueError("Receipt does not match the committed pointer")
    except (FileNotFoundError, OSError, ValueError, TypeError):
        logger.info("Research papers reuse receipt unavailable; conditional uploads will resume safely")
        return None
    logger.info("Research papers reuse receipt loaded: generation={} objects={}", identity["generation"], len(receipt["keys"]))
    return receipt


def read_committed_papers_receipt(path: Path, *, pointer: dict[str, Any] | None,
                                  target: str) -> set[str]:
    receipt = load_committed_papers_receipt(path, pointer=pointer, target=target)
    return set(receipt["keys"]) if receipt else set()


def write_committed_papers_receipt(path: Path, *, candidate: PapersContentCandidate,
                                   pointer: dict[str, Any], target: str,
                                   build: dict[str, Any] | None = None) -> None:
    identity = _identity(pointer, target)
    if (identity["release_id"] != candidate.release_id or identity["generation"] != candidate.generation
            or identity["origin"] != candidate.origin):
        raise ValueError("Papers candidate is not the committed research generation")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".papers-reuse-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            receipt = {"schema": 1, "identity": identity,
                       "keys": sorted(candidate.object_keys)}
            if build is not None:
                receipt["build"] = build
                receipt["files"] = [{"path": item["path"], "sha256": item["sha256"], "bytes": item["bytes"]}
                                    for item in candidate.entries]
            json.dump(receipt, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
