"""CDA CSV/annual sources preserve zero, current windows, and missing weeks."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.sg import (
    OPEN_DATA_POLL_URL,
    SingaporeCDACrawler,
    parse_annual_workbook,
    parse_historical_csv,
)
from src.data.processors.sg import SGWeeklyUpdater
from src.services.ingestion.countries import sg
from tests.contract.native import assert_native_observations_match

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/SG"


def artifact(raw, kind, metadata, url, index):
    path = Path("data/raw/sg/contract") / f"{index}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return SimpleNamespace(
        relative_path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_size=len(raw),
        kind=kind,
        metadata_=metadata,
        source_url=url,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize(
    "force,fill_missing", [(False, False), (True, False), (False, True)]
)
def test_sg_full_history_and_revision_contract(
    force, fill_missing, tmp_path, monkeypatch
):
    class Crawler(SingaporeCDACrawler):
        def _historical(self):
            return parse_historical_csv(
                (FIXTURES / "history.csv").read_bytes(), source_url=OPEN_DATA_POLL_URL
            )

        def _year(self, year):
            return parse_annual_workbook(
                (FIXTURES / f"{year}.xlsx").read_bytes(),
                year=year,
                source_url=f"https://example.org/{year}.xlsx",
            ), 2

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "sg.csv"
    output.write_text("Date,SourceDiseaseCode,Cases,Year\n2015-01-04,cholera,2,2015\n")
    artifacts = [artifact(output.read_bytes(), "history_csv", {}, str(output), 0)]
    full = force or fill_missing
    if full:
        artifacts.append(
            artifact(
                (FIXTURES / "history.csv").read_bytes(),
                "historical_csv",
                {},
                OPEN_DATA_POLL_URL,
                1,
            )
        )
    for year in range(2023 if full else 2025, 2027):
        artifacts.append(
            artifact(
                (FIXTURES / f"{year}.xlsx").read_bytes(),
                "cda_xlsx",
                {"year": year},
                f"https://example.org/{year}.xlsx",
                year,
            )
        )
    expected = SGWeeklyUpdater(output_csv=output, crawler_type=Crawler).refresh_source(
        source="cda", force=force, fill_missing=fill_missing, start_year=2012
    )
    monkeypatch.setattr(
        SingaporeCDACrawler,
        "get",
        lambda *a, **kw: pytest.fail("organize requested source"),
    )
    actual = sg.organize(
        artifacts,
        updater=SGWeeklyUpdater(output_csv=output),
        source="cda",
        force=force,
        fill_missing=fill_missing,
        start_year=2012,
    )

    def stable(rows):
        return [{k: v for k, v in row.items() if k != "RetrievedAt"} for row in rows]

    assert stable(actual.rows) == stable(expected.rows)
    assert_native_observations_match(actual.rows, expected.rows, SGWeeklyUpdater())
    assert actual.source_latest_date == expected.source_latest_date
    assert any(row["Cases"] == "0" for row in actual.rows)
    assert not any(row.get("Week") == "3" for row in actual.rows)
    assert not any(
        "respiratory infections" in row.get("RawDiseaseLabel", "").lower()
        for row in actual.rows
    )


async def test_sg_collect_does_not_parse_business_rows(monkeypatch):

    def discover(self, year):
        return f"https://example.org/{year}.xlsx", [], b"<html></html>"

    def get(self, url):
        return SimpleNamespace(
            content=(FIXTURES / f"{url.rsplit('/', 1)[1]}").read_bytes(), url=url
        )

    monkeypatch.setattr(SingaporeCDACrawler, "discover_year_assets", discover)
    monkeypatch.setattr(SingaporeCDACrawler, "get", get)
    monkeypatch.setattr(
        sg,
        "parse_annual_workbook",
        lambda *a, **kw: pytest.fail("collect parsed business rows"),
    )

    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, raw, **kwargs):
            self.artifacts.append((raw, kwargs))

    result = await sg.collect(Writer(), source="cda")
    assert len(result) == 4
    assert sum(metadata["kind"] == "cda_xlsx" for _, metadata in result) == 2


def test_cda_2023_pdf_fallback_is_offline_and_requires_complete_capture(
    tmp_path, monkeypatch
):
    from src.data.crawlers import sg as crawler_module

    class Page:
        def extract_tables(self):
            return [
                [
                    ["Acute Hepatitis A", "0"],
                    ["Dengue Haemorrhagic Fever", "2"],
                    ["POLYCLINIC ATTENDANCES - AVERAGE DAILY NUMBER", None],
                    ["Hand, Foot And Mouth Disease", "999"],
                ]
            ]

    class Document:
        def __init__(self):
            self.pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(crawler_module.pdfplumber, "open", lambda _: Document())
    monkeypatch.chdir(tmp_path)
    captures = [
        artifact(
            b"broken workbook",
            "cda_xlsx",
            {"year": 2023},
            "https://example.org/2023.xlsx",
            1,
        ),
        artifact(
            b"<html/>",
            "html_report",
            {"year": 2023, "pdf_weeks": [30]},
            "https://example.org/2023",
            2,
        ),
        artifact(
            b"captured pdf",
            "cda_pdf",
            {"year": 2023, "week": 30},
            "https://example.org/w30.pdf",
            3,
        ),
    ]
    monkeypatch.setattr(
        SingaporeCDACrawler,
        "get",
        lambda *a, **kw: pytest.fail("organize requested source"),
    )
    actual = sg.organize(
        captures, updater=SGWeeklyUpdater(), source="cda", force=True, start_year=2023
    )
    assert [(row["SourceDiseaseCode"], row["Cases"]) for row in actual.rows] == [
        ("acute_viral_hepatitis_a", "0"),
        ("dengue_haemorrhagic_fever", "2"),
    ]
    captures[1].metadata_["pdf_weeks"] = [30, 31]
    with pytest.raises(
        crawler_module.SGContractError, match="Incomplete CDA PDF fallback"
    ):
        sg.organize(
            captures,
            updater=SGWeeklyUpdater(),
            source="cda",
            force=True,
            start_year=2023,
        )
