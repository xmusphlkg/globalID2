from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.crawl_service import CrawlService


@pytest.fixture(autouse=True)
def _stub_shared_mutation_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_db) -> None:
        return None

    monkeypatch.setattr(
        "src.services.crawl_service.acquire_disease_data_mutation_lock",
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

    async def acquire_lock(session):
        calls.append(("lock", session))

    monkeypatch.setattr(
        "src.services.crawl_service.acquire_disease_data_mutation_lock",
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
    assert [call[0] for call in calls] == ["lock", "legacy", "series"]
    assert calls[0][1] is db
    assert calls[1][1:3] == calls[2][1:3] == (db, rows)
    assert calls[2][3] == "US"
    assert calls[2][4]["quality_policy"].mode == "quarantine"
    assert calls[2][4]["quality_policy"].registry_coverage == "required"
    assert calls[2][4]["source_id"] == {
        "US CDC NNDSS": "SRC_US_NNDSS",
        "US CDC NHSS": "SRC_US_NHSS",
    }
    assert calls[2][4]["geography_key"] == "country:US:national"


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
