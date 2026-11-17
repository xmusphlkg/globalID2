"""Chile's source contract: validated case rows become distinct annual counts."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from types import SimpleNamespace

import pytest

from src.data.crawlers.cl import (ChileENOContractError, ChileENOTransportCache,
                                  discover_resources, parse_eno_csv)


def source_csv(*rows):
    header = "etapa_clinica;anho_notificacion;ENO;region;cie_10_diagnostico;diagnostico\r\n"
    return (header + "".join(";".join(row) + "\r\n" for row in rows)).encode("utf-8-sig")


def test_eno_counts_keep_year_classification_and_native_labels():
    payload = source_csv(
        ("CONFIRMADA", "2007", "Sarampion", "Región del Maule", "B05", "Sarampión"),
        ("CONFIRMADA", "2007", "Sarampion", "Región de Aysén", "B05", "Sarampión"),
        ("CONFIRMADA", "2024", "Sarampión", "Región de Aysén", "B05", "Sarampión"),
        ("PROBABLE", "2024", "MPOX", "Región del Maule", "B04", "MPOX"),
    )
    rows, audit = parse_eno_csv(payload, source_url="https://datos.gob.cl/source.csv")
    assert len(rows) == 3
    assert sum(int(row["Cases"]) for row in rows) == 4
    assert audit["source_case_rows"] == 4
    assert audit["catalog_year_mismatch"] is True
    assert {row["GeographyKey"] for row in rows} == {"country:CL:national"}
    assert {row["Date"] for row in rows} == {date(2007, 12, 31).isoformat(), date(2024, 12, 31).isoformat()}
    assert len({row["SourceDiseaseCode"] for row in rows}) == 3
    probable = next(row for row in rows if row["RawDiseaseLabel"] == "MPOX")
    assert probable["Measure"] == "probable_case_notifications"
    assert probable["PublicReleaseEnabled"] == "false"
    assert probable["RawSHA256"] == hashlib.sha256(payload).hexdigest()


def test_missing_year_or_unknown_classification_fails_closed():
    with pytest.raises(ChileENOContractError, match="year"):
        parse_eno_csv(source_csv(("CONFIRMADA", "", "MPOX", "Region", "B04", "MPOX")), source_url="source")
    with pytest.raises(ChileENOContractError, match="classification"):
        parse_eno_csv(source_csv(("SOSPECHOSA", "2024", "MPOX", "Region", "B04", "MPOX")), source_url="source")


def test_no_absent_year_zero_is_synthesized():
    rows, audit = parse_eno_csv(source_csv(("CONFIRMADA", "2024", "MPOX", "Region", "B04", "MPOX")),
                                source_url="source", start_year=2023)
    assert len(rows) == 1
    assert audit["selected_years"] == [2024]
    assert rows[0]["Cases"] == "1"


def test_package_requires_three_official_resources():
    package = {"success": True, "result": {"name": "notificaciones-eno", "id": "official",
               "organization": {"title": "Ministerio de Salud"},
               "resources": [{"id": kind, "name": label, "format": kind,
                              "url": f"https://datos.gob.cl/{kind}"}
                             for kind, label in (("csv", "Bases ENO"),
                                                 ("xlsx", "Diccionario de datos ENO"),
                                                 ("pdf", "Metodologia validacion"))]}}
    _, resources = discover_resources(json.dumps(package).encode())
    assert set(resources) == {"csv", "xlsx", "pdf"}
    package["result"]["resources"][0]["url"] = "https://example.org/csv"
    with pytest.raises(ChileENOContractError, match="official host"):
        discover_resources(json.dumps(package).encode())


def test_csv_transport_cache_reuses_verified_bytes_and_invalidates_revision(tmp_path):
    payload = source_csv(("CONFIRMADA", "2024", "MPOX", "Region", "B04", "MPOX"))
    calls = []

    class Crawler:
        def get(self, url, *, headers):
            calls.append(headers)
            return SimpleNamespace(status_code=200, content=payload,
                                   headers={"ETag": '"revision-1"'}, url=url)

    cache = ChileENOTransportCache(tmp_path)
    url = "https://datos.gob.cl/source.csv"
    _, first = cache.get(Crawler(), url, revision_marker="r1")
    _, second = cache.get(Crawler(), url, revision_marker="r1")
    _, third = cache.get(Crawler(), url, revision_marker="r2")
    assert [first["cache_status"], second["cache_status"], third["cache_status"]] == [
        "downloaded", "hit_verified", "refetched"]
    assert len(calls) == 2
    assert calls[1]["If-None-Match"] == '"revision-1"'
    (tmp_path / f"{first['sha256']}.csv").write_bytes(b"tampered")
    with pytest.raises(ChileENOContractError, match="integrity"):
        cache.get(Crawler(), url, revision_marker="r2")
