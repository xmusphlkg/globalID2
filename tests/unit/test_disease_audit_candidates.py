"""The disease-audit scan reads disease cells without building a row dict."""

from __future__ import annotations

import asyncio

import pytest

from src.services import disease_duplicate_audit_service as audit_module
from src.services.disease_duplicate_audit_service import DiseaseDuplicateAuditService


def _write_catalogue(path) -> None:
    path.write_text(
        "disease_id,standard_name_en,standard_name_zh,category,icd_10,icd_11,description,source\n"
        "D001,Anthrax,炭疽,Bacterial,,,,,\n",
        encoding="utf-8",
    )


def test_new_disease_candidates_keep_first_seen_terms(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(audit_module, "ROOT", tmp_path)
    standard = tmp_path / "standard.csv"
    _write_catalogue(standard)
    mapping = tmp_path / "mapping"
    mapping.mkdir()
    current = tmp_path / "current" / "jp"
    current.mkdir(parents=True)
    (current / "cases.csv").write_text(
        "Disease,Cases\n"
        "Anthrax,1\n"
        "Mystery Fever,2\n"
        "Mystery Fever,3\n"
        "D099,4\n"
        "123,5\n"
        "   ,6\n"
        "Total,7\n"
        "Unknown,8\n",
        encoding="utf-8",
    )

    service = DiseaseDuplicateAuditService(standard, mapping, tmp_path / "current")
    catalogue = audit_module.read_csv(standard)
    found = service._new_disease_candidates(catalogue)

    assert len(found) == 1
    item = found[0]
    assert item["country_code"] == "JP"
    assert item["term"] == "mystery fever"
    assert item["raw_terms"] == ["Mystery Fever"]
    assert item["row_count"] == 2
    assert item["files"] == ["current/jp/cases.csv"]


def test_disease_cell_reader_skips_short_rows(tmp_path) -> None:
    path = tmp_path / "short.csv"
    path.write_text("Cases,Disease\nAnthrax\n1,Cholera\n", encoding="utf-8")

    assert list(audit_module._iter_disease_cells(path)) == ["Cholera"]


@pytest.mark.asyncio
async def test_status_reuses_the_local_audit_for_one_fingerprint(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(audit_module, "ROOT", tmp_path)
    audit_module._AUDIT_STATUS_CACHE.clear()
    standard = tmp_path / "standard.csv"
    _write_catalogue(standard)
    mapping = tmp_path / "mapping"
    mapping.mkdir()
    current = tmp_path / "current"
    current.mkdir()
    service = DiseaseDuplicateAuditService(standard, mapping, current)
    calls = {"count": 0}
    original = service.run_local_audit

    def wrapped(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "run_local_audit", wrapped)

    async def routes():
        return []

    monkeypatch.setattr(audit_module, "get_runtime_routes", routes)

    first = await service.status()
    await asyncio.sleep(0.002)
    second = await service.status()

    assert calls["count"] == 1
    assert first["local_summary"]["new_disease_candidates"] == 0
    assert second["local_summary"] == first["local_summary"]
    assert second["generated_at"] != first["generated_at"]
    audit_module._AUDIT_STATUS_CACHE.clear()
