from __future__ import annotations

import pytest

from src.data.processors.mapping_lookup import (
    MappingConflictError,
    build_mapping_lookup,
    load_country_mapping_dict,
    normalize_mapping_key,
)


def test_normalize_mapping_key_handles_unicode_case_and_spacing() -> None:
    assert normalize_mapping_key("  Ｈｅｐａｔｉｔｉｓ   B  ") == "hepatitis b"


def test_build_mapping_lookup_accepts_same_target_alias_variants() -> None:
    result = build_mapping_lookup(
        [("Hepatitis B", 8, "D008"), ("ＨＥＰＡＴＩＴＩＳ B", 8, "D008")],
        country_code="ZZ",
    )

    assert result == {"hepatitis b": 8}


def test_build_mapping_lookup_rejects_ambiguous_active_alias() -> None:
    with pytest.raises(MappingConflictError, match="D008.*D068"):
        build_mapping_lookup(
            [("Hepatitis B", 8, "D008"), ("hepatitis b", 68, "D068")],
            country_code="ZZ",
        )


def test_source_specific_mapping_overrides_wildcard() -> None:
    rows = [
        ("HIV", 5, "D005", "*", None),
        ("HIV", 162, "D162", "SRC_ZZ_HIV", "SER_ZZ_HIV"),
    ]

    assert build_mapping_lookup(
        rows, country_code="ZZ", source_id="src_zz_hiv"
    ) == {"hiv": 162}
    assert build_mapping_lookup(
        rows, country_code="ZZ", source_id="SRC_ZZ_OTHER"
    ) == {"hiv": 5}


def test_source_specific_targets_require_source_context() -> None:
    rows = [
        ("HIV", 5, "D005", "SRC_ZZ_AIDS", None),
        ("HIV", 162, "D162", "SRC_ZZ_HIV", None),
    ]

    with pytest.raises(MappingConflictError, match="Source is required.*HIV"):
        build_mapping_lookup(rows, country_code="ZZ")


def test_same_source_conflict_fails_even_with_explicit_source() -> None:
    rows = [
        ("HIV", 5, "D005", "SRC_ZZ_HIV", None),
        ("ＨＩＶ", 162, "D162", "src_zz_hiv", None),
    ]

    with pytest.raises(MappingConflictError, match="SRC_ZZ_HIV.*D005.*D162"):
        build_mapping_lookup(rows, country_code="ZZ", source_id="SRC_ZZ_HIV")


async def test_loader_treats_pre_migration_rows_as_wildcard() -> None:
    class _Result:
        def __init__(self, rows=(), scalar_value=None):
            self._rows = rows
            self._scalar_value = scalar_value

        def scalar(self):
            return self._scalar_value

        def __iter__(self):
            return iter(self._rows)

    class _DB:
        def __init__(self):
            self.sql: list[str] = []

        async def execute(self, statement, _parameters=None):
            sql = str(statement)
            self.sql.append(sql)
            if "information_schema.columns" in sql:
                return _Result(scalar_value=False)
            return _Result(rows=[("HIV", 162, "D162", "*")])

    db = _DB()
    mapping = await load_country_mapping_dict(
        db, "zz", source_id="SRC_ZZ_SURVEILLANCE"
    )

    assert mapping == {"hiv": 162}
    assert "dm.source_id" not in db.sql[1]
