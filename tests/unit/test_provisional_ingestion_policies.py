from __future__ import annotations

from datetime import date

from src.data.processors.au import AUMonthlyUpdater
from src.data.processors.ch import CHMonthlyUpdater
from src.data.processors.hk import HKMonthlyUpdater
from src.data.processors.jp import JPWeeklyUpdater
from src.data.processors.kr import KRMonthlyUpdater
from src.data.processors.nz import NZMonthlyUpdater
from src.data.processors.tw import TWMonthlyUpdater


def test_au_marks_only_the_open_month_provisional(tmp_path) -> None:
    today = date.today()
    prior_year = today.year - 1
    path = tmp_path / "au.csv"
    path.write_text(
        ",Disease,DiseaseFull,Group,Year,Month,Date,Cases,Population,Incidence\n"
        f"1,Influenza,Influenza,national_total,{prior_year},1,{prior_year}-01-01,4,,\n"
        f"2,Influenza,Influenza,national_total,{today.year},{today.month},"
        f"{today.year}-{today.month:02d}-01,5,,\n",
        encoding="utf-8",
    )

    rows = AUMonthlyUpdater(output_csv=path)._load_rows(path)

    assert [row["DatasetStatus"] for row in rows] == [
        "closed_revisable",
        "provisional",
    ]


def test_ch_uses_api_completeness_instead_of_country_level_guess(tmp_path) -> None:
    path = tmp_path / "ch.csv"
    path.write_text(
        ",Disease,DiseaseCode,Year,Month,ISOWeek,Date,PeriodType,PeriodValue,"
        "Cases,Geography,Group,DataComplete,Trend,SourceDate,Version,Source,SourceURL\n"
        "1,Influenza,influenza,2025,1,,2025-01-01,month,2025-01,4,CH,,TRUE,,,,FOPH,https://example.test\n"
        "2,Influenza,influenza,2025,2,,2025-02-01,month,2025-02,5,CH,,FALSE,,,,FOPH,https://example.test\n",
        encoding="utf-8",
    )

    rows = CHMonthlyUpdater(output_csv=path)._load_rows(path)

    assert [row["DatasetStatus"] for row in rows] == [
        "closed_revisable",
        "provisional",
    ]


def test_hk_marks_the_three_latest_published_months_provisional(tmp_path) -> None:
    path = tmp_path / "hk.csv"
    path.write_text(
        ",Disease,DiseaseCode,Year,Month,Date,Cases,AnnualTotal,RecordType,Source,SourceURL\n"
        "1,Cholera,,2026,1,2026-01-01,1,4,cases,CHP,https://example.test\n"
        "2,Cholera,,2026,2,2026-02-01,1,4,cases,CHP,https://example.test\n"
        "3,Cholera,,2026,3,2026-03-01,1,4,cases,CHP,https://example.test\n"
        "4,Cholera,,2026,4,2026-04-01,1,4,cases,CHP,https://example.test\n",
        encoding="utf-8",
    )

    rows = HKMonthlyUpdater(output_csv=path)._load_rows(path)

    assert [row["DatasetStatus"] for row in rows] == [
        "closed_revisable",
        "provisional",
        "provisional",
        "provisional",
    ]


def test_source_wide_provisional_feeds_are_explicit(tmp_path) -> None:
    jp_path = tmp_path / "jp.csv"
    jp_path.write_text(
        'Reporting Area,Current MMWR Year,MMWR WEEK,Disease,Current week,"Current week, flag"\n'
        "総数,2026,1,Influenza,4,\n",
        encoding="utf-8",
    )
    kr_path = tmp_path / "kr.csv"
    kr_path.write_text(
        ",Disease,DiseaseCode,DiseaseGroup,Year,Month,Date,Cases,LocalCases,ImportedCases,Source,SourceURL\n"
        "1,홍역,,2급,2026,1,2026-01-01,4,4,0,KDCA,https://example.test\n",
        encoding="utf-8",
    )
    nz_path = tmp_path / "nz.csv"
    nz_path.write_text(
        ",Disease,Year,Month,Date,Cases,CumulativeTotal,Rate,Source\n"
        "1,COVID-19,2026,1,2026-01-01,4,4,,PHF Science\n",
        encoding="utf-8",
    )

    jp = JPWeeklyUpdater(output_csv=jp_path)._load_standardized_rows(jp_path)[0]
    kr = KRMonthlyUpdater(output_csv=kr_path)._load_rows(kr_path)[0]
    nz = NZMonthlyUpdater(output_csv=nz_path)._load_rows(nz_path)[0]

    assert {jp["DatasetStatus"], kr["DatasetStatus"], nz["DatasetStatus"]} == {
        "provisional"
    }
    assert {jp["IsProvisional"], kr["IsProvisional"], nz["IsProvisional"]} == {
        "true"
    }
    assert {kr["RevisionSemantics"], nz["RevisionSemantics"]} == {
        "authoritative_revision"
    }
    assert {kr["AuthoritativeRevision"], nz["AuthoritativeRevision"]} == {"true"}


def test_tw_marks_open_month_provisional_and_closed_months_revisable(tmp_path) -> None:
    today = date.today()
    prior_year = today.year - 1
    path = tmp_path / "tw.csv"
    path.write_text(
        ",Disease,DiseaseCode,Year,Month,Date,Cases,LocalCases,ImportedCases,Source,SourceURL\n"
        f"1,登革熱,061,{prior_year},1,{prior_year}-01-01,4,4,0,NIDSS,https://example.test\n"
        f"2,登革熱,061,{today.year},{today.month},{today.year}-{today.month:02d}-01,5,5,0,NIDSS,https://example.test\n",
        encoding="utf-8",
    )

    rows = TWMonthlyUpdater(output_csv=path)._load_rows(path)

    assert [row["DatasetStatus"] for row in rows] == [
        "closed_revisable",
        "provisional",
    ]
