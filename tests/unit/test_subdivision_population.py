from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from scripts.import_subdivision_population import (
    ABS_STATES,
    DATA,
    SubdivisionPopulation,
    load_abs_population,
    load_cn_population,
    validate_rows,
)


def test_official_cn_snapshot_covers_31_provinces_every_year():
    rows = load_cn_population(DATA / "cn_nbs_year_end_2000_2024.csv")
    assert len(rows) == 31 * 25
    for year in range(2000, 2025):
        assert len([row for row in rows if row.year == year]) == 31
    assert (
        next(
            row.population
            for row in rows
            if row.country_code == "CN-GD" and row.year == 2024
        )
        == 127_800_000
    )
    assert (
        next(
            row.population
            for row in rows
            if row.country_code == "CN-ZJ" and row.year == 2024
        )
        == 66_700_000
    )
    assert not any(row.year > 2024 for row in rows)


def test_abs_uses_persons_june_and_only_observed_dates_in_same_year(tmp_path: Path):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data1"
    sheet.append(
        [None]
        + [f"Estimated Resident Population ; Persons ; {name} ;" for name in ABS_STATES]
        + ["Estimated Resident Population ; Male ; New South Wales ;"]
    )
    sheet.append(["Unit"] + ["Persons"] * 9)
    sheet.append([datetime(2025, 3, 1)] + [100] * 8 + [40])
    sheet.append([datetime(2025, 6, 1)] + [200] * 8 + [80])
    sheet.append([datetime(2025, 12, 1)] + [300] * 8 + [120])
    sheet.append([datetime(2026, 3, 1)] + [400] * 8 + [160])
    path = tmp_path / "abs.xlsx"
    book.save(path)
    rows = load_abs_population(path)
    assert len(rows) == 16
    assert all(row.population == 200 for row in rows if row.year == 2025)
    assert all(
        row.population == 400 and row.basis == "latest_observed_quarter_same_year"
        for row in rows
        if row.year == 2026
    )
    assert not any(row.year == 2027 for row in rows)


def test_population_rejects_unsupported_locations_duplicates_and_wrong_reference_year():
    def row(code="CN-GD", year=2024, population=100, reference="2024-12-31"):
        return SubdivisionPopulation(
            code,
            year,
            population,
            "NBS",
            reference,
            "https://www.stats.gov.cn/",
            "year_end",
        )

    for invalid in [
        [row("US")],
        [row(population=0)],
        [row(), row()],
        [row(reference="2025-12-31")],
    ]:
        with pytest.raises(ValueError):
            validate_rows(invalid)


def test_official_abs_snapshot_has_eight_local_denominators():
    rows = load_abs_population(DATA / "abs_310104_mar2026.xlsx")
    assert {row.country_code for row in rows} == set(ABS_STATES.values())
    assert {row.year for row in rows} == set(range(1981, 2027))
    assert len(rows) == 8 * 46
    assert len({row.population for row in rows if row.year == 2025}) == 8
