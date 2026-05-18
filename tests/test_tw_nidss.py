from __future__ import annotations

from src.core.source_scopes import (
    canonical_data_source_label,
    canonicalize_task_source,
    scope_display_label,
    scope_from_data_source,
)
from src.data.crawlers.tw import TWDiseaseSource, aggregate_monthly_csv_rows


def test_tw_nidss_monthly_aggregation_keeps_local_and_imported_counts():
    disease = TWDiseaseSource(
        code="061",
        name="登革熱",
        monthly_csv_url="https://od.cdc.gov.tw/eic/Age_County_Gender_061.csv",
        weekly_csv_url="https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_061.csv",
    )
    rows = [
        {"發病年份": "2026", "發病月份": "01", "是否為境外移入": "0", "確定病例數": "2"},
        {"發病年份": "2026", "發病月份": "01", "是否為境外移入": "1", "確定病例數": "3"},
        {"發病年份": "2026", "發病月份": "02", "是否為境外移入": "0", "確定病例數": "5"},
    ]

    aggregated = aggregate_monthly_csv_rows(disease, rows, months={(2026, 1)})

    assert aggregated == [
        {
            "Date": "2026-01-01",
            "RawDiseaseLabel": "登革熱",
            "DiseaseCode": "061",
            "Year": "2026",
            "Month": "1",
            "Cases": "5",
            "LocalCases": "2",
            "ImportedCases": "3",
            "Source": "Taiwan CDC NIDSS Open Data",
            "SourceURL": "https://od.cdc.gov.tw/eic/Age_County_Gender_061.csv",
        }
    ]


def test_tw_nidss_source_scope_aliases():
    assert canonicalize_task_source("nidss", country_code="TW") == "nidss_open_data"
    assert canonicalize_task_source("tw", country_code="TW") == "nidss_open_data"
    assert scope_from_data_source("Taiwan CDC NIDSS Open Data") == "nidss_open_data"
    assert canonical_data_source_label("Taiwan CDC NIDSS Open Data") == "Taiwan CDC NIDSS"
    assert scope_display_label("nidss_open_data", country_code="TW") == "Taiwan CDC NIDSS"
