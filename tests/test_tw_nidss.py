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


def test_tw_nidss_csv_download_falls_back_to_official_http_on_ssl_eof(
    monkeypatch,
):
    from src.data.crawlers.tw import TaiwanNIDSSCrawler

    crawler = TaiwanNIDSSCrawler()
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/csv"}
        content = "發病年份,發病月份,確定病例數\n2026,6,1\n".encode()

        def raise_for_status(self):
            return None

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.startswith("https://"):
            raise requests.exceptions.SSLError("unexpected eof while reading")
        return FakeResponse()

    monkeypatch.setattr(crawler.session, "request", fake_request)

    response = crawler._get_csv_response(
        "https://od.cdc.gov.tw/eic/Age_County_Gender_061.csv"
    )

    assert response.status_code == 200
    assert calls[0][1].startswith("https://od.cdc.gov.tw/")
    assert calls[1][1].startswith("http://od.cdc.gov.tw/")

    crawler._get_csv_response(
        "https://od.cdc.gov.tw/eic/Age_County_Gender_042.csv"
    )
    assert calls[2][1].startswith("http://od.cdc.gov.tw/")


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


def test_tw_updater_recovers_from_raw_monthly_cache_when_live_fetch_fails(tmp_path, monkeypatch):
    from src.data.processors.tw import TWMonthlyUpdater

    output_csv = tmp_path / "taiwan_national_monthly.csv"
    output_csv.write_text(
        (
            ",Disease,DiseaseCode,Year,Month,Date,Cases,LocalCases,ImportedCases,Source,SourceURL\n"
            '1,登革熱,061,2026,6,2026-06-01,1,1,0,"Taiwan, China CDC NIDSS Open Data",'
            "https://od.cdc.gov.tw/eic/Age_County_Gender_061.csv\n"
        ),
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw" / "tw"
    monthly_dir = raw_dir / "monthly"
    monthly_dir.mkdir(parents=True)
    (monthly_dir / "061.csv").write_text(
        (
            "確定病名,發病年份,發病月份,縣市,鄉鎮,性別,是否為境外移入,年齡層,確定病例數,縣市別代碼,鄉鎮別代碼\n"
            "061,2026,08,台南市,善化區,F,1,45~49,2,67000,67000190\n"
            "061,2026,08,桃園市,龜山區,M,0,25~29,3,68000,68000070\n"
        ),
        encoding="utf-8",
    )

    def fail_live_fetch(self, *args, **kwargs):
        raise RuntimeError("[TW-NIDSS] upstream unavailable")

    monkeypatch.setattr(
        "src.data.processors.tw.TaiwanNIDSSCrawler.crawl_monthly_national",
        fail_live_fetch,
    )

    result = TWMonthlyUpdater(output_csv=output_csv).refresh_source(
        months=[(2026, 8)],
        raw_dir=raw_dir,
    )

    assert result.source_latest_date.isoformat() == "2026-08-01"
    assert result.rows == [
        {
            "Date": "2026-08-01",
            "RawDiseaseLabel": "登革熱",
            "DiseaseCode": "061",
            "Year": "2026",
            "Month": "8",
            "Cases": "5",
            "LocalCases": "3",
            "ImportedCases": "2",
            "Source": "Taiwan, China CDC NIDSS Open Data",
            "SourceURL": "https://od.cdc.gov.tw/eic/Age_County_Gender_061.csv",
            "DatasetStatus": "provisional",
            "IsProvisional": "true",
        }
    ]
    assert any("raw monthly cache" in log for log in result.script_logs)
    assert "2026-08-01" in output_csv.read_text(encoding="utf-8")
