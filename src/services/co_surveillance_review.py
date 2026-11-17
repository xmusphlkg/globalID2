"""Fail-closed Colombia national weekly notification publication contract."""
from datetime import date, timedelta

TARGETS = {"217": "D052", "320": "D026", "420": "D082", "430": "D082",
           "450": "D042", "580": "D021", "895": "D051"}


def reviewed_co_bes_record(record):
    code = str(record.get("reviewed_source_code") or "").removeprefix("bes_weekly_")
    dims = record.get("dimensions")
    if (code not in TARGETS or record.get("disease_id") != TARGETS[code]
            or record.get("definition_version") != "co_bes_explicit_weekly_notifications_v1"
            or record.get("metric_type") != ("death_notifications" if code == "580" else "case_notifications")
            or record.get("reporting_basis") != "weekly_notifications_excluding_discarded"
            or record.get("temporal_granularity") != "weekly"
            or record.get("geography_key") != "country:CO:national"
            or not isinstance(dims, dict)
            or set(dims) != {"source_epi_year", "source_epi_week", "calendar"}
            or dims["calendar"] != "INS_source_epi_week"
            or type(dims["source_epi_year"]) is not int
            or type(dims["source_epi_week"]) is not int
            or not 1 <= dims["source_epi_week"] <= 53):
        return None
    try:
        start = date.fromisoformat(str(record["period_start"]))
        end = date.fromisoformat(str(record["period_end_exclusive"]))
    except (KeyError, ValueError, TypeError):
        return None
    if end - start != timedelta(days=7) or start.weekday() != 6:
        return None
    return {**record, "projection_dimension_key": "all", "observation_kind": "interval_count"}
