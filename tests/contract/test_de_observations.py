"""RKI national and every state preserve their independent source geography."""

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.de import GERMAN_STATES, GermanySurvStatCrawler
from src.data.processors.de import DEWeeklyUpdater
from src.services.ingestion.countries import de
from tests.contract.native import assert_native_observations_match

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion"


def artifact(raw, kind, metadata, source_url, index):
    p = Path("data/raw/de/contract") / f"{index}.bin"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return SimpleNamespace(
        relative_path=str(p),
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_size=len(raw),
        kind=kind,
        metadata_=metadata,
        source_url=source_url,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize("country", ["DE", *sorted(GERMAN_STATES)])
@pytest.mark.parametrize("force", [False, True])
def test_rki_all_geography_contracts(country, force, tmp_path, monkeypatch):
    config = json.loads((FIXTURES / country / "rki.json").read_text())
    raw = (FIXTURES / "DE/rki.zip").read_bytes()
    url = "https://example.org/rki.zip"
    weeks = [date.fromisoformat(config["week"])]
    monkeypatch.chdir(tmp_path)

    class Crawler(GermanySurvStatCrawler):
        def _download_year(self, year, region=None):
            return raw, url

    output = tmp_path / "rki.csv"
    output.write_text(
        "Date,SourceDiseaseCode,Cases,GeographyKey\n2025-01-06,masern,4,country:"
        + country
        + ":national\n"
    )
    captured = [
        artifact(
            raw,
            "rki_zip",
            {"year": 2026, "region": None if country == "DE" else country},
            url,
            0,
        ),
        artifact(output.read_bytes(), "history_csv", {}, str(output), 1),
    ]
    expected = DEWeeklyUpdater(
        country_code=country, output_csv=output, crawler_type=Crawler
    ).refresh_source(source="rki", weeks=weeks, force=force)
    monkeypatch.setattr(
        GermanySurvStatCrawler,
        "_download_year",
        lambda *a, **kw: pytest.fail("organize requested source"),
    )
    actual = de.organize(
        captured,
        updater=DEWeeklyUpdater(country_code=country, output_csv=output),
        source="rki",
        weeks=weeks,
        force=force,
    )

    def stable(rows):
        return [{k: v for k, v in r.items() if k != "RetrievedAt"} for r in rows]

    assert stable(actual.rows) == stable(expected.rows)
    assert_native_observations_match(
        actual.rows, expected.rows, DEWeeklyUpdater(country_code=country)
    )
    assert actual.source_latest_date == expected.source_latest_date
    assert all(
        row["GeographyKey"] == f"country:{country}:national" for row in actual.rows
    )
    assert any(row["Cases"] == "0" for row in actual.rows)


async def test_rki_collect_saves_export_before_normalizing(monkeypatch):
    raw = (FIXTURES / "DE/rki.zip").read_bytes()
    monkeypatch.setattr(
        GermanySurvStatCrawler,
        "_download_year",
        lambda *a, **kw: (raw, "https://example.org/rki.zip"),
    )
    monkeypatch.setattr(
        de,
        "parse_survstat_zip",
        lambda *a, **kw: pytest.fail("collect parsed business rows"),
    )

    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, raw, **kwargs):
            self.artifacts.append((raw, kwargs))

    result = await de.collect(Writer(), source="rki", weeks=[date(2026, 7, 27)])
    assert result[0][0] == raw and result[0][1]["kind"] == "rki_zip"
