from __future__ import annotations

import warnings

import pandas as pd

from src.generation.data_cleaner import long_to_wide


def test_monthly_aggregation_normalizes_aware_timestamps_to_utc_without_warning():
    frame = pd.DataFrame(
        {
            "time": [
                "2026-01-31T23:30:00-02:00",
                "2026-02-15T12:00:00+00:00",
            ],
            "cases": [2, 3],
        }
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        wide = long_to_wide(frame, "monthly")

    assert wide["_period_str"].tolist() == ["2026-02"]
    assert wide["cases"].tolist() == [5]
