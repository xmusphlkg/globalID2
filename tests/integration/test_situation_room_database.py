from __future__ import annotations

from datetime import date

import pytest

from src.services.situation_room import fetch_series_frame, load_config
from src.services.situation_statistics import evaluate_frame


@pytest.mark.asyncio
async def test_current_registry_enumerations_produce_nonzero_analysis() -> None:
    config = load_config()
    frame = await fetch_series_frame(config)

    assert not frame.empty
    assert "case_notifications" in set(frame["metric_type"])
    assert "non_additive" in set(frame["aggregation_policy"])

    assessments, rejected = evaluate_frame(frame, config, as_of=date(2026, 8, 13))
    assert len(assessments) > 0, rejected
