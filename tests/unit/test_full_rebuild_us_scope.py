from __future__ import annotations

import pandas as pd
import pytest

from scripts.full_rebuild_database import validate_us_nndss_history_scope


def test_us_rebuild_accepts_resident_aliases_and_unrelated_nhss_total() -> None:
    frame = pd.DataFrame(
        [
            {"Source": "US CDC NNDSS", "ReportingArea": "US RESIDENTS"},
            {"Source": "US CDC NNDSS", "ReportingArea": "U.S. Residents"},
            {"Source": "US CDC NHSS", "ReportingArea": "TOTAL"},
        ]
    )

    validate_us_nndss_history_scope(frame)


def test_us_rebuild_rejects_nndss_total_projection() -> None:
    frame = pd.DataFrame(
        [{"Source": "US CDC NNDSS", "ReportingArea": "Total"}]
    )

    with pytest.raises(ValueError, match="non-resident reporting scopes"):
        validate_us_nndss_history_scope(frame)


def test_us_rebuild_requires_reporting_area_evidence() -> None:
    frame = pd.DataFrame([{"Source": "US CDC NNDSS", "Cases": 3}])

    with pytest.raises(ValueError, match="requires ReportingArea evidence"):
        validate_us_nndss_history_scope(frame)
