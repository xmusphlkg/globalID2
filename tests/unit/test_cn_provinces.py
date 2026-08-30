from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import requests

from src.data.crawlers.cn_province_adapters.registry import ADAPTERS
from src.data.crawlers.cn_provinces import (
    DATACENTER_SOURCE_ID,
    MONTHLY_REPORT_SOURCE_ID,
    ProvinceDataCenterCrawler,
    _antiword_tables,
    _attachment_suffix,
    _normalize_report_table,
    _resolve_report_disease,
    load_config,
    load_phsm_history,
    load_phsm_history_with_audit,
    parse_datacenter_spreadsheet,
    province_configs,
)
from src.data.processors.cn_provinces import CNProvinceUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.ontology import load_disease_ontology


def test_province_registry_has_31_mainland_jurisdictions() -> None:
    provinces = province_configs()

    assert len(provinces) == 31
    assert provinces["CN-BJ"].adcode == "110000"
    assert provinces["CN-XJ"].name_zh == "新疆维吾尔自治区"


def test_each_province_has_an_independent_adapter_module() -> None:
    adapter_dir = Path("src/data/crawlers/cn_province_adapters")
    modules = {
        path.stem
        for path in adapter_dir.glob("*.py")
        if path.stem not in {"__init__", "base", "registry"}
    }

    assert len(ADAPTERS) == 31
    assert len(modules) == 31
    assert "shanghai" in modules
    assert "xinjiang" in modules


def test_province_registry_covers_all_49_phsm_non_total_disease_categories() -> None:
    diseases = load_config()["diseases"]

    assert len(diseases) == 49
    assert len({item["code"] for item in diseases}) == 49
    assert {item["code"] for item in diseases} >= {
        "viral_hepatitis",
        "hepatitis_d",
        "h5n1",
        "h7n9",
        "covid_19",
        "mpox",
        "schistosomiasis",
    }


def test_phsm_history_keeps_sources_separate_and_does_not_fill_blanks(tmp_path) -> None:
    workbook = tmp_path / "history.xlsx"
    datacenter = pd.DataFrame(
        [
            {"year": 2020, "month": 1, "disease_cn": "甲肝", "disease_en": "Hepatitis A", "province": "Beijing", "value": 2, "url": "https://center.example/"},
            {"year": 2020, "month": 1, "disease_cn": "甲肝", "disease_en": "Hepatitis A", "province": "Beijing", "value": 2, "url": "https://center.example/"},
            {"year": 2020, "month": 2, "disease_cn": "甲肝", "disease_en": "Hepatitis A", "province": "Beijing", "value": None, "url": "https://center.example/"},
            {"year": 2020, "month": 1, "disease_cn": "甲肝", "disease_en": "Hepatitis A", "province": "Total", "value": 99, "url": "https://center.example/"},
        ]
    )
    monthly = pd.DataFrame(
        [
            {"year": 2020, "month": 1, "disease_cn": "甲肝", "disease_en": "Hepatitis A", "province": "Beijing", "value": 1, "url": "https://wjw.example/report"},
            {"year": 2020, "month": 2, "disease_cn": "甲肝", "disease_en": "Hepatitis A", "province": "Beijing", "value": 0, "url": "https://wjw.example/report"},
        ]
    )
    with pd.ExcelWriter(workbook) as writer:
        datacenter.to_excel(writer, sheet_name="ProvinceCenter", index=False)
        monthly.to_excel(writer, sheet_name="ProvinceReport", index=False)

    rows = load_phsm_history(workbook)

    assert len(rows) == 3
    assert {row["SourceID"] for row in rows} == {
        DATACENTER_SOURCE_ID,
        MONTHLY_REPORT_SOURCE_ID,
    }
    assert {row["GeographyKey"] for row in rows} == {"country:CN-BJ:national"}
    assert sorted(row["Cases"] for row in rows) == [0, 1, 2]
    assert all(row["GeographyKey"] != "country:CN:national" for row in rows)


def test_phsm_history_audit_accounts_for_totals_blanks_and_duplicates(tmp_path) -> None:
    workbook = tmp_path / "history-audit.xlsx"
    rows = pd.DataFrame(
        [
            {"year": 2023, "month": 1, "disease_cn": "猴痘", "disease_en": "Monkey pox", "province": "Beijing", "value": 1, "url": "https://example.test/1"},
            {"year": 2023, "month": 1, "disease_cn": "猴痘", "disease_en": "Monkey pox", "province": "Beijing", "value": 1, "url": "https://example.test/1"},
            {"year": 2023, "month": 2, "disease_cn": "猴痘", "disease_en": "Monkey pox", "province": "Beijing", "value": None, "url": "https://example.test/2"},
            {"year": 2023, "month": 1, "disease_cn": "甲乙丙类合计", "disease_en": "Total", "province": "Beijing", "value": 99, "url": "https://example.test/3"},
        ]
    )
    with pd.ExcelWriter(workbook) as writer:
        rows.to_excel(writer, sheet_name="ProvinceReport", index=False)

    loaded = load_phsm_history_with_audit(
        workbook, include_datacenter=False
    )

    assert len(loaded.rows) == 1
    assert loaded.audit.as_dict() == {
        "source_rows": 4,
        "imported_rows": 1,
        "duplicate_rows": 1,
        "blank_value_rows": 1,
        "total_rows": 1,
        "unmapped_disease_rows": 0,
        "unmapped_disease_labels": [],
        "unmapped_province_rows": 0,
        "unmapped_province_labels": [],
    }


def test_phsm_history_fails_closed_on_new_disease_label(tmp_path) -> None:
    workbook = tmp_path / "unmapped.xlsx"
    rows = pd.DataFrame(
        [{"year": 2023, "month": 1, "disease_cn": "新增病种", "disease_en": None, "province": "Beijing", "value": 1, "url": "https://example.test/"}]
    )
    with pd.ExcelWriter(workbook) as writer:
        rows.to_excel(writer, sheet_name="ProvinceReport", index=False)

    with pytest.raises(ValueError, match="新增病种"):
        load_phsm_history(workbook, include_datacenter=False)


def test_datacenter_spreadsheet_parser_preserves_explicit_zero() -> None:
    xml = b'''<?xml version="1.0" encoding="UTF-8"?>
    <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
      <Worksheet><Table>
        <Row><Cell><Data></Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data></Data></Cell><Cell><Data>\xe7\x94\xb2\xe8\x82\x9d</Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data>\xe5\x9c\xb0\xe5\x8c\xba</Data></Cell><Cell><Data>\xe5\x8f\x91\xe7\x97\x85\xe6\x95\xb0</Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data>\xe5\x85\xa8\xe5\x9b\xbd</Data></Cell><Cell><Data>8</Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data>\xe5\x8c\x97\xe4\xba\xac\xe5\xb8\x82</Data></Cell><Cell><Data>0</Data></Cell></Row>
      </Table></Worksheet>
    </Workbook>'''

    rows = parse_datacenter_spreadsheet(
        xml, report_date=date(2020, 1, 1), source_url="https://center.example/"
    )

    assert len(rows) == 1
    assert rows[0]["Cases"] == 0
    assert rows[0]["GeographyKey"] == "country:CN-BJ:national"


def test_datacenter_parser_uses_audited_disease_id_fallback_for_2021() -> None:
    xml = b'''<?xml version="1.0" encoding="UTF-8"?>
    <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
      <Worksheet><Table>
        <Row><Cell><Data></Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data></Data></Cell><Cell><Data></Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data>\xe5\x9c\xb0\xe5\x8c\xba</Data></Cell><Cell><Data>\xe5\x8f\x91\xe7\x97\x85\xe6\x95\xb0</Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data>\xe5\x85\xa8\xe5\x9b\xbd</Data></Cell><Cell><Data>8</Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data>\xe5\x8c\x97\xe4\xba\xac\xe5\xb8\x82</Data></Cell><Cell><Data>3</Data></Cell></Row>
      </Table></Worksheet>
    </Workbook>'''

    rows = parse_datacenter_spreadsheet(
        xml,
        report_date=date(2021, 1, 1),
        source_url="https://center.example/?diseaseId=10",
        fallback_disease_label="痢疾",
    )

    assert len(rows) == 1
    assert rows[0]["Cases"] == 3
    assert rows[0]["SourceDiseaseCode"] == "dysentery"
    assert rows[0]["RawDiseaseLabel"] == "痢疾"


def test_datacenter_fallback_refuses_unlabelled_province_rows() -> None:
    xml = b'''<?xml version="1.0" encoding="UTF-8"?>
    <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
      <Worksheet><Table>
        <Row><Cell><Data></Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data></Data></Cell><Cell><Data></Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data>\xe5\x9c\xb0\xe5\x8c\xba</Data></Cell><Cell><Data>\xe5\x8f\x91\xe7\x97\x85\xe6\x95\xb0</Data></Cell></Row>
        <Row><Cell><Data></Data></Cell><Cell><Data></Data></Cell><Cell><Data>3</Data></Cell></Row>
      </Table></Worksheet>
    </Workbook>'''

    rows = parse_datacenter_spreadsheet(
        xml,
        report_date=date(2022, 1, 1),
        source_url="https://center.example/?diseaseId=10",
        fallback_disease_label="痢疾",
    )

    assert rows == []


def test_datacenter_retries_transient_upstream_error(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.HTTPError(f"status {self.status_code}")

    class FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.responses = [FakeResponse(503), FakeResponse(200)]

        def get(self, *_args, **_kwargs):
            return self.responses.pop(0)

    session = FakeSession()
    crawler = ProvinceDataCenterCrawler(session=session, max_retries=1)
    monkeypatch.setattr("src.data.crawlers.cn_provinces.time.sleep", lambda _delay: None)

    response = crawler._get_with_retry("https://example.test/report", session=session)

    assert response.status_code == 200
    assert session.responses == []


def test_datacenter_parallelism_is_bounded() -> None:
    with pytest.raises(ValueError, match="between 1 and 16"):
        ProvinceDataCenterCrawler(max_workers=17)


def test_html_table_parser_promotes_embedded_header_and_ignores_footer() -> None:
    table = pd.DataFrame(
        [
            ["2026年7月统计表", "2026年7月统计表", "2026年7月统计表"],
            ["病名", "发病数", "死亡数"],
            ["甲肝", "0", "0"],
            ["说明", "*统计口径调整", None],
        ]
    )

    assert _normalize_report_table(table) == [
        {
            "label": "甲肝",
            "cases": 0,
            "source_row": {"病名": "甲肝", "发病数": "0", "死亡数": "0"},
        }
    ]


def test_report_table_stops_at_repeated_hidden_revision_header() -> None:
    table = pd.DataFrame(
        [
            ["甲肝", "3", "0"],
            ["病名", "发病数", "死亡数"],
            ["甲肝", "999", "0"],
        ],
        columns=["病名", "发病数", "死亡数"],
    )

    assert _normalize_report_table(table) == [
        {
            "label": "甲肝",
            "cases": 3,
            "source_row": {"病名": "甲肝", "发病数": "3", "死亡数": "0"},
        }
    ]


def test_report_disease_resolver_handles_word_footnotes_and_short_corruption() -> None:
    assert _resolve_report_disease("肺结核87")["code"] == "tuberculosis"
    assert _resolve_report_disease("其他感染性腹泻病他染")["code"] == "infectious_diarrhea"
    assert _resolve_report_disease("艾滋病**")["code"] == "aids"
    assert _resolve_report_disease("其中:（1）甲肝")["code"] == "hepatitis_a"


def test_shanghai_spreadsheet_header_uses_total_as_case_count() -> None:
    table = pd.DataFrame(
        [
            ["2025年12月上海市法定传染病报告发病数统计表", None, None, None],
            ["病名", "本市户籍", "非本市户籍", "合计"],
            ["其中:（1）甲肝", 2, 1, 3],
        ],
        columns=["附录", "Unnamed: 1", "Unnamed: 2", "Unnamed: 3"],
    )

    assert _normalize_report_table(table) == [
        {
            "label": "其中:（1）甲肝",
            "cases": 3,
            "source_row": {
                "病名": "其中:（1）甲肝",
                "本市户籍": 2,
                "非本市户籍": 1,
                "合计": 3,
            },
        }
    ]


def test_antiword_pipe_table_parser() -> None:
    tables = _antiword_tables(
        "报告标题\n|病名|发病数|死亡数|\n|甲肝|2|0|\n|肺结核|5|0|\n说明"
    )

    assert len(tables) == 1
    assert _normalize_report_table(tables[0]) == [
        {
            "label": "甲肝",
            "cases": 2,
            "source_row": {"病名": "甲肝", "发病数": "2", "死亡数": "0"},
        },
        {
            "label": "肺结核",
            "cases": 5,
            "source_row": {"病名": "肺结核", "发病数": "5", "死亡数": "0"},
        },
    ]


def test_attachment_suffix_supports_download_query_filename() -> None:
    assert _attachment_suffix("/module/downfile.jsp?filename=report.doc") == ".doc"


def test_both_sources_resolve_to_distinct_registered_series() -> None:
    ontology = load_disease_ontology()
    store = SeriesObservationStore(ontology)
    base = {
        "Date": "2020-01-01",
        "RawDiseaseLabel": "甲肝",
        "SourceDiseaseCode": "hepatitis_a",
        "Cases": 3,
        "GeographyKey": "country:CN-BJ:national",
    }

    center = store.build_observations(
        [{**base, "DefinitionVersion": "CN_PROVINCE_DATACENTER_ONSET_V1"}],
        "CN",
        source_id=DATACENTER_SOURCE_ID,
    )
    report = store.build_observations(
        [{**base, "DefinitionVersion": "CN_PROVINCE_MONTHLY_REPORT_V1"}],
        "CN",
        source_id=MONTHLY_REPORT_SOURCE_ID,
    )

    assert center.observations[0]["series_code"] == "SER_CN_PROV_DC_HEPATITIS_A"
    assert report.observations[0]["series_code"] == "SER_CN_PROV_REPORT_HEPATITIS_A"


def test_all_49_province_diseases_have_both_registered_source_series() -> None:
    ontology = load_disease_ontology()

    center = ontology.series_lookup(source_id=DATACENTER_SOURCE_ID)
    report = ontology.series_lookup(source_id=MONTHLY_REPORT_SOURCE_ID)

    assert len(center) == 49
    assert len(report) == 49
    assert {item["local_codes"][0] for item in center} == {
        item["local_codes"][0] for item in report
    }
    viral_hepatitis = next(
        item for item in center if item["local_codes"] == ["viral_hepatitis"]
    )
    assert viral_hepatitis["aggregation_policy"] == "reported_aggregate"
    assert viral_hepatitis["rollup_policy"] == "no_auto_rollup"


@pytest.mark.asyncio
async def test_updater_rejects_cn_national_geography() -> None:
    updater = CNProvinceUpdater()

    with pytest.raises(ValueError, match="must not use the CN national geography"):
        await updater.import_rows(
            object(),
            [{"Date": "2020-01-01", "SourceID": DATACENTER_SOURCE_ID, "GeographyKey": "country:CN:national"}],
        )
