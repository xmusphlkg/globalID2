"""Microdata originals must be complete Excel files before being archived."""
import hashlib
import io
import zipfile

import pytest

from scripts.imports import collect_co_microdatos as archive


def workbook_bytes():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as book:
        book.writestr("[Content_Types].xml", "<Types/>")
        book.writestr("xl/workbook.xml", "<workbook/>")
    return stream.getvalue()


def item(payload):
    return {"A_x00f1_o": "2025", "CodigoEvento": "210", "Id": 1,
            "FileRef": "/Microdatos/Datos_2025_210.xlsx", "NombreEvento": "Dengue",
            "Modified": "2026-08-07T00:00:00Z", "File": {"Length": str(len(payload))}}


def fake_transport(monkeypatch, payload):
    class Response:
        url = archive.BASE + "/Microdatos/Datos_2025_210.xlsx"
        headers = {"Content-Length": str(len(payload))}

        def iter_content(self, chunk_size):
            yield payload

        def close(self):
            pass

    class Transport:
        calls = 0

        def __init__(self, **kwargs):
            pass

        def get(self, *args, **kwargs):
            Transport.calls += 1
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(archive, "ColombiaINSOfficialTransport", Transport)
    return Transport


def test_complete_file_is_verified_and_resumed_with_corruption_repair(tmp_path, monkeypatch):
    payload = workbook_bytes()
    transport = fake_transport(monkeypatch, payload)
    record = archive.download(item(payload), tmp_path)
    path = tmp_path / record["path"]
    assert path.read_bytes() == payload
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    assert archive.download(item(payload), tmp_path, record) == record
    assert transport.calls == 1
    path.write_bytes(b"x" * len(payload))
    archive.download(item(payload), tmp_path, record)
    assert transport.calls == 2
    assert path.read_bytes() == payload


@pytest.mark.parametrize("payload", [b"<html>Sign in</html>", workbook_bytes()[:-12]])
def test_html_and_truncated_zip_are_not_saved(tmp_path, monkeypatch, payload):
    fake_transport(monkeypatch, payload)
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        archive.download(item(payload), tmp_path)
    assert not list(tmp_path.rglob("*.xlsx"))
    assert not list(tmp_path.rglob("*.part"))


def test_partial_file_is_rejected_against_official_catalogue(tmp_path, monkeypatch):
    payload = workbook_bytes()
    fake_transport(monkeypatch, payload[:-1])
    with pytest.raises(ValueError, match="catalogue byte size"):
        archive.download(item(payload), tmp_path)
    assert not list(tmp_path.rglob("*.xlsx"))


def test_new_official_version_preserves_previous_bytes(tmp_path, monkeypatch):
    payload = workbook_bytes()
    fake_transport(monkeypatch, payload)
    old = archive.download(item(payload), tmp_path)
    stream = io.BytesIO(payload)
    with zipfile.ZipFile(stream, "a") as book:
        book.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    updated_payload = stream.getvalue()
    updated = item(updated_payload)
    updated["Modified"] = "2026-11-16T00:00:00Z"
    fake_transport(monkeypatch, updated_payload)
    new = archive.download(updated, tmp_path, old)
    assert old["path"] != new["path"]
    assert (tmp_path / old["path"]).read_bytes() == payload
    assert (tmp_path / new["path"]).read_bytes() == updated_payload


@pytest.mark.parametrize("ref", ["/Microdatos/../../other.xlsx", "https://example.com/x.xlsx"])
def test_catalogue_cannot_select_external_or_traversal_paths(ref):
    entry = item(workbook_bytes())
    entry["FileRef"] = ref
    with pytest.raises(ValueError, match="workbook path"):
        archive.item_identity(entry)
