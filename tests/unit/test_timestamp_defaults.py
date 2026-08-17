from datetime import timezone

from src.domain.base import TimestampMixin, utc_now


def test_created_at_default_is_timezone_aware_utc():
    value = utc_now()

    assert value.tzinfo is not None
    assert value.utcoffset() == timezone.utc.utcoffset(value)


def test_shared_timestamp_columns_declare_timezone_aware_defaults():
    assert TimestampMixin.created_at.type.timezone is True
    assert TimestampMixin.updated_at.type.timezone is True
