"""Plan source-verified INS BES appendix pages for model-assisted extraction."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import ijson
import pypdfium2

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "data/raw/co/bes-history/20261116-production-backfill-v4/manifest.json"
DEFAULT_OUTPUT = ROOT / "data/current/co/bes-ai-page-plan.json"
DEFAULT_ANOMALY_OUTPUT = ROOT / "data/current/co/bes-ai-anomaly-plan.json"
DEFAULT_PARSE_CACHE = ROOT / "data/cache/co/ins_bes_parsed"
V3_PARSER_VERSION = "co-ins-bes-dashboard-parser-v3"
FILENAME = re.compile(r"(?P<year>20\d{2})_Boletin_epidemiologico_semana[ _](?P<week>\d{1,2})\.pdf$")
TITLE = re.compile(r"comportamiento de la notificacion por (?:departamento|entidad territorial)")


def _normalized(value: str) -> str:
    plain = "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                    if not unicodedata.combining(char))
    return " ".join(plain.split())


def _cached_raw_hashes(directory: Path) -> set[str]:
    hashes = set()
    for path in directory.glob("*.json.gz"):
        with gzip.open(path, "rb") as stream:
            for prefix, event, value in ijson.parse(stream):
                if prefix == "raw_sha256" and event == "string":
                    hashes.add(value)
                    break
    return hashes


def _verified_pdf_path(artifact: dict) -> Path:
    path = (ROOT / artifact["relative_path"]).resolve()
    if not path.is_relative_to((ROOT / "data/raw/co").resolve()):
        raise ValueError("BES artifact path escapes raw archive")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    if size != artifact["byte_size"] or digest.hexdigest() != artifact["sha256"]:
        raise ValueError(f"BES artifact integrity mismatch: {path.name}")
    return path


def _page_candidates(path: Path, *, tail_pages: int) -> tuple[int, str, list[dict]]:
    document = pypdfium2.PdfDocument(path)
    try:
        count = len(document)
        if count < 1:
            raise ValueError(f"BES PDF has no pages: {path.name}")
        candidates = []
        tail = range(max(0, count - tail_pages), count)
        for index in tail:
            textpage = document[index].get_textpage()
            try:
                text = _normalized(textpage.get_text_range())
            finally:
                textpage.close()
            if TITLE.search(text) and "departamento" in text and "acumulado" in text:
                candidates.append({"page_index": index, "page_number": index + 1,
                                   "selection": "title_department_cumulative_text"})
        if candidates:
            return count, "table_header_text_matched", candidates
        return count, "fallback_tail_requires_model_page_classification", [
            {"page_index": index, "page_number": index + 1,
             "selection": "unverified_tail_candidate"} for index in tail
        ]
    finally:
        document.close()


def build_plan(manifest: Path = DEFAULT_MANIFEST, *, tail_pages: int = 12,
               parse_cache_dir: Path = DEFAULT_PARSE_CACHE) -> dict:
    if not 1 <= tail_pages <= 24:
        raise ValueError("tail_pages must be between 1 and 24")
    source = json.loads(manifest.read_bytes())
    artifacts = [item for item in source["artifacts"] if item["kind"] == "cda_pdf"]
    if len(artifacts) != 97:
        raise ValueError(f"Expected 97 captured 2025/26 INS BES PDFs, found {len(artifacts)}")
    cached = _cached_raw_hashes(parse_cache_dir)
    reports = []
    seen = set()
    for item in artifacts:
        url = item["source_url"]
        filename = unquote(url.rsplit("/", 1)[-1])
        match = FILENAME.fullmatch(filename)
        listed_year = item.get("metadata_", {}).get("report", {}).get("listed_year")
        if not match or listed_year != int(match["year"]):
            raise ValueError(f"BES filename/listed year mismatch: {filename}")
        year, week = int(match["year"]), int(match["week"])
        if year not in (2025, 2026) or not 1 <= week <= 53 or (year, week) in seen:
            raise ValueError(f"BES year/week missing or duplicated: {filename}")
        seen.add((year, week))
        path = _verified_pdf_path(item)
        page_count, status, pages = _page_candidates(path, tail_pages=tail_pages)
        report = item["metadata_"]["report"]
        reports.append({
            "official_url": url, "filename": filename,
            "pdf_path": path.relative_to(ROOT).as_posix(),
            "pdf_sha256": item["sha256"], "raw_sha256": item["sha256"],
            "report": report, "pages": [page["page_number"] for page in pages],
            "official_year": listed_year,
            "filename_week": week, "page_count": page_count,
            "parser_cache_status": "cached" if item["sha256"] in cached else "uncached",
            "priority": 2 if item["sha256"] in cached else 1,
            "page_selection_status": status, "candidate_pages": pages,
        })
    reports.sort(key=lambda item: (item["priority"], item["official_year"], item["filename_week"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": manifest.resolve().relative_to(ROOT).as_posix(),
        "source_policy": "Official SharePoint year and filename week plan; AI must verify report cover and table cells.",
        "page_index_base": 0, "page_number_base": 1,
        "tail_pages_searched": tail_pages, "report_count": len(reports),
        "uncached_reports": sum(row["priority"] == 1 for row in reports),
        "candidate_page_count": sum(len(row["candidate_pages"]) for row in reports),
        "reports": reports,
    }


def build_anomaly_plan(full_plan: dict, *, parse_cache_dir: Path = DEFAULT_PARSE_CACHE) -> dict:
    """Select missing v3 reports and exact pages with unresolved v3 gaps."""
    v3 = {}
    for path in parse_cache_dir.glob("*.json.gz"):
        with gzip.open(path, "rb") as stream:
            fields = {}
            for key, value in ijson.kvitems(stream, ""):
                if key in {"audit", "parser_version", "raw_sha256"}:
                    fields[key] = value
                if key == "raw_sha256":
                    break
        if fields.get("parser_version") == V3_PARSER_VERSION:
            sha = fields["raw_sha256"]
            if sha in v3:
                raise ValueError(f"Duplicate v3 parse cache for PDF {sha}")
            v3[sha] = fields["audit"]
    selected = []
    for report in full_plan["reports"]:
        sha = report["pdf_sha256"]
        audit = v3.get(sha)
        if audit is None:
            selected.append({**report, "priority": 1, "partial_report": False,
                             "anomaly_reason": "missing_v3_cache", "v3_gaps": []})
            continue
        gaps = audit.get("gaps") or []
        if not gaps:
            continue
        pages = sorted({int(gap["page"]) for gap in gaps})
        if not set(pages) <= set(report["pages"]):
            raise ValueError(f"v3 gap page outside verified appendix: {report['filename']}")
        selected.append({**report, "priority": 2, "partial_report": True,
                         "anomaly_reason": "v3_cache_gaps", "v3_gaps": gaps,
                         "pages": pages,
                         "candidate_pages": [page for page in report["candidate_pages"]
                                             if page["page_number"] in pages]})
    selected.sort(key=lambda row: (row["priority"], row["official_year"], row["filename_week"]))
    planned_hashes = {report["pdf_sha256"] for report in full_plan["reports"]}
    if not v3 or not v3.keys() <= planned_hashes:
        raise ValueError("v3 parse cache does not match the captured report plan")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_full_plan": "data/current/co/bes-ai-page-plan.json",
        "required_parser_version": V3_PARSER_VERSION,
        "report_count": len(selected),
        "uncached_reports": sum(not row["partial_report"] for row in selected),
        "partial_reports": sum(row["partial_report"] for row in selected),
        "candidate_page_count": sum(len(row["pages"]) for row in selected),
        "reports": selected,
    }


def _write_json_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".bes-ai-pages-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--parse-cache-dir", type=Path, default=DEFAULT_PARSE_CACHE)
    parser.add_argument("--tail-pages", type=int, default=12)
    parser.add_argument("--anomaly-only", action="store_true")
    parser.add_argument("--anomaly-output", type=Path, default=DEFAULT_ANOMALY_OUTPUT)
    args = parser.parse_args()
    if args.anomaly_only:
        plan = build_anomaly_plan(json.loads(args.output.read_bytes()),
                                  parse_cache_dir=args.parse_cache_dir)
        _write_json_atomic(args.anomaly_output, plan)
        print(json.dumps({key: plan[key] for key in
                          ("report_count", "uncached_reports", "partial_reports",
                           "candidate_page_count")}, sort_keys=True))
        return
    plan = build_plan(args.manifest, tail_pages=args.tail_pages,
                      parse_cache_dir=args.parse_cache_dir)
    _write_json_atomic(args.output, plan)
    print(json.dumps({key: plan[key] for key in
                      ("report_count", "uncached_reports", "candidate_page_count")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
