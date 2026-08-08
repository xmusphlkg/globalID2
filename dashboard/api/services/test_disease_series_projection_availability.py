from sqlalchemy.dialects import postgresql

from dashboard.api.services.disease_series_projection import (
    UNREADABLE_SERIES_AVAILABILITY_STATUSES,
    _readable_series_availability_clause,
)


def test_historical_series_remain_readable_without_is_active_filter() -> None:
    assert "historical" not in UNREADABLE_SERIES_AVAILABILITY_STATUSES

    compiled = str(
        _readable_series_availability_clause().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "availability_status NOT IN" in compiled
    assert "is_active" not in compiled
    assert "'discontinued'" in compiled
    assert "'not_available'" in compiled
