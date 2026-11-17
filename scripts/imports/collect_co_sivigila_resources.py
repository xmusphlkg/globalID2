#!/usr/bin/env python3
"""Preserve public Sivigila data, codebooks and reports beside microdata."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.imports.collect_co_microdatos import now, sha256_file, write_json
from src.data.crawlers.co_ins_transport import ColombiaINSOfficialTransport

BASE = "https://portalsivigila.ins.gov.co"
OUTPUT = ROOT / "data/raw/co/sivigila-resources"
LIBRARIES = {"VigilanciaRutinaria": "routine_surveillance", "Tablas Referencia Sivigila": "reference_tables",
             "Operación Estadística": "statistical_operation", "Documentos": "documents",
             "Publicaciones": "publications", "Polvora2022": "special_surveillance"}
WORKER = threading.local()
PDFIUM_LOCK = threading.Lock()


def validate_original(path, extension):
    with path.open("rb") as stream:
        magic = stream.read(1024)
    if extension in {".xlsx", ".docx", ".pptx", ".zip"}:
        with zipfile.ZipFile(path) as archive:
            identity = {".xlsx": "xl/workbook.xml", ".docx": "word/document.xml", ".pptx": "ppt/presentation.xml"}.get(extension)
            if identity and not {"[Content_Types].xml", identity} <= set(archive.namelist()):
                raise ValueError("Not an XLSX workbook" if extension == ".xlsx" else "Not an Office document")
            if archive.testzip():
                raise ValueError("Archive CRC mismatch")
        return "zip_members_crc_ok"
    if extension == ".pdf":
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdfpage import PDFPage
        if not magic.startswith(b"%PDF-"):
            raise ValueError("Not a PDF")
        try:
            with path.open("rb") as stream:
                document = PDFDocument(PDFParser(stream))
                if next(PDFPage.create_pages(document), None) is None:
                    raise ValueError("PDF has no pages")
        except Exception:
            # Some official originals have broken cross-reference tables. Keep
            # their bytes and distinguish PDFium recovery from normal parsing.
            import pypdfium2 as pdfium
            with PDFIUM_LOCK:
                document = pdfium.PdfDocument(str(path))
                try:
                    if not len(document):
                        raise ValueError("PDF has no pages")
                    for index in range(len(document)):
                        page = document[index]
                        try:
                            page.get_size()
                        finally:
                            page.close()
                finally:
                    document.close()
            return "pdfium_recovered_structure_ok"
        return "pdf_structure_ok"
    if extension == ".rar":
        if not magic.startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")):
            raise ValueError("Not a RAR archive")
        try:
            subprocess.run(["unrar", "t", "-idq", "-p-", str(path)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # unrar-free cannot decode some RAR3 Unicode filenames. lsar
            # explicitly tests each member, including its checksum.
            result = subprocess.run(["lsar", "-t", "-j", str(path)], check=True,
                                    capture_output=True, text=True, timeout=600)
            members = json.loads(result.stdout)["lsarContents"]
            files = [item for item in members if not item.get("XADIsDirectory")]
            if not files or any(item.get("lsarTestResult") != "ok" for item in files):
                raise ValueError("RAR member integrity test failed")
            return "rar_members_crc_ok_lsar"
        return "rar_members_crc_ok"
    if extension in {".png", ".jpg", ".jpeg"}:
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        return "image_structure_ok"
    if extension == ".xls":
        import xlrd
        book = xlrd.open_workbook(str(path), on_demand=True)
        try:
            if not book.sheet_names():
                raise ValueError("XLS has no worksheets")
        finally:
            book.release_resources()
        return "xls_structure_ok"
    if extension == ".doc":
        from xlrd.compdoc import CompDoc
        if not CompDoc(path.read_bytes()).get_named_stream("WordDocument"):
            raise ValueError("Not a Word document")
        return "word_compound_document_ok"
    if extension == ".mp4":
        if b"ftyp" not in magic[:64]:
            raise ValueError("Not an MP4 container")
        return "mp4_container_header_ok"
    raise ValueError("Unreviewed resource format")


def resource_plan(discovery):
    listing = discovery / "libraries.json"
    libraries = json.loads(listing.read_text())["d"]["results"] if listing.exists() else []
    resources = []

    def add_items(items, *, origin, title, category, root_path, catalogue, excluded_extensions=()):
        for item in items:
            if item.get("FileSystemObjectType") == 1 or "Length" not in item.get("File", {}):
                continue
            ref = item["FileRef"]
            if Path(ref).suffix.lower() in excluded_extensions:
                continue
            if not ref.startswith(root_path + "/"):
                raise ValueError("Unexpected document library path")
            if any(part in {".", ".."} for part in ref.split("/")):
                raise ValueError("Invalid resource path")
            resources.append({"source_url": origin + quote(ref, safe="/"), "source_path": ref,
                              "source_library": title, "category": category,
                              "source_modified": item["Modified"], "catalogue_id": item["Id"],
                              "expected_bytes": int(item["File"]["Length"]),
                              "catalogue_path": catalogue, "filename": Path(ref).name})

    for library in libraries:
        category = LIBRARIES.get(library["Title"])
        if category is None:
            continue
        path = discovery / (library["Id"] + ".json")
        data = json.loads(path.read_text())["d"]
        if data.get("__next"):
            raise ValueError("Discovery catalogue is incomplete; capture all pages first")
        add_items(data["results"], origin=BASE, title=library["Title"], category=category,
                  root_path=library["RootFolder"]["ServerRelativeUrl"], catalogue=path.name)
    linked = discovery / "linked-libraries.json"
    if linked.exists():
        for library in json.loads(linked.read_text()):
            for index, filename in enumerate(library["catalogue_files"]):
                data = json.loads((discovery / filename).read_text())["d"]
                if bool(data.get("__next")) != (index < len(library["catalogue_files"]) - 1):
                    raise ValueError("Linked library pagination is incomplete")
                add_items(data["results"], origin=library["origin"], title=library["title"],
                          category=library["category"], root_path=library["root_path"], catalogue=filename,
                          excluded_extensions=library.get("excluded_extensions", ()))
    if not resources or len({item["source_url"] for item in resources}) != len(resources):
        raise ValueError("Empty or duplicated resource plan")
    return resources


def download(resource, output, previous, transport=None):
    if previous and previous.get("source_modified") == resource["source_modified"]:
        path = output / previous["path"]
        if (path.is_file() and path.stat().st_size == resource["expected_bytes"]
                and sha256_file(path) == previous["sha256"]):
            return previous
    directory = output / "files" / resource["category"]
    directory.mkdir(parents=True, exist_ok=True)
    owns_transport = transport is None
    transport = transport or ColombiaINSOfficialTransport(timeout=180)
    temporary = None
    try:
        response = transport.get(resource["source_url"], stream=True, headers={"Accept-Encoding": "identity"})
        try:
            size = 0
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    size += len(chunk)
                    if size > resource["expected_bytes"]:
                        raise ValueError("Resource exceeds official byte size")
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if size != resource["expected_bytes"]:
                raise ValueError("Resource size differs from official catalogue")
            if (response.headers.get("Content-Length") and not response.headers.get("Content-Encoding")
                    and int(response.headers["Content-Length"]) != size):
                raise ValueError("Resource size differs from HTTP length")
            extension = Path(resource["filename"]).suffix.lower()
            validation = validate_original(temporary, extension)
            checksum = sha256_file(temporary)
            destination = directory / checksum[:20]
            destination.mkdir(exist_ok=True)
            path = destination / resource["filename"]
            temporary.replace(path)
            return {**resource, "path": path.relative_to(output).as_posix(), "sha256": checksum,
                    "byte_size": size, "final_url": response.url, "captured_at": now(), "validation": validation}
        finally:
            response.close()
    finally:
        if owns_transport:
            transport.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def retry_download(resource, output, previous):
    for attempt in range(3):
        try:
            return download(resource, output, previous, getattr(WORKER, "transport", None))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def verify(output, resources, records):
    errors, verified = [], []
    sums = {}
    for sidecar in (output / "discovery").glob("*.source.json"):
        source = json.loads(sidecar.read_text())
        try:
            path = (output / "discovery" / source["path"]).resolve()
            path.relative_to(output.resolve())
            if path.stat().st_size != source["byte_size"] or sha256_file(path) != source["sha256"]:
                raise ValueError("Discovery snapshot integrity mismatch")
            sums[path.relative_to(output).as_posix()] = source["sha256"]
        except (OSError, ValueError) as exc:
            errors.append({"discovery_path": source["path"], "error_type": type(exc).__name__})
    for resource in resources:
        record = records.get(resource["source_url"])
        if not record:
            errors.append({"source_url": resource["source_url"], "error": "missing_file"})
            continue
        try:
            path = (output / record["path"]).resolve()
            path.relative_to(output.resolve())
            if (path.stat().st_size != resource["expected_bytes"]
                    or record["byte_size"] != resource["expected_bytes"]
                    or record["source_modified"] != resource["source_modified"]
                    or sha256_file(path) != record["sha256"]):
                raise ValueError("File integrity mismatch")
            verified.append(record)
        except (OSError, ValueError) as exc:
            errors.append({"source_url": resource["source_url"], "error_type": type(exc).__name__})
    report = {"verified_at": now(), "status": "complete_verified" if not errors else "incomplete",
              "expected_files": len(resources), "verified_files": len(verified),
              "expected_bytes": sum(item["expected_bytes"] for item in resources),
              "verified_bytes": sum(item["byte_size"] for item in verified),
              "files_by_category": dict(sorted(Counter(item["category"] for item in verified).items())), "errors": errors}
    write_json(output / "verification.json", report)
    if not errors:
        fields = ["category", "filename", "source_library", "byte_size", "path", "sha256", "source_url", "source_modified", "validation"]
        with (output / "inventory.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(sorted(verified, key=lambda item: (item["category"], item["filename"])))
        sums.update({item["path"]: item["sha256"] for item in verified})
        (output / "SHA256SUMS").write_text("".join(f"{checksum}  {path}\n" for path, checksum in sorted(sums.items())))
    return report


def collect(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not args.verify_only and not os.environ.get("CO_INS_HTTP_PROXY") and not os.environ.get("CO_INS_SOCKS_PROXY"):
            raise ValueError("An explicit INS proxy is required")
        resources = resource_plan(output / "discovery")
        write_json(output / "resource-plan.json", resources)
        path = output / "manifest.json"
        old = json.loads(path.read_text()) if path.exists() else {}
        records = {item["source_url"]: item for item in old.get("files", [])}
        if args.verify_only:
            return 0 if verify(output, resources, records)["status"] == "complete_verified" else 1
        report = {"source": "INS Sivigila additional public resources", "started_at": now(), "status": "running",
                  "selected_count": len(resources), "expected_bytes": sum(item["expected_bytes"] for item in resources),
                  "completed_count": 0, "errors": []}
        last_checkpoint = 0.0

        def checkpoint():
            nonlocal last_checkpoint
            report["updated_at"] = now()
            report["files"] = sorted(records.values(), key=lambda item: item["source_url"])
            write_json(path, report)
            last_checkpoint = time.monotonic()

        checkpoint()
        transports = []
        transport_lock = threading.Lock()

        def initialize_worker():
            WORKER.transport = ColombiaINSOfficialTransport(timeout=180)
            with transport_lock:
                transports.append(WORKER.transport)

        with ThreadPoolExecutor(max_workers=args.workers, initializer=initialize_worker) as pool:
            futures = {pool.submit(retry_download, item, output, records.get(item["source_url"])): item for item in
                       sorted(resources, key=lambda item: item["expected_bytes"])}
            for future in as_completed(futures):
                resource = futures[future]
                needs_checkpoint = True
                try:
                    record = future.result()
                    needs_checkpoint = records.get(record["source_url"]) is not record
                    records[record["source_url"]] = record
                    report["completed_count"] += 1
                    print(f"[{report['completed_count']}/{len(resources)}] {resource['source_path']}", flush=True)
                except Exception as exc:
                    report["errors"].append({"source_url": resource["source_url"], "error_type": type(exc).__name__})
                    print(f"FAILED {resource['source_path']}: {type(exc).__name__}", flush=True)
                # Reused records are already durable. Avoid rewriting the whole
                # manifest thousands of times during a hash-only resume, while
                # still checkpointing every newly saved original or failure.
                if needs_checkpoint or time.monotonic() - last_checkpoint >= 5:
                    checkpoint()
        for transport in transports:
            transport.close()
        report["status"] = "complete" if not report["errors"] else "incomplete"
        report["finished_at"] = now()
        checkpoint()
        result = verify(output, resources, records)
        return 0 if result["status"] == "complete_verified" and not report["errors"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--verify-only", action="store_true")
    return collect(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
