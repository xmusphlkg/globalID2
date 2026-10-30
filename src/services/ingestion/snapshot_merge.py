"""Merge already validated snapshots without replacing fresh facts with old rows."""
from __future__ import annotations


def merge_snapshot_rows(live, prior, *, dimensions=()):
    """Fresh natural keys win; absent keys retain explicit cached provenance.

    This is an upsert recovery policy, not evidence that an incomplete fetch is
    an authoritative full snapshot. Source-specific coverage/quality checks must
    run first; callers remain responsible for authoritative deletions.
    """
    codes_by_label = {
        str(row.get("RawDiseaseLabel") or ""): str(row["DiseaseCode"])
        for row in [*prior, *live] if row.get("DiseaseCode") and row.get("RawDiseaseLabel")
    }
    def identity(row):
        label = str(row.get("RawDiseaseLabel") or "")
        disease = str(row.get("DiseaseCode") or codes_by_label.get(label) or label)
        return (str(row.get("Date") or ""), disease,
                *(str(row.get(field) or "") for field in dimensions))

    def indexed(rows):
        result = {}
        for row in rows:
            key = identity(row)
            if not key[0] or not key[1]:
                raise ValueError("Snapshot row lacks a dated disease identity")
            if key in result and any(result[key].get(field) != row.get(field) for field in ("Cases", "LocalCases", "ImportedCases")):
                raise ValueError("Snapshot contains conflicting values for one natural key")
            result[key] = row
        return result

    old, current = indexed(prior), indexed(live)
    replaced = len(old.keys() & current.keys())
    retained = len(old.keys() - current.keys())
    audit = {
        "policy": "validated_live_keys_override_cached_keys",
        "live_rows": len(current), "prior_rows": len(old),
        "overwritten_cached_keys": replaced, "retained_cached_keys": retained,
    }
    if not live:
        return prior, audit
    if not prior:
        return live, audit
    return list({**old, **current}.values()), audit
