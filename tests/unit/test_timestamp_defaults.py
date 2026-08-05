from datetime import timezone

from src.domain.base import utc_now


def test_created_at_default_is_timezone_aware_utc():
    value = utc_now()

    assert value.tzinfo is not None
    assert value.utcoffset() == timezone.utc.utcoffset(value)
