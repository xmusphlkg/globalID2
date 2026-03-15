import pandas as pd

from dashboard.api.schemas.disease_record import DiseaseRecordOut
from src.ai.agents.analyst import AnalystAgent
from src.core.missing_values import normalize_rate_columns, normalize_rate_value


def test_normalize_rate_value_treats_negative_sentinel_as_missing() -> None:
    assert normalize_rate_value(-10) is None
    assert normalize_rate_value("-10") is None
    assert normalize_rate_value(0.125) == 0.125


def test_normalize_rate_columns_replaces_negative_rate_sentinels() -> None:
    df = pd.DataFrame(
        {
            "incidence_rate": [-10, 0.3],
            "mortality_rate": ["-10", "0.02"],
        }
    )

    normalized = normalize_rate_columns(df)

    assert pd.isna(normalized.loc[0, "incidence_rate"])
    assert pd.isna(normalized.loc[0, "mortality_rate"])
    assert normalized.loc[1, "incidence_rate"] == 0.3
    assert normalized.loc[1, "mortality_rate"] == 0.02


def test_disease_record_schema_converts_missing_rate_sentinels_to_none() -> None:
    record = DiseaseRecordOut.model_validate(
        {
            "time": "2026-03-01T00:00:00",
            "disease_id": 1,
            "country_id": 1,
            "incidence_rate": -10,
            "mortality_rate": "-10",
        }
    )

    assert record.incidence_rate is None
    assert record.mortality_rate is None


def test_analyst_statistics_ignore_negative_rate_sentinels() -> None:
    analyst = AnalystAgent()
    data = pd.DataFrame(
        {
            "cases": [10, 20],
            "deaths": [1, 2],
            "incidence_rate": [-10, 0.2],
        }
    )

    stats = analyst._calculate_statistics(data)

    assert stats["avg_incidence_rate"] == 0.2
    assert stats["max_incidence_rate"] == 0.2