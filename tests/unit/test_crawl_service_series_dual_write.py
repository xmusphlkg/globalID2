from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.crawl_service import CrawlService
from src.data.storage.series_observation_store import SeriesObservationSaveResult


@pytest.fixture(autouse=True)
def _stub_shared_mutation_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_db, _country_code) -> None:
        return None

    monkeypatch.setattr(
        "src.services.crawl_service.acquire_country_data_mutation_lock",
        _noop,
    )


def test_series_quality_policy_supports_country_env_override(monkeypatch) -> None:
    updater = SimpleNamespace(
        country_code="KR",
        series_quality_guard={
            "mode": "report",
            "minimum_cross_series": "8",
        },
    )
    monkeypatch.setenv("GLOBALID_SERIES_QUALITY_MODE", "fail_closed")
    monkeypatch.setenv("GLOBALID_KR_SERIES_QUALITY_MODE", "quarantine")

    policy = CrawlService._series_quality_policy(updater)

    assert policy.mode == "quarantine"
    assert policy.minimum_cross_series == 8


@pytest.mark.asyncio
async def test_import_rows_dual_writes_in_the_same_session(monkeypatch) -> None:
    calls: list[tuple] = []
    db = object()
    rows = [{"Date": "2024-12-31", "Cases": "1"}]
    import_result = SimpleNamespace(inserted_or_updated=1)

    class Updater:
        country_code = "US"

        async def import_rows(self, session, source_rows, **kwargs):
            calls.append(("legacy", session, source_rows, kwargs))
            return import_result

    class Store:
        async def save_rows(self, session, source_rows, country_code, **kwargs):
            calls.append(("series", session, source_rows, country_code, kwargs))
            return SimpleNamespace(
                upserted=1,
                skipped_unmatched=0,
                skipped_ambiguous=0,
                skipped_invalid=0,
                skipped_registry_not_synced=0,
                quality_report=SimpleNamespace(
                    issues=(), highest_severity=None, to_dict=lambda: {"issues": []}
                ),
            )

    async def acquire_lock(session, country_code):
        calls.append(("lock", session, country_code))

    monkeypatch.setattr(
        "src.services.crawl_service.acquire_country_data_mutation_lock",
        acquire_lock,
    )
    monkeypatch.setattr(
        "src.services.crawl_service.SeriesObservationStore", lambda: Store()
    )

    result = await CrawlService._import_rows_with_series(
        db,
        Updater(),
        rows,
        db_latest_date=None,
        source_latest_date=None,
        force=True,
    )

    assert result is import_result
    assert [call[0] for call in calls] == ["lock", "legacy", "lock", "series"]
    assert calls[0][1:] == calls[2][1:] == (db, "US")
    assert calls[1][1:3] == calls[3][1:3] == (db, rows)
    assert calls[3][3] == "US"
    assert calls[3][4]["quality_policy"].mode == "quarantine"
    assert calls[3][4]["quality_policy"].registry_coverage == "required"
    assert calls[3][4]["source_id"] == {
        "US CDC NNDSS": "SRC_US_NNDSS",
        "US CDC NHSS": "SRC_US_NHSS",
    }
    assert calls[3][4]["geography_key"] == "country:US:national"


@pytest.mark.asyncio
async def test_import_rows_can_use_distinct_row_scopes_for_series(monkeypatch) -> None:
    calls: list[tuple] = []
    db = object()
    legacy_rows = [{"ReportingArea": "TOTAL", "Cases": "3"}]
    series_rows = [
        {"ReportingArea": "TOTAL", "Cases": "3"},
        {"ReportingArea": "US RESIDENTS", "Cases": "2"},
    ]
    import_result = SimpleNamespace(inserted_or_updated=1)

    class Updater:
        country_code = "US"
        series_geography_from_rows = True

        async def import_rows(self, session, source_rows, **kwargs):
            calls.append(("legacy", session, source_rows, kwargs))
            return import_result

    class Store:
        async def save_rows(self, session, source_rows, country_code, **kwargs):
            calls.append(("series", session, source_rows, country_code, kwargs))
            return SimpleNamespace(
                upserted=2,
                skipped_unmatched=0,
                skipped_ambiguous=0,
                skipped_invalid=0,
                skipped_registry_not_synced=0,
                quality_report=SimpleNamespace(
                    issues=(), highest_severity=None, to_dict=lambda: {"issues": []}
                ),
            )

    monkeypatch.setattr(
        "src.services.crawl_service.SeriesObservationStore", lambda: Store()
    )

    result = await CrawlService._import_rows_with_series(
        db,
        Updater(),
        legacy_rows,
        series_rows=series_rows,
        db_latest_date=None,
        source_latest_date=None,
        force=True,
    )

    assert result is import_result
    assert calls[0][2] is legacy_rows
    assert calls[1][2] is series_rows
    assert calls[1][4]["geography_key"] is None


@pytest.mark.asyncio
async def test_us_registered_only_selection_requires_resident_and_total_scopes(
    monkeypatch,
) -> None:
    db = object()
    legacy_rows = [{"Source": "US CDC NNDSS", "Cases": "2"}]
    series_rows = [
        {
            "Source": "US CDC NNDSS",
            "ReportingArea": "US RESIDENTS",
            "Cases": "2",
        },
        {
            "Source": "US CDC NNDSS",
            "ReportingArea": "TOTAL",
            "Cases": "3",
        },
        {
            "Source": "US CDC NNDSS",
            "ReportingArea": "TOTAL",
            "Cases": "",
        },
    ]
    saved_rows = []

    class Updater:
        country_code = "US"
        series_geography_from_rows = True
        series_registered_rows_only = True

        async def import_rows(self, *_args, **_kwargs):
            return SimpleNamespace(inserted_or_updated=1)

    class Store:
        def select_registry_rows(self, rows, *_args, **_kwargs):
            return SimpleNamespace(
                rows=rows[:2], skipped_unregistered=0, skipped_missing=1
            )

        async def save_rows(self, _db, rows, *_args, **_kwargs):
            saved_rows.extend(rows)
            return SimpleNamespace(
                upserted=2,
                skipped_unmatched=0,
                skipped_ambiguous=0,
                skipped_invalid=0,
                skipped_registry_not_synced=0,
                quality_report=SimpleNamespace(
                    issues=(), highest_severity=None, to_dict=lambda: {"issues": []}
                ),
            )

    monkeypatch.setattr(
        "src.services.crawl_service.SeriesObservationStore", lambda: Store()
    )

    await CrawlService._import_rows_with_series(
        db,
        Updater(),
        legacy_rows,
        series_rows=series_rows,
        db_latest_date=None,
        source_latest_date=None,
        force=True,
    )

    assert saved_rows == series_rows[:2]


@pytest.mark.asyncio
async def test_series_only_save_surfaces_registry_prefilter_counts(monkeypatch) -> None:
    rows = [{"Cases": "2"}, {"Cases": ""}, {"Cases": "4"}]

    class Updater:
        country_code = "CA-ON"
        ontology_source_id = "SRC_CA_ON_PHO_IDTO"
        series_geography_key = "country:CA-ON:national"
        series_registered_rows_only = True

    class Store:
        def select_registry_rows(self, source_rows, *_args, **_kwargs):
            return SimpleNamespace(
                rows=source_rows[:1],
                skipped_unregistered=1,
                skipped_missing=1,
            )

        async def save_rows(self, *_args, **_kwargs):
            return SimpleNamespace(
                upserted=1,
                skipped_unmatched=0,
                skipped_ambiguous=0,
                skipped_invalid=0,
                skipped_registry_not_synced=0,
                quality_report=SimpleNamespace(
                    issues=(), highest_severity=None, to_dict=lambda: {"issues": []}
                ),
            )

    monkeypatch.setattr(
        "src.services.crawl_service.SeriesObservationStore", lambda: Store()
    )

    result = await CrawlService._save_series_rows(object(), Updater(), rows)

    assert result.upserted == 1
    assert result.skipped_unregistered == 1
    assert result.skipped_missing == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("with_weeks,expected", [(False, [10000, 10000, 1]), (True, [12000, 8001])])
async def test_japan_series_write_is_bounded_and_preserves_all_rows(monkeypatch, with_weeks, expected):
    batches = []

    class Store:
        def _jp_mixed_holding_profiles(self, rows, *_args):
            assert len(rows) == 20001
            return {("identity",): {"all_case": {}, "sentinel": {}}}

        async def save_rows(self, db, rows, *_args, **_kwargs):
            batches.append((db, list(rows)))
            assert ("identity",) in _kwargs["jp_holding_profiles"]
            return SeriesObservationSaveResult(
                upserted=len(rows), skipped_unmatched=0, skipped_ambiguous=0,
                skipped_invalid=0, skipped_registry_not_synced=0,
                quality_report=SimpleNamespace(issues=(), highest_severity=None),
            )

    monkeypatch.setattr("src.services.crawl_service.SeriesObservationStore", Store)
    db = object()
    rows = [{"n": n, **({"MMWRYear": "2026", "MMWRWeek": str(1 if n < 6000 else 2 if n < 12000 else 3)} if with_weeks else {})} for n in range(20001)]
    result = await CrawlService._save_series_rows(
        db, SimpleNamespace(country_code="JP", series_country_code="JP"), rows,
    )

    assert [len(batch) for _, batch in batches] == expected
    assert all(session is db for session, _ in batches)
    assert [row for _, batch in batches for row in batch] == rows
    assert result.upserted == len(rows)


@pytest.mark.asyncio
async def test_us_registered_only_selection_blocks_missing_resident_scope(
    monkeypatch,
) -> None:
    class Updater:
        country_code = "US"
        series_geography_from_rows = True
        series_registered_rows_only = True

        async def import_rows(self, *_args, **_kwargs):
            return SimpleNamespace(inserted_or_updated=1)

    only_total = [
        {
            "Source": "US CDC NNDSS",
            "ReportingArea": "TOTAL",
            "Cases": "3",
        }
    ]

    class Store:
        def select_registry_rows(self, rows, *_args, **_kwargs):
            return SimpleNamespace(
                rows=rows, skipped_unregistered=0, skipped_missing=0
            )

    monkeypatch.setattr(
        "src.services.crawl_service.SeriesObservationStore", lambda: Store()
    )

    with pytest.raises(ValueError, match="US_RESIDENTS"):
        await CrawlService._import_rows_with_series(
            object(),
            Updater(),
            only_total,
            series_rows=only_total,
            db_latest_date=None,
            source_latest_date=None,
            force=True,
        )


@pytest.mark.parametrize("deaths", ["7", "", None])
async def test_legacy_supplemental_write_cannot_be_declared_case_series_noop(monkeypatch, deaths):
    async def save(*_args, **_kwargs):
        return SeriesObservationSaveResult(
            upserted=1, skipped_unmatched=0, skipped_ambiguous=0,
            skipped_invalid=0, skipped_registry_not_synced=0,
            quality_report=SimpleNamespace(), publication_changes=(),
        )

    class Updater:
        country_code = "US"

        async def import_rows(self, *_args, **_kwargs):
            return SimpleNamespace(inserted_or_updated=1)

    monkeypatch.setattr(CrawlService, "_save_series_rows", save)
    results = []
    await CrawlService._import_rows_with_series(
        object(), Updater(), [{"Cases": "10", "Deaths": deaths}],
        db_latest_date=None, source_latest_date=None, force=True,
        publication_results=results,
    )
    assert results[0].publication_changes is None


def test_legacy_only_updater_can_explicitly_disable_registry_requirement() -> None:
    updater = SimpleNamespace(
        country_code="US",
        series_registry_coverage="legacy_only",
    )

    policy = CrawlService._series_quality_policy(updater)

    assert policy.registry_coverage == "legacy_only"


def test_partially_registered_country_updaters_select_registry_rows() -> None:
    from src.data.processors.au import AUMonthlyUpdater
    from src.data.processors.br import BRMonthlyUpdater
    from src.data.processors.ch import CHMonthlyUpdater
    from src.data.processors.hk import HKMonthlyUpdater
    from src.data.processors.jp import JPWeeklyUpdater
    from src.data.processors.nz import NZMonthlyUpdater
    from src.data.processors.tw import TWMonthlyUpdater

    updater_types = (
        AUMonthlyUpdater,
        BRMonthlyUpdater,
        CHMonthlyUpdater,
        HKMonthlyUpdater,
        JPWeeklyUpdater,
        NZMonthlyUpdater,
        TWMonthlyUpdater,
    )

    assert all(cls.series_registered_rows_only for cls in updater_types)


def test_crawl_service_supports_australia_state_and_territory_codes() -> None:
    supported = set(CrawlService.supported_country_codes())

    assert {
        "AU-ACT",
        "AU-NSW",
        "AU-NT",
        "AU-QLD",
        "AU-SA",
        "AU-TAS",
        "AU-VIC",
        "AU-WA",
    }.issubset(supported)
    updater = CrawlService._make_updater("AUMonthlyUpdater", "AU-NSW")
    assert updater.country_code == "AU-NSW"
    assert updater.mapping_country_code == "AU"
    assert updater.series_geography_key == "country:AU-NSW:national"


@pytest.mark.asyncio
async def test_australia_subdivision_reuses_parent_series_registry(monkeypatch) -> None:
    rows = [
        {
            "Date": "2026-03-01",
            "RawDiseaseLabel": "Anthrax",
            "Cases": "4",
            "GeographyKey": "country:AU-NSW:national",
        }
    ]
    calls = []

    class Store:
        def enrich_registry_identities(self, source_rows, country_code, **kwargs):
            calls.append(("enrich", country_code, kwargs))
            return source_rows

        def select_registry_rows(self, source_rows, country_code, **kwargs):
            calls.append(("select", country_code, kwargs))
            return SimpleNamespace(
                rows=source_rows,
                skipped_unregistered=0,
                skipped_missing=0,
                unregistered_rows=(),
            )

        async def save_rows(self, _db, source_rows, country_code, **kwargs):
            calls.append(("save", country_code, kwargs))
            return SimpleNamespace(
                upserted=1,
                skipped_unmatched=0,
                skipped_ambiguous=0,
                skipped_invalid=0,
                skipped_registry_not_synced=0,
                quality_report=SimpleNamespace(
                    issues=(), highest_severity=None, to_dict=lambda: {"issues": []}
                ),
            )

    monkeypatch.setattr(
        "src.services.crawl_service.SeriesObservationStore", lambda: Store()
    )

    updater = SimpleNamespace(
        country_code="AU-NSW",
        series_country_code="AU",
        ontology_source_id="SRC_AU_NINDSS",
        series_geography_key="country:AU-NSW:national",
        series_registered_rows_only=True,
    )

    result = await CrawlService._save_series_rows(object(), updater, rows)

    assert result.upserted == 1
    assert calls == [
        ("select", "AU", {"source_id": "SRC_AU_NINDSS"}),
        (
            "save",
            "AU",
            {
                "source_id": "SRC_AU_NINDSS",
                "geography_key": "country:AU-NSW:national",
                "quality_policy": CrawlService._series_quality_policy(updater),
            },
        ),
    ]
    assert calls[-1][2]["quality_policy"].registry_coverage == "required"


@pytest.mark.asyncio
async def test_series_write_failure_is_not_silently_ignored(monkeypatch) -> None:
    class Updater:
        country_code = "US"

        async def import_rows(self, *_args, **_kwargs):
            return SimpleNamespace(inserted_or_updated=1)

    class Store:
        async def save_rows(self, *_args, **_kwargs):
            raise RuntimeError("series write failed")

    monkeypatch.setattr(
        "src.services.crawl_service.SeriesObservationStore", lambda: Store()
    )

    with pytest.raises(RuntimeError, match="series write failed"):
        await CrawlService._import_rows_with_series(
            object(),
            Updater(),
            [],
            db_latest_date=None,
            source_latest_date=None,
            force=False,
        )
