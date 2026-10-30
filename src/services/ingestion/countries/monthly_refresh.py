"""Merge current source facts without mistaking a historical cache for a refresh."""
from datetime import date


def merge_monthly_refresh(live, prior, *, requested_months, country_code):
    requested = set(map(tuple, requested_months))

    def selected(row):
        stamp = date.fromisoformat(row["Date"])
        if str(row.get("Frequency") or "monthly").lower() != "monthly":
            raise ValueError(f"{country_code} monthly refresh cannot merge another frequency")
        if stamp.day != 1:
            raise ValueError(f"{country_code} monthly refresh requires month-start dates")
        return (stamp.year, stamp.month) in requested

    live = [row for row in live if selected(row)]
    prior = [row for row in prior if selected(row)]
    if not live:
        raise RuntimeError(f"{country_code} live refresh produced no usable rows; cached data is not a successful refresh")

    def identity(row):
        return (
            row["Date"], row.get("SourceDiseaseCode") or row.get("DiseaseCode") or row["RawDiseaseLabel"],
            row.get("GeographyKey") or f"country:{country_code}:national",
            row.get("Frequency") or "monthly", row.get("Measure") or "case_notifications",
            row.get("RecordType") or "cases",
        )

    merged = {identity(row): row for row in prior}
    live_keys = {identity(row) for row in live}
    merged.update({identity(row): row for row in live})
    fallback = []
    for key in merged.keys() - live_keys:
        row = {**merged[key], "SourceFreshness": "cache_fallback", "SourceDataDate": merged[key]["Date"], "SourceFreshnessReason": "No live result for this disease/month"}
        merged[key] = row
        fallback.append({"disease": row["RawDiseaseLabel"], "date": row["Date"]})
    covered = {(date.fromisoformat(row["Date"]).year, date.fromisoformat(row["Date"]).month) for row in live}
    missing = sorted(requested - covered)
    audit = {"status": "partial" if fallback or missing else "fresh", "live_rows": len(live_keys), "fallback_rows": len(fallback), "fallback_disease_dates": sorted(fallback, key=lambda row: (row["date"], row["disease"])), "missing_live_months": [f"{year:04d}-{month:02d}" for year, month in missing]}
    rows = sorted(merged.values(), key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
    return rows, audit
