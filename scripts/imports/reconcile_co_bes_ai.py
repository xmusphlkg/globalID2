#!/usr/bin/env python3
"""Reconcile existing BES model readings against documented original PDF reviews.

This command never calls a model or imports observations into the database.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ai.model_identity import model_identity
from src.data.crawlers.co_bes import (
    BESContractError,
    BESReport,
    inspect_pdf,
    normalize_annex_cells,
)
from src.services.ingestion.co_bes_ai import (
    CACHE_DIRECTORY,
    PROMPT_VERSION,
    _atomic_json,
    _decode,
    _numeric_fingerprint,
    _page_cells,
)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def local_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise BESContractError("Review or source path outside repository")
    return path


def reviews(paths):
    result = []
    for path in paths:
        path = local_path(path)
        if not path.exists():
            continue
        payload = path.read_bytes()
        document = json.loads(payload)
        if "corrections" not in document:
            raise BESContractError(f"Review lacks corrections schema: {path.name}")
        for correction in document["corrections"]:
            evidence = local_path(correction["evidence_path"])
            evidence_digest = digest(evidence.read_bytes())
            if evidence_digest != correction.get("evidence_sha256"):
                raise BESContractError("Original PDF visual evidence SHA-256 mismatch")
            result.append({**correction, "review_path": str(path.relative_to(ROOT)),
                           "review_sha256": digest(payload)})
    return result


def apply_reviews(cells, corrections):
    for correction in corrections:
        field = correction["field"]
        if field not in {"accumulated_value_raw", "case_definition_raw", "event_label_raw"}:
            raise BESContractError("Unsupported visual review correction field")
        matches = [cell for cell in cells if
                   cell["event_group_position_1_based"] == correction["event_group_position_1_based"]
                   and ("geography_label_raw" not in correction or
                        cell["geography_label_raw"] == correction["geography_label_raw"])]
        expected = 1 if "geography_label_raw" in correction else 39
        if len(matches) != expected or not isinstance(correction["value"], str):
            raise BESContractError("Visual review does not uniquely identify source cells")
        for cell in matches:
            cell[field] = correction["value"]


def request_attachments(parents):
    attachments = {}
    for parent in parents:
        directory = local_path(Path("data/raw/ai") / parent)
        for path in directory.glob("*.json"):
            payload = path.read_bytes()
            if digest(payload) != path.stem:
                raise BESContractError("Durable model request SHA-256 mismatch")
            for attachment in json.loads(payload).get("attachments", []):
                if attachment.get("data"):
                    pdf = base64.b64decode(attachment["data"], validate=True)
                    attachments[digest(pdf)] = (pdf, str(path.relative_to(ROOT)))
    return attachments


def rendered_page_digest(payload, page):
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(payload) as document:
        source = document[page - 1]
        try:
            bitmap = source.render(scale=1)
            try:
                image = bitmap.to_pil()
                return digest(str((image.mode, image.size)).encode() + image.tobytes())
            finally:
                bitmap.close()
        finally:
            source.close()


def verify_reexported_pages(originals, attachments, payload, page):
    expected = rendered_page_digest(payload, page)
    evidence = []
    for original in originals:
        attachment_digest = original["page_sha256"]
        if attachment_digest not in attachments:
            raise BESContractError("Original model attachment not found in requested durable parents")
        attachment, request_path = attachments[attachment_digest]
        if rendered_page_digest(attachment, 1) != expected:
            raise BESContractError("Model attachment pixels differ from the original PDF page")
        evidence.append({"request_path": request_path, "attachment_sha256": attachment_digest,
                         "source_page_render_sha256": expected, "render_scale": 1})
    return evidence


def run(args):
    args.plan = local_path(args.plan)
    args.cache_directory = local_path(args.cache_directory)
    args.output = local_path(args.output)
    plan = json.loads(args.plan.read_bytes())
    corrections = reviews(args.review)
    attachments = request_attachments(args.attachment_parent)
    state = {"generated_at": datetime.now(timezone.utc).isoformat(),
             "plan": str(args.plan), "database_imported": False,
             "model_calls": 0, "completed": [], "pending": []}
    for entry in plan["reports"]:
        if args.full_reports_only and entry.get("partial_report"):
            continue
        source_digest = entry["pdf_sha256"]
        try:
            payload = local_path(entry["pdf_path"]).read_bytes()
            if digest(payload) != source_digest:
                raise BESContractError("Original PDF SHA-256 differs from frozen plan")
            report = BESReport(**entry["report"])
            cover = inspect_pdf(payload)
            if cover.year != report.listed_year or cover.period_end is None:
                raise BESContractError("Original PDF does not establish source period")
        except Exception as exc:  # noqa: BLE001 - persist source/PDF failures at this CLI boundary.
            for page in entry["pages"]:
                state["pending"].append({"pdf_sha256": source_digest, "page": page,
                    "report": entry["report"]["title"], "reason": f"cover: {type(exc).__name__}: {exc}"})
            continue
        for page in entry["pages"]:
            item = {"pdf_sha256": source_digest, "page": page,
                    "report": entry["report"]["title"]}
            try:
                originals, transcripts, identities = [], [], set()
                page_corrections = [c for c in corrections if
                    c["pdf_sha256"] == source_digest and c["page"] == page]
                for attempt in (1, 2):
                    path = args.cache_directory / "responses" / f"{source_digest}-{PROMPT_VERSION}-p{page}-{attempt}.json"
                    response_payload = path.read_bytes()
                    original = json.loads(response_payload)
                    identity = model_identity(original.get("model"))
                    if (original.get("input_mode") != "native_file" or
                            original.get("page") != page or identity in identities or
                            identity in {"", "unknown"} or
                            not re.fullmatch(r"[0-9a-f]{64}", str(original.get("page_sha256", "")))):
                        raise BESContractError("Original responses lack distinct native PDF readers")
                    identities.add(identity)
                    cells = _page_cells(_decode(original["response"]), page=page,
                                        digest=source_digest, cover=cover)
                    if not cells:
                        raise BESContractError("Original reading omitted expected fixed annex")
                    apply_reviews(cells, page_corrections)
                    normalized, audit = normalize_annex_cells(cells, report, cover)
                    if len(normalized) != len(cells):
                        raise BESContractError("Canonical normalization omitted source cells")
                    transcripts.append(cells)
                    originals.append({**original, "response_path": str(path.relative_to(ROOT)),
                                      "response_sha256": digest(response_payload)})
                attachment_evidence = []
                if originals[0]["page_sha256"] != originals[1]["page_sha256"]:
                    attachment_evidence = verify_reexported_pages(originals, attachments, payload, page)
                if _numeric_fingerprint(transcripts[0]) != _numeric_fingerprint(transcripts[1]):
                    raise BESContractError("Remaining independent transcription disagreement")
                result = {"pdf_sha256": source_digest, "page": page, "validated": True,
                          "source_epi_year": cover.year, "source_epi_week": cover.week,
                          "cells": transcripts[0], "provenance": originals,
                          "visual_review": page_corrections, "audit": audit,
                          "source_attachment_verification": attachment_evidence,
                          "validation": "independent_models_with_original_visual_resolution"
                          if page_corrections else "independent_page_transcription_agreement"}
                target = args.cache_directory / "pages" / f"{source_digest}-{PROMPT_VERSION}-p{page}.json"
                _atomic_json(target, result)
                state["completed"].append({**item, "cells": len(transcripts[0]),
                    "source_unreadable_cells": audit.get("source_unreadable_cells", 0),
                    "visual_corrections": len(page_corrections), "cache_path": str(target)})
            except Exception as exc:  # noqa: BLE001 - persist each page failure and reconcile remaining pages.
                state["pending"].append({**item, "reason": f"{type(exc).__name__}: {exc}"})
        _atomic_json(args.output, state)
    state.update(completed_pages=len(state["completed"]), pending_pages=len(state["pending"]))
    _atomic_json(args.output, state)
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=ROOT / "data/current/co/bes-ai-anomaly-plan.json")
    parser.add_argument("--cache-directory", type=Path, default=ROOT / CACHE_DIRECTORY)
    parser.add_argument("--review", action="append", default=[
        "data/current/co/bes-ai-visual-review.json", "data/current/co/bes-ai-visual-w5.json",
        "data/current/co/bes-ai-visual-resume-w1-w2.json",
        "data/current/co/bes-ai-visual-resume-w3-w4.json"])
    parser.add_argument("--output", type=Path, default=ROOT / "data/current/co/bes-ai-reconcile-state.json")
    parser.add_argument("--full-reports-only", action="store_true")
    parser.add_argument("--attachment-parent", action="append", default=[])
    raise SystemExit(run(parser.parse_args()))
