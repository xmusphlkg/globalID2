"""Additional public archives must keep originals and reject error responses."""
import hashlib
import json
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from scripts.imports import collect_co_sivigila_resources as archive


def test_disguised_html_is_not_a_pdf(tmp_path):
    path = tmp_path / "document.part"
    path.write_bytes(b"<html>Sign in</html>")
    with pytest.raises(ValueError, match="Not a PDF"):
        archive.validate_original(path, ".pdf")


def test_real_pdf_and_zip_are_validated(tmp_path):
    from PIL import Image
    path = tmp_path / "document.part"
    Image.new("RGB", (20, 20), "white").save(path, format="PDF")
    assert archive.validate_original(path, ".pdf") == "pdf_structure_ok"
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("protocol.txt", "case definitions")
    assert archive.validate_original(path, ".zip") == "zip_members_crc_ok"
    with pytest.raises(ValueError, match="Not an XLSX"):
        archive.validate_original(path, ".xlsx")


def test_pdf_recovery_preserves_original_bytes_and_rejects_empty_pdf(tmp_path, monkeypatch):
    from PIL import Image
    from pdfminer.pdfparser import PDFSyntaxError
    path = tmp_path / "document.part"
    Image.new("RGB", (20, 20), "white").save(path, format="PDF")
    payload = path.read_bytes()

    def fail_pdfminer(*args):
        raise PDFSyntaxError("No /Root object!")

    monkeypatch.setattr("pdfminer.pdfdocument.PDFDocument", fail_pdfminer)
    assert archive.validate_original(path, ".pdf") == "pdfium_recovered_structure_ok"
    assert path.read_bytes() == payload
    path.write_bytes(b"%PDF-1.7\nnot a document")
    with pytest.raises(Exception):
        archive.validate_original(path, ".pdf")


@pytest.mark.parametrize("member_result, succeeds", [("ok", True), ("not_tested", False), ("failed", False)])
def test_rar_fallback_requires_each_file_to_pass_crc(tmp_path, monkeypatch, member_result, succeeds):
    path = tmp_path / "archive.part"
    path.write_bytes(b"Rar!\x1a\x07\x00")

    def run(command, **kwargs):
        if command[0] == "unrar":
            raise subprocess.CalledProcessError(1, command)
        assert command[:3] == ["lsar", "-t", "-j"]
        return SimpleNamespace(stdout=json.dumps({"lsarContents": [
            {"lsarTestResult": member_result}, {"XADIsDirectory": 1, "lsarTestResult": "not_tested"}]}))

    monkeypatch.setattr(archive.subprocess, "run", run)
    if succeeds:
        assert archive.validate_original(path, ".rar") == "rar_members_crc_ok_lsar"
    else:
        with pytest.raises(ValueError, match="integrity test failed"):
            archive.validate_original(path, ".rar")


def test_verification_requires_every_file_and_matching_hash(tmp_path):
    (tmp_path / "discovery").mkdir()
    payload = b"source original"
    (tmp_path / "original.pdf").write_bytes(payload)
    resource = {"source_url": "https://portalsivigila.ins.gov.co/example.pdf",
                "expected_bytes": len(payload), "source_modified": "2026-11-16"}
    record = {**resource, "path": "original.pdf", "byte_size": len(payload),
              "sha256": hashlib.sha256(payload).hexdigest(), "category": "documents", "filename": "example.pdf"}
    assert archive.verify(tmp_path, [resource], {})["status"] == "incomplete"
    assert archive.verify(tmp_path, [resource], {resource["source_url"]: record})["status"] == "complete_verified"
    (tmp_path / "original.pdf").write_bytes(b"x" * len(payload))
    assert archive.verify(tmp_path, [resource], {resource["source_url"]: record})["status"] == "incomplete"


def test_corrupted_source_catalogue_prevents_success(tmp_path):
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    payload = b"official catalogue"
    (discovery / "catalogue.json").write_bytes(b"x" * len(payload))
    (discovery / "catalogue.source.json").write_text(json.dumps({"path": "catalogue.json",
        "byte_size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}))
    report = archive.verify(tmp_path, [], {})
    assert report["status"] == "incomplete"
    assert not (tmp_path / "SHA256SUMS").exists()


def test_linked_ins_report_library_requires_complete_pagination(tmp_path):
    config = [{"origin": "https://www.ins.gov.co", "title": "Informesdeevento", "category": "event_reports",
               "root_path": "/buscador-eventos/Informesdeevento", "catalogue_files": ["reports.json"]}]
    (tmp_path / "linked-libraries.json").write_text(json.dumps(config))
    catalogue = {"d": {"results": [{"Id": 1, "FileRef": "/buscador-eventos/Informesdeevento/Dengue 2026.pdf",
                                   "Modified": "2026-11-16", "File": {"Length": "100"}}]}}
    (tmp_path / "reports.json").write_text(json.dumps(catalogue))
    resources = archive.resource_plan(tmp_path)
    assert len(resources) == 1
    assert resources[0]["source_url"] == "https://www.ins.gov.co/buscador-eventos/Informesdeevento/Dengue%202026.pdf"
    catalogue["d"]["__next"] = "https://www.ins.gov.co/next-page"
    (tmp_path / "reports.json").write_text(json.dumps(catalogue))
    with pytest.raises(ValueError, match="pagination is incomplete"):
        archive.resource_plan(tmp_path)
