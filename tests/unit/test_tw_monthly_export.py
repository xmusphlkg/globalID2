"""Official NIDSS ODS snapshots preserve the existing monthly source semantics."""
from pathlib import Path
from types import SimpleNamespace
import io
import zipfile

import pytest
import requests

from src.data.crawlers.tw import (
    DEFAULT_MONTHLY_EXPORT_URL, TWDiseaseSource, TaiwanNIDSSCrawler,
    normalize_monthly_exports, parse_monthly_export, is_tw_ods_transient_error,
    ODS_TIMEOUT,
)

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion/TW"
DISEASE = TWDiseaseSource("061", "登革熱", "", "")


def exports():
    return [
        {"payload": (FIXTURES / filename).read_bytes(), "metadata": {
            "code": "061", "name": "登革熱", "months": [(2026, 9)],
            "date_basis": "發病日", "population": population,
        }}
        for filename, population in [("monthly_local.ods", "本土病例"), ("monthly_imported.ods", "境外移入病例")]
    ]


def test_actual_official_export_uses_national_total_not_regional_subtotals():
    rows = normalize_monthly_exports(exports(), disease=DISEASE)
    assert len(rows) == 1
    assert rows[0]["Date"] == "2026-09-01"
    assert (rows[0]["Cases"], rows[0]["LocalCases"], rows[0]["ImportedCases"]) == ("25", "2", "23")
    assert rows[0]["SourceURL"] == DEFAULT_MONTHLY_EXPORT_URL


def test_historical_export_accepts_pre_2000_years():
    data = exports()
    for item in data:
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(item["payload"])) as original, zipfile.ZipFile(buffer, "w") as output:
            for name in original.namelist():
                content = original.read(name)
                if name == "content.xml":
                    content = content.replace(b"2026", b"1998")
                output.writestr(name, content)
        item["payload"] = buffer.getvalue()
        item["metadata"]["months"] = [(1998, 9)]
    rows = normalize_monthly_exports(data, disease=DISEASE)
    assert [(row["Date"], row["Cases"]) for row in rows] == [("1998-09-01", "25")]


def test_aids_diagnosis_and_nationality_are_not_imported_infections():
    data = [{"payload": (FIXTURES / "monthly_aids.ods").read_bytes(), "metadata": {
        "code": "042", "name": "後天免疫缺乏症候群", "months": [(2026, 9)],
        "date_basis": "診斷日", "population": "本國籍及非本國籍",
    }}]
    row = normalize_monthly_exports(data, disease=TWDiseaseSource("042", "後天免疫缺乏症候群", "", ""))[0]
    assert (row["Cases"], row["LocalCases"], row["ImportedCases"]) == ("48", "", "")


def test_unsplit_source_counts_do_not_invent_local_or_imported_zeros(tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries.monthly_common import normalized_csv

    data = [{"payload": (FIXTURES / "monthly_unsplit.ods").read_bytes(), "metadata": {
        "code": "487a", "name": "流感併發重症", "months": [(2026, 9), (2026, 10)],
        "date_basis": "發病日", "population": "本土病例及境外移入病例",
    }}]
    rows = normalize_monthly_exports(data, disease=TWDiseaseSource("487a", "流感併發重症", "", ""))
    assert len(rows) == 2 and all(int(row["Cases"]) >= 0 for row in rows)
    assert all(row["LocalCases"] == row["ImportedCases"] == "" for row in rows)
    normalized = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv").parse_source_csv(normalized_csv(rows))
    assert all(row["LocalCases"] == row["ImportedCases"] == "" for row in normalized)


@pytest.mark.parametrize("change,match", [
    ({"date_basis": "衛生局收到日"}, "date basis"),
    ({"population": "境外移入病例"}, "population scope"),
    ({"months": [(2026, 8), (2026, 9)]}, "cover the requested months"),
])
def test_export_refuses_semantic_drift_or_missing_months(change, match):
    item = exports()[0]
    args = {key: item["metadata"][key] for key in ("date_basis", "population", "months")}
    args.update(change)
    with pytest.raises(ValueError, match=match):
        parse_monthly_export(item["payload"], disease=DISEASE, **args)


@pytest.mark.parametrize("indexes", [[0], [0, 0], [0, 1, 1]])
def test_partial_or_duplicate_population_batch_cannot_be_stored(indexes):
    data = exports()
    with pytest.raises(ValueError, match="populations"):
        normalize_monthly_exports([data[index] for index in indexes], disease=DISEASE)


def test_export_requests_pin_the_date_basis_and_population_with_verified_tls(monkeypatch):
    crawler = TaiwanNIDSSCrawler()
    page = b'''<form action="/Export/NidssReportTimer"><input name="pty_disease" value="061"><input name="__RequestVerificationToken" value="fixture"></form>
    <select name="pty_date_type_value"><option value="1">RECEIVED</option><option value="3">ONSET</option></select>
    <select name="pty_immigration_value"><option value="0">LOCAL</option><option value="1">IMPORTED</option></select>
    <select name="pty_sickclass_value"><option value="determined_cnt">CONFIRMED</option></select>'''.decode().replace("ONSET", "發病日").replace("LOCAL", "本土病例").replace("IMPORTED", "境外移入病例").replace("CONFIRMED", "確定病例").encode()
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert kwargs["verify"] is True
        payload = page if method == "GET" else exports()[int(kwargs["data"]["pty_immigration_value"])]["payload"]
        return SimpleNamespace(content=payload, raise_for_status=lambda: None)
    monkeypatch.setattr(crawler.session, "request", request)
    result = crawler.fetch_monthly_exports(DISEASE, months=[(2026, 9)])
    assert len(result) == 2
    for method, url, kwargs in calls[1:]:
        assert method == "POST" and url == DEFAULT_MONTHLY_EXPORT_URL
        assert kwargs["data"]["pty_date_type_value"] == "3"
        assert kwargs["data"]["pty_period_value"] == "ym"
        assert kwargs["data"]["pty_sickclass_value"] == "determined_cnt"
    assert all(kwargs["timeout"] == ODS_TIMEOUT for _, _, kwargs in calls)
    for url in ("https://nidss.cdc.gov.tw/nndss/disease?id=061", DEFAULT_MONTHLY_EXPORT_URL):
        assert crawler.session.get_adapter(url).max_retries.total == 0


@pytest.mark.parametrize("exc,expected", [
    (requests.Timeout("read timeout"), True),
    (requests.ConnectionError("connection reset"), True),
    (requests.exceptions.SSLError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"), True),
    (requests.exceptions.SSLError("connection reset by peer"), True),
    (requests.exceptions.SSLError("certificate verify failed: hostname mismatch, unexpected EOF while reading"), False),
    (requests.exceptions.SSLError("certificate has expired"), False),
    (requests.exceptions.SSLError("unsupported protocol"), False),
    (ValueError("missing confirmed cases"), False),
])
def test_ods_transport_failure_classification(exc, expected):
    assert is_tw_ods_transient_error(exc) is expected


@pytest.mark.parametrize("status,expected", [(404, False), (400, False), (429, True), (500, True), (504, True)])
def test_ods_http_failure_classification(status, expected):
    response = requests.Response()
    response.status_code = status
    assert is_tw_ods_transient_error(requests.HTTPError(response=response)) is expected


def _zip_with_content(name, payload):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, payload)
    return buffer.getvalue()


@pytest.mark.parametrize("payload", [
    b"PK\x03\x04truncated",
    _zip_with_content("not-content.xml", b"<root/>"),
    _zip_with_content("content.xml", b"<root>"),
])
def test_corrupt_exports_are_rejected_with_clear_error(payload):
    with pytest.raises(ValueError, match="corrupt or incomplete"):
        parse_monthly_export(payload, disease=DISEASE, months=[(2026, 9)], date_basis="發病日", population="本土病例")


def test_corrupt_export_is_isolated_from_healthy_disease(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw
    from src.services.ingestion.countries.monthly_common import normalized_csv

    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    prior = normalized_csv([{"Date": "2026-09-01", "RawDiseaseLabel": "另一病種", "DiseaseCode": "bad", "Cases": "7"}])
    artifacts = [SimpleNamespace(kind="prior_csv", payload=prior)]
    artifacts.extend(SimpleNamespace(kind="monthly_ods", payload=item["payload"], metadata_=item["metadata"]) for item in exports())
    for item in exports():
        artifacts.append(SimpleNamespace(kind="monthly_ods", payload=b"PK\x03\x04truncated", metadata_={**item["metadata"], "code": "bad", "name": "另一病種"}))
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    result = tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(2026, 9)], output_csv=updater.output_csv)
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["rejected_exports"] == [{"code": "bad", "reason": "NIDSS monthly export is corrupt or incomplete"}]
    assert {row["DiseaseCode"]: row["Cases"] for row in result.rows} == {"061": "25", "bad": "7"}
    assert next(row for row in result.rows if row["DiseaseCode"] == "bad")["SourceFreshness"] == "cache_fallback"


def test_organize_replays_original_ods_offline(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw
    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    monkeypatch.setattr(TaiwanNIDSSCrawler, "fetch_monthly_exports", lambda *args, **kwargs: pytest.fail("organize accessed network"))
    artifacts = [SimpleNamespace(kind="monthly_ods", payload=item["payload"], metadata_=item["metadata"]) for item in exports()]
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    result = tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(2026, 9)], output_csv=updater.output_csv)
    assert len(result.rows) == 1
    assert result.rows[0]["Cases"] == "25"
    assert result.rows[0]["SourceURL"] == DEFAULT_MONTHLY_EXPORT_URL


def test_live_export_refreshes_values_even_when_prior_snapshot_has_more_diseases(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw
    from src.services.ingestion.countries.monthly_common import normalized_csv

    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    prior = normalized_csv([
        {"Date": "2026-09-01", "RawDiseaseLabel": "登革熱", "DiseaseCode": "061", "Cases": "999", "LocalCases": "999", "ImportedCases": "0"},
        {"Date": "2026-09-01", "RawDiseaseLabel": "另一病種", "DiseaseCode": "fixture", "Cases": "7", "LocalCases": "7", "ImportedCases": "0"},
    ])
    artifacts = [SimpleNamespace(kind="prior_csv", payload=prior)]
    artifacts.extend(SimpleNamespace(kind="monthly_ods", payload=item["payload"], metadata_=item["metadata"]) for item in exports())
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    result = tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(2026, 9)], output_csv=updater.output_csv)
    assert {row["DiseaseCode"]: row["Cases"] for row in result.rows} == {"061": "25", "fixture": "7"}
    assert result.script_logs == ["[organize] live_rows=1 fallback_rows=1"]
    assert result.source_audit["status"] == "partial"
    fallback = next(row for row in result.rows if row["DiseaseCode"] == "fixture")
    assert fallback["SourceFreshness"] == "cache_fallback"
    assert fallback["SourceDataDate"] == "2026-09-01"
    assert [(row["DiseaseCode"], row["Cases"]) for row in result.store_rows] == [("061", "25")]


def test_all_live_failures_do_not_report_cached_data_as_a_refresh(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw
    from src.services.ingestion.countries.monthly_common import normalized_csv

    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    prior = normalized_csv([{"Date": "2026-09-01", "RawDiseaseLabel": "登革熱", "DiseaseCode": "061", "Cases": "999"}])
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    with pytest.raises(RuntimeError, match="cached data is not a successful refresh"):
        tw.organize([SimpleNamespace(kind="prior_csv", payload=prior)], updater=updater, source="nidss_open_data", months=[(2026, 9)], output_csv=updater.output_csv)


def test_missing_disease_without_a_cache_is_recorded_as_partial(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw

    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    artifacts = [SimpleNamespace(kind="monthly_ods", payload=item["payload"], metadata_={**item["metadata"], "expected_disease_codes": ["061", "010"]}) for item in exports()]
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    result = tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(2026, 9)], output_csv=updater.output_csv)
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["missing_live_disease_codes"] == ["010"]
    assert result.source_audit["fallback_rows"] == 0


def test_invalid_disease_export_is_isolated_and_partial_prior_coverage_is_retained(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw
    from src.services.ingestion.countries.monthly_common import normalized_csv

    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    prior = normalized_csv([{"Date": "2026-09-01", "RawDiseaseLabel": "另一病種", "DiseaseCode": "bad", "Cases": "7"}])
    artifacts = [SimpleNamespace(kind="prior_csv", payload=prior)]
    artifacts.extend(SimpleNamespace(kind="monthly_ods", payload=item["payload"], metadata_=item["metadata"]) for item in exports())
    # The second disease lacks its imported population, so none of it is live.
    item = exports()[0]
    artifacts.append(SimpleNamespace(kind="monthly_ods", payload=item["payload"], metadata_={**item["metadata"], "code": "bad", "name": "另一病種"}))
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    result = tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(2026, 9), (2026, 10)], output_csv=updater.output_csv)
    assert {row["DiseaseCode"]: row["Cases"] for row in result.rows} == {"061": "25", "bad": "7"}
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["rejected_exports"] == [{"code": "bad", "reason": "NIDSS export batch has missing or duplicate populations"}]
    assert next(row for row in result.rows if row["DiseaseCode"] == "bad")["SourceFreshness"] == "cache_fallback"


def test_all_invalid_exports_still_fail_closed(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw

    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    item = exports()[0]
    artifacts = [SimpleNamespace(kind="monthly_ods", payload=item["payload"], metadata_=item["metadata"])]
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    with pytest.raises(RuntimeError, match="cached data is not a successful refresh"):
        tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(2026, 9)], output_csv=updater.output_csv)


def _monthly_ods_artifacts(code, name, year, month, local, imported):
    """Synthetic source workbooks retain NIDSS labels and national totals."""
    artifacts = []
    for population, value in (("本土病例", local), ("境外移入病例", imported)):
        xml = f'''<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet><table:table>
        <table:table-row><table:table-cell><text:p>日期種類:發病日</text:p></table:table-cell></table:table-row>
        <table:table-row><table:table-cell><text:p>病例種類:確定病例</text:p></table:table-cell></table:table-row>
        <table:table-row><table:table-cell><text:p>感染來源:{population}</text:p></table:table-cell></table:table-row>
        <table:table-row><table:table-cell><text:p>年月</text:p></table:table-cell></table:table-row>
        <table:table-row><table:table-cell/><table:table-cell><text:p>{year}</text:p></table:table-cell></table:table-row>
        <table:table-row><table:table-cell/><table:table-cell><text:p>{month}</text:p></table:table-cell></table:table-row>
        <table:table-row><table:table-cell><text:p>合計</text:p></table:table-cell><table:table-cell><text:p>{value}</text:p></table:table-cell></table:table-row>
        </table:table></office:spreadsheet></office:body></office:document-content>'''
        artifacts.append(SimpleNamespace(kind="monthly_ods", payload=_zip_with_content("content.xml", xml.encode()), metadata_={
            "code": code, "name": name, "months": [(year, month)],
            "date_basis": "發病日", "population": population,
        }))
    return artifacts


def test_organize_excludes_historical_series_zero_outside_reviewed_validity(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw

    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    updater = TWMonthlyUpdater(output_csv=tmp_path / "out.csv")
    updater._default_recent_months = lambda: [(2026, 9)]
    artifacts = []
    for year, local in [(1998, 0), (2023, 3), (2026, 0)]:
        artifacts.extend(_monthly_ods_artifacts("19CVS", "嚴重特殊傳染性肺炎", year, 9, local, 0))
    result = tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(1998, 9), (2023, 9), (2026, 9)], output_csv=updater.output_csv)
    assert [(r["Date"], r["Cases"]) for r in result.rows] == [("2023-09-01", "3")]
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["excluded_rows"] == [
        {"code": "19CVS", "date": "1998-09-01", "reason": "outside_reviewed_validity"},
        {"code": "19CVS", "date": "2026-09-01", "reason": "outside_reviewed_validity"},
    ]


def test_organize_rejects_nonzero_outside_reviewed_validity(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw

    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    updater = TWMonthlyUpdater(output_csv=tmp_path / "out.csv")
    artifacts = _monthly_ods_artifacts("19CVS", "嚴重特殊傳染性肺炎", 1998, 9, 0, 1)
    with pytest.raises(ValueError, match="19CVS has a nonzero observation outside its reviewed validity on 1998-09-01"):
        tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(1998, 9)], output_csv=updater.output_csv)


def test_organize_excludes_unverified_historical_ods_zeros(monkeypatch, tmp_path):
    from src.data.processors.tw import TWMonthlyUpdater
    from src.services.ingestion.countries import tw

    monkeypatch.setattr(tw, "read_artifact", lambda item: item.payload)
    updater = TWMonthlyUpdater(output_csv=tmp_path / "out.csv")
    updater._default_recent_months = lambda: [(2026, 9)]
    artifacts = []
    artifacts.extend(_monthly_ods_artifacts("NoCoV", "中東呼吸症候群冠狀病毒感染症", 1998, 9, 0, 0))
    artifacts.extend(_monthly_ods_artifacts("NoCoV", "中東呼吸症候群冠狀病毒感染症", 2026, 9, 0, 0))
    result = tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(1998, 9), (2026, 9)], output_csv=updater.output_csv)
    assert [(r["Date"], r["Cases"]) for r in result.rows] == [("2026-09-01", "0")]
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["excluded_rows"] == [{"code": "NoCoV", "date": "1998-09-01", "reason": "unverified_historical_zero"}]
