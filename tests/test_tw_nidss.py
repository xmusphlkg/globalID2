from __future__ import annotations

import requests

from src.core.source_scopes import (
    canonical_data_source_label,
    canonicalize_task_source,
    scope_display_label,
    scope_from_data_source,
)
from src.data.crawlers.tw import (
    DEFAULT_SOURCE_NAME,
    TWDiseaseSource,
    aggregate_monthly_csv_rows,
)


def test_tw_nidss_monthly_aggregation_keeps_local_and_imported_counts():
    disease = TWDiseaseSource(
        code="061",
        name="登革熱",
        monthly_csv_url="https://od.cdc.gov.tw/eic/Age_County_Gender_061.csv",
        weekly_csv_url="https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_061.csv",
    )
    rows = [
        {
            "發病年份": "2026",
            "發病月份": "01",
            "是否為境外移入": "0",
            "確定病例數": "2",
        },
        {
            "發病年份": "2026",
            "發病月份": "01",
            "是否為境外移入": "1",
            "確定病例數": "3",
        },
        {
            "發病年份": "2026",
            "發病月份": "02",
            "是否為境外移入": "0",
            "確定病例數": "5",
        },
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
            "Source": DEFAULT_SOURCE_NAME,
            "SourceURL": "https://od.cdc.gov.tw/eic/Age_County_Gender_061.csv",
        }
    ]


def test_tw_nidss_monthly_aggregation_accepts_diagnosis_year_month_columns():
    disease = TWDiseaseSource(
        code="042",
        name="後天免疫缺乏症候群",
        monthly_csv_url="https://od.cdc.gov.tw/eic/Age_County_Gender_042.csv",
        weekly_csv_url="https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_042.csv",
    )
    rows = [
        {
            "診斷年份": "2025",
            "診斷月份": "12",
            "非本國籍": "0",
            "確定病例數": "2",
        },
        {
            "診斷年份": "2026",
            "診斷月份": "01",
            "非本國籍": "1",
            "確定病例數": "3",
        },
    ]

    aggregated = aggregate_monthly_csv_rows(disease, rows, months={(2026, 1)})

    assert aggregated == [
        {
            "Date": "2026-01-01",
            "RawDiseaseLabel": "後天免疫缺乏症候群",
            "DiseaseCode": "042",
            "Year": "2026",
            "Month": "1",
            "Cases": "3",
            "LocalCases": "3",
            "ImportedCases": "0",
            "Source": DEFAULT_SOURCE_NAME,
            "SourceURL": "https://od.cdc.gov.tw/eic/Age_County_Gender_042.csv",
        }
    ]


def test_tw_nidss_source_scope_aliases():
    assert canonicalize_task_source("nidss", country_code="TW") == "nidss_open_data"
    assert canonicalize_task_source("tw", country_code="TW") == "nidss_open_data"
    assert scope_from_data_source(DEFAULT_SOURCE_NAME) == "nidss_open_data"
    assert scope_from_data_source("Taiwan CDC NIDSS Open Data") == "nidss_open_data"
    assert canonical_data_source_label(DEFAULT_SOURCE_NAME) == "Taiwan, China CDC NIDSS"
    assert (
        canonical_data_source_label("Taiwan CDC NIDSS Open Data")
        == "Taiwan, China CDC NIDSS"
    )
    assert (
        scope_display_label("nidss_open_data", country_code="TW")
        == "Taiwan, China CDC NIDSS"
    )


def test_tw_nidss_csv_download_failure_is_skipped(monkeypatch):
    from src.data.crawlers.tw import TaiwanNIDSSCrawler

    disease = TWDiseaseSource(
        code="050",
        name="天花",
        monthly_csv_url="https://od.cdc.gov.tw/eic/Age_County_Gender_050.csv",
        weekly_csv_url="https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_050.csv",
    )
    crawler = TaiwanNIDSSCrawler()

    monkeypatch.setattr(
        "src.data.crawlers.tw.time.sleep", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        crawler.session,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.exceptions.SSLError("ssl eof")
        ),
    )

    assert crawler._download_csv_text(disease) is None


def test_tw_nidss_csv_download_uses_twca_bundle_on_ssl_chain_error(
    tmp_path, monkeypatch
):
    from src.data.crawlers.tw import TaiwanNIDSSCrawler

    disease = TWDiseaseSource(
        code="050",
        name="天花",
        monthly_csv_url="https://od.cdc.gov.tw/eic/Age_County_Gender_050.csv",
        weekly_csv_url="https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_050.csv",
    )
    bundle = tmp_path / "twca-bundle.pem"
    bundle.write_text("bundle", encoding="utf-8")
    crawler = TaiwanNIDSSCrawler()
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/csv"}
        content = "發病年份,發病月份,是否為境外移入,確定病例數\n2026,6,0,1\n".encode()

        def raise_for_status(self):
            return None

    def fake_request(_method, _url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise requests.exceptions.SSLError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                "unable to get local issuer certificate"
            )
        return FakeResponse()

    monkeypatch.setattr(
        "src.data.crawlers.tw.time.sleep", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "src.core.tls.build_augmented_ca_bundle_for_url",
        lambda *_args, **_kwargs: str(bundle),
    )
    monkeypatch.setattr(crawler.session, "request", fake_request)

    csv_text = crawler._download_csv_text(disease)

    assert csv_text is not None
    assert "確定病例數" in csv_text
    assert calls[0]["verify"] is True
    assert calls[1]["verify"] == str(bundle)
    assert all(call["verify"] is not False for call in calls)
    assert crawler._tls_fallback._verify_by_host["od.cdc.gov.tw"] == str(bundle)


def test_tw_nidss_crawl_continues_when_one_disease_fails(tmp_path, monkeypatch):
    from src.data.crawlers.tw import TaiwanNIDSSCrawler

    failed = TWDiseaseSource(
        code="050",
        name="天花",
        monthly_csv_url="https://od.cdc.gov.tw/eic/Age_County_Gender_050.csv",
        weekly_csv_url="https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_050.csv",
    )
    healthy = TWDiseaseSource(
        code="061",
        name="登革熱",
        monthly_csv_url="https://od.cdc.gov.tw/eic/Age_County_Gender_061.csv",
        weekly_csv_url="https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_061.csv",
    )
    crawler = TaiwanNIDSSCrawler()
    monkeypatch.setattr(crawler, "fetch_disease_index", lambda: [failed, healthy])

    def fake_download(disease):
        if disease.code == "050":
            raise RuntimeError("temporary upstream failure")
        return "發病年份,發病月份,是否為境外移入,確定病例數\n2026,6,0,4\n"

    monkeypatch.setattr(crawler, "_download_csv_text", fake_download)

    output_csv = tmp_path / "taiwan_national_monthly.csv"
    summary = crawler.crawl_monthly_national(output_csv, months=[(2026, 6)])
    output = output_csv.read_text(encoding="utf-8")

    assert summary.row_count == 1
    assert summary.diseases_fetched == 1
    assert "登革熱" in output
    assert "天花" not in output
