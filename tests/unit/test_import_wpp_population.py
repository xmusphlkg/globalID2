from pathlib import Path

import pytest

from scripts import import_wpp_population


def test_default_wpp_input_prefers_existing_history_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    history = tmp_path / "history.csv"
    processed = tmp_path / "processed.csv"
    history.write_text("history", encoding="utf-8")
    processed.write_text("processed", encoding="utf-8")
    monkeypatch.setattr(
        import_wpp_population,
        "DEFAULT_INPUT_CANDIDATES",
        (history, processed),
    )

    assert import_wpp_population.resolve_default_input() == history


def test_wpp_parser_keeps_all_supported_country_denominators(tmp_path: Path) -> None:
    source = tmp_path / "wpp.csv"
    source.write_text(
        "Iso2,Time,Value,Sex,Age,AgeStart,AgeEnd,IndicatorName\n"
        "JP,2026,122000000,Both sexes,Total,0,-1,Total population by sex\n"
        "KR,2026,51000000,Both sexes,Total,0,-1,Total population by sex\n"
        "BR,2026,213000000,Both sexes,Total,0,-1,Total population by sex\n"
        "CH,2026,9000000,Both sexes,Total,0,-1,Total population by sex\n"
        "HK,2026,7500000,Both sexes,Total,0,-1,Total population by sex\n"
        "TW,2026,23000000,Both sexes,Total,0,-1,Total population by sex\n",
        encoding="utf-8",
    )

    rows = import_wpp_population.load_rows(source)

    assert {row.iso2 for row in rows} == {"JP", "KR", "BR", "CH", "HK", "TW"}


def test_population_plan_automatically_includes_new_database_countries() -> None:
    rows = [
        import_wpp_population.PopulationRow("JP", 2025, 123.0),
        import_wpp_population.PopulationRow("JP", 2026, 122.0),
        import_wpp_population.PopulationRow("GB", 2025, 68.0),
        import_wpp_population.PopulationRow("GB", 2026, 69.0),
    ]

    plan = import_wpp_population.build_population_import_plan(
        rows,
        {"JP": 1, "UK": 2},
    )

    import_wpp_population.validate_population_import_plan(plan)
    assert plan["mapped_country_codes"] == ["JP", "UK"]
    assert len(plan["rows"]) == 4
    assert {
        (row.country_code, row.wpp_iso2)
        for row in plan["rows"]
    } == {("JP", "JP"), ("UK", "GB")}


def test_population_plan_rejects_unmatched_or_incomplete_new_country() -> None:
    rows = [
        import_wpp_population.PopulationRow("JP", 2025, 123.0),
        import_wpp_population.PopulationRow("JP", 2026, 122.0),
        import_wpp_population.PopulationRow("KR", 2025, 51.0),
    ]
    plan = import_wpp_population.build_population_import_plan(
        rows,
        {"JP": 1, "KR": 2, "ZZ": 3},
    )

    with pytest.raises(ValueError, match="no WPP ISO2 match: ZZ") as error:
        import_wpp_population.validate_population_import_plan(plan)

    assert "KR missing 1 year" in str(error.value)


def test_population_plan_excludes_subdivision_without_using_parent_denominator() -> None:
    rows = [import_wpp_population.PopulationRow("CA", 2026, 40000000.0)]
    plan = import_wpp_population.build_population_import_plan(
        rows,
        {"CA": 1, "CA-ON": 2},
        excluded_location_codes={"CA-ON"},
    )

    import_wpp_population.validate_population_import_plan(plan)
    assert plan["mapped_country_codes"] == ["CA"]
    assert plan["excluded_location_codes"] == ["CA-ON"]
    assert all(row.country_code != "CA-ON" for row in plan["rows"])
    assert import_wpp_population.is_wpp_population_target(
        {"location_type": "subdivision", "iso_subdivision_code": "CA-ON"}
    ) is False
