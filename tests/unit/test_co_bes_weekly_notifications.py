"""Only a table-local weekly contract can admit an Observado value."""
import pytest

from src.data.crawlers.co_bes import BESContractError
from src.data.crawlers.co_bes_weekly import page_weekly_table

METHOD = ("Los casos esperados se construyen con casos históricos que han sido confirmados, "
          "mientras que los casos observados corresponden a la notificación de la semana analizada "
          "exceptuando los casos descartados.")
TABLE = """Tabla 2. Comparación de casos notificados de eventos priorizados, de baja frecuencia, según el
comportamiento histórico, Colombia, a semana epidemiológica 20 de 2025
Sección eventos última semana
Evento Observado Esperado p
Leishmaniasis cutánea 24 83 0,00
Zika 7 5 0,10
Chicungunya 0 2 0,11
Mortalidad por dengue 6 4 0,11
Lepra 7 5 0,12
Leishmaniasis mucosa 0 1 0,30
Fiebre tifoidea y paratifoidea 1 1 0,37
Fuente: Sivigila, Instituto Nacional de Salud, Colombia, 2025
"""


def test_last_week_definition_produces_cases_and_deaths_separately():
    rows = page_weekly_table(METHOD + "\n" + TABLE, year=2025, week=20)
    assert len(rows) == 7
    by_event = {r["event_code"]: r for r in rows}
    assert by_event["420"]["value"] == 24
    assert by_event["217"]["value"] == 0
    assert by_event["580"]["metric"] == "deaths"
    assert by_event["580"]["value"] == 6
    assert by_event["420"]["expected_raw"] == "83"


def test_observed_header_without_explicit_weekly_definition_is_not_enough():
    assert page_weekly_table(TABLE, year=2025, week=20) == []
    assert page_weekly_table(METHOD.replace("semana analizada", "periodo acumulado") + TABLE,
                             year=2025, week=20) == []


def test_annex_observed_and_wrong_report_period_are_excluded():
    assert page_weekly_table(METHOD + "\nDepartamento Acumulado 2025 Esperado Observado\nTotal nacional 682 80 124",
                             year=2025, week=20) == []
    assert page_weekly_table(METHOD + TABLE, year=2025, week=19) == []
    assert page_weekly_table(METHOD + TABLE, year=2026, week=20) == []


def test_missing_or_unreviewed_event_does_not_claim_complete_table():
    with pytest.raises(BESContractError, match="incomplete"):
        page_weekly_table(METHOD + TABLE.replace("Zika 7 5 0,10\n", ""), year=2025, week=20)
    with pytest.raises(BESContractError, match="Unreviewed"):
        page_weekly_table(METHOD + TABLE.replace("Zika", "Unknown disease"), year=2025, week=20)


def test_chikunguna_spelling_variant_and_table_number_change_keep_same_identity():
    table = TABLE.replace("Tabla 2.", "Tabla 4.").replace("Chicungunya", "Chikunguña")
    assert next(r for r in page_weekly_table(METHOD + table, year=2025, week=20)
                if r["event_code"] == "217")["value"] == 0


def test_decimal_expected_censored_p_and_optional_pertussis_do_not_shift_columns():
    table = TABLE.replace("24 83 0,00", "24 83,4 <0,05")
    table = table.replace("Lepra 7 5 0,12", "Tos ferina 38 34 0,05\nLepra 7 5 0,12")
    rows = page_weekly_table(METHOD + table, year=2025, week=20)
    assert len(rows) == 7
    cutaneous = next(row for row in rows if row["event_code"] == "420")
    assert (cutaneous["value"], cutaneous["expected_raw"], cutaneous["p_raw"]) == (24, "83,4", "<0,05")


def test_original_omitted_death_row_is_null_while_reported_zero_stays_zero():
    table = TABLE.replace("Mortalidad por dengue 6 4 0,11\n", "")
    rows = page_weekly_table(METHOD + table, year=2025, week=20)
    assert next(r for r in rows if r["event_code"] == "580")["value"] is None
    assert next(r for r in rows if r["event_code"] == "217")["value"] == 0
