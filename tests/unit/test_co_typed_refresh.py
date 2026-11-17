"""Guard the source break that previously made Colombia curves misleading."""
from datetime import date

from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.co_surveillance_review import reviewed_co_bes_record


def test_annex_label_cannot_resolve_to_weekly_body_contract():
    store = SeriesObservationStore()
    label = "Leishmaniasis cutánea"
    assert store._resolve_series(country_code="CO", source_id="SRC_CO_INS_BES",
                                 local_code="bes_leishmaniasis_cutanea", local_label=label) == []
    weekly = store._resolve_series(country_code="CO", source_id="SRC_CO_INS_BES",
                                   local_code="bes_weekly_420", local_label=label)
    assert len(weekly) == 1
    assert weekly[0]["measure"] == "case_notifications"


def test_week_53_keeps_source_year_and_exact_cross_year_boundaries():
    record = {"reviewed_source_code": "bes_weekly_420", "disease_id": "D082",
        "definition_version": "co_bes_explicit_weekly_notifications_v1", "metric_type": "case_notifications",
        "reporting_basis": "weekly_notifications_excluding_discarded", "temporal_granularity": "weekly",
        "geography_key": "country:CO:national", "date": date(2025, 12, 28),
        "period_start": "2025-12-28", "period_end_exclusive": "2026-01-04",
        "dimensions": {"source_epi_year": 2025, "source_epi_week": 53, "calendar": "INS_source_epi_week"}}
    accepted = reviewed_co_bes_record(record)
    assert accepted is not None
    assert accepted["dimensions"]["source_epi_year"] == 2025
    assert reviewed_co_bes_record({**record, "metric_type": "source_reported_ytd_event_count"}) is None
    assert reviewed_co_bes_record({**record, "period_end_exclusive": "2026-01-03"}) is None


def test_flunet_filter_includes_iso_week_one_starting_in_previous_year():
    from src.data.processors.who_flunet import WHOFluNetUpdater
    rows = [{"Date": "2024-12-30", "SourceYear": "2025"},
            {"Date": "2024-12-23", "SourceYear": "2024"}]
    assert WHOFluNetUpdater("CO").prepare_rows(rows, start_year=2025).rows == rows[:1]
