import struct

import pytest

from scripts.update_br_subdivisions import (
    coalesce_source_rows,
    projected_dbf_records,
    record_month,
    residence_state,
)


def test_residence_uses_ibge_state_and_never_notifying_state():
    assert residence_state({"SG_UF": "35", "SG_UF_NOT": "33"}) == "BR-SP"
    assert residence_state({"SG_UF": "", "ID_MN_RESI": "3304557"}) == "BR-RJ"
    assert residence_state({"SG_UF": "99", "SG_UF_NOT": "35"}) is None
    assert residence_state({"SG_UF": "35", "ID_MN_RESI": "330455"}) is None
    assert residence_state({"SG_UF": "99", "ID_MN_RESI": "999999"}) is None


def test_monthly_state_totals_preserve_each_annual_source_contribution():
    base = {
        "CountryCode": "BR-SP",
        "DiseaseCode": "DENG",
        "Date": "2025-01-01",
        "Cases": "2",
        "SourceFiles": "DENGBR24.dbc",
        "SourceURLs": "ftp://official/DENGBR24.dbc",
        "DatasetStatus": "final",
    }
    following = {
        **base,
        "Cases": "3",
        "SourceFiles": "DENGBR25.dbc",
        "SourceURLs": "ftp://official/DENGBR25.dbc",
        "DatasetStatus": "preliminary",
    }
    row = coalesce_source_rows([base, following])[0]
    assert row["Cases"] == "5"
    assert row["SourceFiles"] == "DENGBR24.dbc|DENGBR25.dbc"
    with pytest.raises(ValueError, match="Duplicate source-file"):
        coalesce_source_rows([base, base])


def test_notification_date_precedence_and_missing_date_fallback():
    assert record_month({"DT_NOTIFIC": "20240229", "DT_SIN_PRI": "20240101"}, 2024) == (
        2024,
        2,
        "DT_NOTIFIC",
    )
    assert record_month({"DT_NOTIFIC": "********", "DT_SIN_PRI": "20240301"}, 2024) == (
        2024,
        3,
        "DT_SIN_PRI",
    )
    assert record_month({"DT_NOTIFIC": "20240230", "NU_ANO": "2023"}, 2024) == (
        2023,
        1,
        "year_only_fallback",
    )


def write_dbf(path, fields, records):
    header_length = 32 + 32 * len(fields) + 1
    record_length = 1 + sum(length for _, _, length in fields)
    header = bytearray(32)
    header[0] = 3
    struct.pack_into("<IHH", header, 4, len(records), header_length, record_length)
    with path.open("wb") as handle:
        handle.write(header)
        for name, kind, length in fields:
            descriptor = bytearray(32)
            descriptor[: len(name)] = name.encode()
            descriptor[11] = ord(kind)
            descriptor[16] = length
            handle.write(descriptor)
        handle.write(b"\r")
        for deleted, values in records:
            handle.write(b"*" if deleted else b" ")
            for name, _, length in fields:
                handle.write(values.get(name, "").encode().ljust(length))


def test_projected_dbf_reads_geography_dates_only_and_ignores_deleted_records(tmp_path):
    path = tmp_path / "cases.dbf"
    fields = [("SG_UF", "C", 2), ("DT_NOTIFIC", "D", 8), ("PATIENT", "C", 12)]
    write_dbf(
        path,
        fields,
        [
            (False, {"SG_UF": "35", "DT_NOTIFIC": "20240101", "PATIENT": "private"}),
            (True, {"SG_UF": "33", "DT_NOTIFIC": "20240101"}),
        ],
    )
    assert list(projected_dbf_records(path)) == [
        {"SG_UF": "35", "DT_NOTIFIC": "20240101"}
    ]
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="Truncated"):
        list(projected_dbf_records(path))


def test_dbf_without_residence_fields_fails_instead_of_using_notifying_state(tmp_path):
    path = tmp_path / "notifying.dbf"
    write_dbf(path, [("SG_UF_NOT", "C", 2)], [(False, {"SG_UF_NOT": "35"})])
    with pytest.raises(LookupError):
        list(projected_dbf_records(path))
