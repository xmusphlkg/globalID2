#!/usr/bin/env python3
"""Archive original INS Sivigila microdata workbooks; no database writes."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urljoin
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.crawlers.co_ins_transport import ColombiaINSOfficialTransport

BASE = "https://portalsivigila.ins.gov.co"
PAGE = BASE + "/Paginas/Buscador.aspx"
CATALOGUE = BASE + "/_api/web/lists/GetByTitle('Microdatos')/items?$select=*,FileRef,File/Length&$expand=File&$top=3000"
DEFAULT_OUTPUT = ROOT / "data/raw/co/sivigila-microdatos"


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_workbook(path):
    """Reject login/error HTML, incomplete ZIPs and damaged Excel members."""
    with zipfile.ZipFile(path) as book:
        names = set(book.namelist())
        if not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
            raise ValueError("Response is not an XLSX workbook")
        bad_member = book.testzip()
        if bad_member:
            raise ValueError("Workbook ZIP checksum failure")


def item_identity(item):
    year = str(item["A_x00f1_o"])
    code = str(item["CodigoEvento"])
    if not re.fullmatch(r"20\d{2}", year) or not re.fullmatch(r"\d+", code):
        raise ValueError("Invalid year or event code in official catalogue")
    ref = item["FileRef"]
    if not re.fullmatch(r"/Microdatos/[^/]+\.xlsx", ref, flags=re.I):
        raise ValueError("Unexpected official workbook path")
    return year, code, urljoin(BASE, ref)


def download(item, output, previous=None):
    year, code, url = item_identity(item)
    expected = int(item["File"]["Length"])
    if not 0 < expected <= 1024 * 1024 * 1024:
        raise ValueError("Unexpected catalogue file size")
    if previous and previous.get("source_modified") == item["Modified"]:
        path = output / previous["path"]
        if (path.is_file() and path.stat().st_size == expected
                and sha256_file(path) == previous["sha256"]):
            return previous
    directory = output / "files" / year / code
    directory.mkdir(parents=True, exist_ok=True)
    transport = ColombiaINSOfficialTransport(timeout=180)
    temporary = None
    try:
        response = transport.get(url, stream=True, headers={"Accept-Encoding": "identity"})
        try:
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    size += len(chunk)
                    if size > expected:
                        raise ValueError("Workbook exceeds catalogue byte size")
                    digest.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if size != expected:
                raise ValueError("Workbook does not match catalogue byte size")
            declared = response.headers.get("Content-Length")
            if declared and not response.headers.get("Content-Encoding") and int(declared) != size:
                raise ValueError("Workbook does not match HTTP byte size")
            validate_workbook(temporary)
            checksum = digest.hexdigest()
            path = directory / (checksum + ".xlsx")
            temporary.replace(path)
            return {"year": int(year), "event_code": code,
                    "event_name": item.get("NombreEvento"), "catalogue_id": item["Id"],
                    "source_url": url, "final_url": response.url,
                    "source_modified": item["Modified"], "captured_at": now(),
                    "path": path.relative_to(output).as_posix(),
                    "byte_size": size, "sha256": checksum, "validation": "xlsx_zip_crc_ok"}
        finally:
            response.close()
    finally:
        transport.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_with_retries(item, output, previous):
    for attempt in range(3):
        try:
            return download(item, output, previous)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def capture_catalogue(output):
    snapshot = output / "snapshots" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot.mkdir(parents=True)
    evidence, items = [], []
    transport = ColombiaINSOfficialTransport(timeout=180)
    try:
        url, visited = PAGE, set()
        while url:
            if url in visited:
                raise ValueError("Official catalogue pagination loop")
            visited.add(url)
            response = transport.get(url, headers={"Accept": "application/json;odata=verbose"} if url != PAGE else {})
            try:
                payload = response.content
                name = "buscador.html" if url == PAGE else f"catalogue-{len(evidence):04d}.json"
                path = snapshot / name
                path.write_bytes(payload)
                evidence.append({"url": response.url, "path": path.relative_to(output).as_posix(),
                                 "byte_size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                                 "captured_at": now()})
                if url == PAGE:
                    if b"Microdatos" not in payload or b"</html" not in payload.lower():
                        raise ValueError("Unexpected microdata search page")
                    url = CATALOGUE
                else:
                    data = response.json()["d"]
                    items.extend(data["results"])
                    url = urljoin(response.url, data["__next"]) if data.get("__next") else None
            finally:
                response.close()
        if not items or len({x["FileRef"] for x in items}) != len(items):
            raise ValueError("Empty or duplicated official catalogue")
        for item in items:
            item_identity(item)
        write_json(snapshot / "manifest.json", {"captured_at": now(), "artifacts": evidence,
                   "file_count": len(items), "years": dict(Counter(str(x["A_x00f1_o"]) for x in items))})
        return items, snapshot
    finally:
        transport.close()


def collect(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not os.environ.get("CO_INS_HTTP_PROXY") and not os.environ.get("CO_INS_SOCKS_PROXY"):
            raise ValueError("An explicit CO_INS proxy is required for this archive")
        items, snapshot = capture_catalogue(output)
        selected = [x for x in items if not args.years or int(x["A_x00f1_o"]) in args.years]
        if not selected:
            raise ValueError("No workbooks match the requested years")
        # Prioritize the recent completed years, then preserve older history.
        selected.sort(key=lambda x: (-int(x["A_x00f1_o"]), int(x["File"]["Length"])))
        manifest_path = output / "manifest.json"
        previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        records = {x["source_url"]: x for x in previous.get("files", [])}
        report = {"source": "INS Sivigila 4.0 Microdatos", "portal_url": PAGE,
                  "started_at": now(), "status": "running", "catalogue_snapshot": snapshot.relative_to(output).as_posix(),
                  "catalogue_count": len(items), "selected_count": len(selected),
                  "selected_years": sorted({int(x["A_x00f1_o"]) for x in selected}),
                  "expected_bytes": sum(int(x["File"]["Length"]) for x in selected),
                  "completed_count": 0, "errors": [], "files": []}

        def checkpoint():
            report["updated_at"] = now()
            report["files"] = sorted(records.values(), key=lambda x: (x["year"], x["event_code"]))
            write_json(manifest_path, report)

        checkpoint()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(download_with_retries, item, output, records.get(item_identity(item)[2])): item
                       for item in selected}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    record = future.result()
                    records[record["source_url"]] = record
                    report["completed_count"] += 1
                    print(f"[{report['completed_count']}/{len(selected)}] {item['FileRef']}", flush=True)
                except Exception as exc:
                    # Avoid leaking credential-bearing network exception strings.
                    report["errors"].append({"source_url": item_identity(item)[2], "error_type": type(exc).__name__})
                    print(f"FAILED {item['FileRef']}: {type(exc).__name__}", flush=True)
                checkpoint()
        report["status"] = "complete" if not report["errors"] else "incomplete"
        report["finished_at"] = now()
        checkpoint()
        write_json(snapshot / "download-manifest.json", report)
        return 0 if report["status"] == "complete" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--years", type=int, nargs="+", help="Default: every published year")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    return collect(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
