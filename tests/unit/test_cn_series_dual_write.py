from __future__ import annotations

import pandas as pd
import pytest

from src.data.crawlers.base import CrawlerResult
from src.data.crawlers.cn import ChinaCDCCrawler
from src.data.processors.cn import DataProcessor
from src.data.storage.record_store import RecordStore
from src.data.storage.series_observation_store import SeriesObservationStore


@pytest.mark.asyncio
async def test_cn_normalization_preserves_exact_source_label() -> None:
    class Mapper:
        @staticmethod
        async def map_dataframe(df, *, disease_col):
            assert disease_col == "DiseasesCN"
            mapped = df.copy()
            mapped["disease_id"] = "D071"
            mapped["standard_name_en"] = "Unspecified viral hepatitis"
            mapped["standard_name_zh"] = "未明示的病毒性肝炎"
            return mapped

    processor = DataProcessor(country_code="CN")
    result = await processor._normalize_disease_names(
        pd.DataFrame([{"DiseasesCN": "肝炎（未分型）"}]),
        language="zh",
        disease_mapper=Mapper(),
    )

    assert result.iloc[0]["RawDiseaseLabel"] == "肝炎（未分型）"
    assert result.iloc[0]["DiseasesCN"] == "未明示的病毒性肝炎"


@pytest.mark.asyncio
async def test_cn_parser_to_series_keeps_unspecified_hepatitis_identity() -> None:
    class Mapper:
        @staticmethod
        async def map_dataframe(df, *, disease_col):
            mapped = df.copy()
            mapped["disease_id"] = "D071"
            mapped["standard_name_en"] = "Unspecified viral hepatitis"
            mapped["standard_name_zh"] = "未明示的病毒性肝炎"
            return mapped

    processor = DataProcessor(country_code="CN")
    parsed = processor.parser.parse(
        """
        <table><tbody>
          <tr><td>病种</td><td>病例数</td><td>死亡数</td></tr>
          <tr><td>肝炎（未分型）</td><td>4</td><td>0</td></tr>
        </tbody></table>
        """,
        language="zh",
        date=pd.Timestamp("2023-08-01"),
        year_month="2023 August",
        source="China CDC",
    )
    assert parsed.success is True
    assert parsed.data.iloc[0]["DiseasesCN"] == "肝炎未分型"
    assert parsed.data.iloc[0]["RawDiseaseLabel"] == "肝炎（未分型）"

    normalized = await processor._normalize_disease_names(
        parsed.data,
        language="zh",
        disease_mapper=Mapper(),
    )
    built = SeriesObservationStore().build_observations(
        normalized.to_dict(orient="records"),
        "CN",
        source_id="SRC_CN_CDC",
        geography_key="country:CN:national",
    )

    assert built.skipped_unmatched == 0
    assert built.observations[0]["series_code"] == (
        "SER_CN_UNSPECIFIED_VIRAL_HEPATITIS"
    )
    assert built.observations[0]["geography_key"] == "country:CN:national"


@pytest.mark.parametrize(
    ("raw_label", "canonical_name", "canonical_name_zh", "series_code"),
    [
        (
            "Hepatitis",
            "Viral Hepatitis",
            "病毒性肝炎",
            "SER_CN_VIRAL_HEPATITIS",
        ),
        (
            "Other hepatitis",
            "Unspecified viral hepatitis",
            "未明示的病毒性肝炎",
            "SER_CN_UNSPECIFIED_VIRAL_HEPATITIS",
        ),
    ],
)
def test_cn_english_online_labels_survive_canonicalization(
    raw_label,
    canonical_name,
    canonical_name_zh,
    series_code,
) -> None:
    built = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2025-12-01",
                "RawDiseaseLabel": raw_label,
                "Diseases": canonical_name,
                "DiseasesCN": canonical_name_zh,
                "Cases": 1,
            }
        ],
        "CN",
        source_id="SRC_CN_CDC",
        geography_key="country:CN:national",
    )

    assert built.skipped_unmatched == 0
    assert built.observations[0]["series_code"] == series_code


def test_cn_duplicate_retrieval_channels_have_deterministic_priority() -> None:
    candidates = [
        CrawlerResult(
            title="PubMed copy",
            year_month="2025 December",
            metadata={"source": "PubMed"},
        ),
        CrawlerResult(
            title="Government copy",
            year_month="2025 December",
            metadata={"source": "Gov Data"},
        ),
        CrawlerResult(
            title="Direct bulletin",
            year_month="2025 December",
            metadata={"source": "China CDC Weekly"},
        ),
    ]

    selected = ChinaCDCCrawler._select_preferred_period_results(candidates)

    assert [item.title for item in selected] == ["Direct bulletin"]


@pytest.mark.asyncio
async def test_record_store_accepts_caller_owned_transaction(monkeypatch) -> None:
    store = RecordStore()
    caller_session = object()
    calls = []

    async def save_in_session(
        db,
        df,
        country_code,
        *,
        cleanup_adjacent_duplicates,
    ):
        calls.append(
            (db, country_code, cleanup_adjacent_duplicates, len(df))
        )
        return 1, 0, 0

    monkeypatch.setattr(store, "_save_dataframe_in_session", save_in_session)

    result = await store.save_dataframe(
        pd.DataFrame([{"disease_id": "D006"}]),
        "CN",
        db=caller_session,
    )

    assert result == (1, 0, 0)
    assert calls == [(caller_session, "CN", True, 1)]
