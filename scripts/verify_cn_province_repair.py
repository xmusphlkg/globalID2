#!/usr/bin/env python3
"""Independently verify a completed province repair with a read-only DB snapshot.

Reads before.json.gz, plan.json and apply_receipt.json, then writes after.json.gz
and verification.json. No observations, mappings, jobs or publication state are
changed. Exit 1 means at least one integrity check failed.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.repair_cn_province_data import DEFAULT_OUTPUT, read_json, write_json  # noqa: E402

FACTS_SQL = """
    SELECT o.*, s.source_system, s.source_label, s.metric_type
    FROM disease_series_observations o
    JOIN disease_surveillance_series s USING(series_code)
    WHERE o.geography_key LIKE 'country:CN-%:national'
      AND s.source_system='SRC_CN_PROV_MONTHLY_REPORT'
    ORDER BY o.geography_key,o.time,o.series_code,o.dimension_key
"""
PROTECTED_SQL = """
    SELECT o.geography_key,s.source_system,count(*) observations,
           sum(o.value) total_value,min(o.time) first_date,max(o.time) last_date
    FROM disease_series_observations o
    JOIN disease_surveillance_series s USING(series_code)
    WHERE o.geography_key='country:CN:national'
       OR (o.geography_key LIKE 'country:CN-%:national'
           AND s.source_system='SRC_CN_PROV_DATACENTER')
    GROUP BY 1,2 ORDER BY 1,2
"""
REVISIONS_SQL = """
    SELECT r.* FROM disease_series_observation_revisions r
    JOIN disease_surveillance_series s USING(series_code)
    WHERE r.geography_key LIKE 'country:CN-%:national'
      AND s.source_system='SRC_CN_PROV_MONTHLY_REPORT'
      AND r.series_code=ANY($1::varchar[])
    ORDER BY r.geography_key,r.time,r.series_code,r.dimension_key,r.recorded_at
"""
REVISION_FIELDS = ("unit", "suppressed", "suppression_reason", "quality_status", "dimensions", "raw_data", "metadata")


def fact_key(row):
    return (str(row["time"])[:10], row["series_code"], row["geography_key"], row.get("dimension_key", "all"))


def same_number(left, right):
    if left is None or right is None:
        return left is None and right is None
    return Decimal(str(left)) == Decimal(str(right))


def serializable(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def decode_record(record):
    result = dict(record)
    for field in ("raw_data", "metadata", "dimensions"):
        if isinstance(result.get(field), str):
            result[field] = json.loads(result[field])
    return result


def unchanged_fact(old, current):
    old, current = serializable(old), serializable(current)
    if not same_number(old.get("value"), current.get("value")):
        return False
    old.pop("value", None)
    current.pop("value", None)
    return old == current


def revision_preserves(old, revision):
    return (same_number(old.get("value"), revision.get("value"))
            and all(serializable(old.get(field)) == serializable(revision.get(field)) for field in REVISION_FIELDS))


def coverage(rows):
    """Count known months and metric observations without treating missing as zero."""
    metrics = defaultdict(list)
    for row in rows:
        metrics[row.get("metric_type") or "unknown"].append(row)
    measured = {}
    for metric, facts in sorted(metrics.items()):
        values = [row for row in facts if row.get("value") is not None]
        months = sorted({str(row["time"])[:7] for row in values if str(row["time"])[:7] >= "2022-01"})
        measured[metric] = {
            "observations": len(facts), "known_values": len(values),
            "unknown_values": len(facts) - len(values),
            "zero_values": sum(same_number(row["value"], 0) for row in values),
            "latest_known_month": max((str(row["time"])[:7] for row in values), default=None),
            "months_with_known_value_since_2022": len(months), "known_months_since_2022": months,
        }
    return {"observations": len(rows),
            "latest_observation_month": max((str(row["time"])[:7] for row in rows), default=None),
            "metrics": measured}


def verify_snapshot(before, plan, receipt, after, revisions, *, plan_sha256, snapshot_sha256, province_codes=()):
    """Pure verification used by the read-only command and adversarial fixtures."""
    failures, examples = Counter(), []
    def fail(code, **details):
        failures[code] += 1
        if len(examples) < 100:
            examples.append({"code": code, **details})

    if plan.get("snapshot_sha256") != snapshot_sha256:
        fail("baseline_snapshot_hash_mismatch")
    if receipt.get("plan_sha256") != plan_sha256:
        fail("receipt_plan_hash_mismatch")
    if not receipt.get("finished_at"):
        fail("apply_receipt_incomplete")
    old = {fact_key(row): row for row in before["observations"]}
    current = {fact_key(row): row for row in after["observations"]}
    changes = {tuple(change["identity"]): change for change in plan["changes"]}
    if len(changes) != len(plan["changes"]):
        fail("duplicate_plan_identity")
    if len(old) != len(before["observations"]) or len(current) != len(after["observations"]):
        fail("duplicate_snapshot_identity")
    receipt_counts = {row["province"]: row.get("planned", 0) for row in receipt.get("provinces", [])}
    expected_counts = Counter(key[2].split(":")[1] for key in changes)
    if receipt_counts != dict(expected_counts):
        fail("receipt_province_counts_mismatch", expected=dict(expected_counts), actual=receipt_counts)

    for key, change in changes.items():
        now = current.get(key)
        if now is None:
            fail("planned_observation_missing", identity=key)
            continue
        if not same_number(now.get("value"), change.get("new_value")):
            fail("planned_value_mismatch", identity=key, expected=change.get("new_value"), actual=now.get("value"))
        raw = now.get("raw_data") or {}
        if raw.get("RepairPlanSHA256") != plan_sha256:
            fail("planned_observation_marker_mismatch", identity=key)
        if change["operation"] == "withdraw_unsupported_narrative" and raw.get("MissingValueStatus") != "source_missing":
            fail("withdrawal_missing_unknown_marker", identity=key)
        if change["operation"] == "insert" and key in old:
            fail("insert_overlaps_baseline", identity=key)
        if change["operation"] != "insert" and key not in old:
            fail("planned_update_missing_baseline", identity=key)

    unplanned = 0
    for key, row in old.items():
        if key in changes:
            continue
        unplanned += 1
        if key not in current:
            fail("unplanned_old_observation_deleted", identity=key)
        elif not unchanged_fact(row, current[key]):
            fail("unplanned_old_observation_changed", identity=key)
    for key in current.keys() - old.keys() - changes.keys():
        fail("unplanned_new_observation", identity=key)

    protected_before = {(row["geography_key"], row["source_system"]): row for row in before["protected_scope_totals"]}
    protected_after = {(row["geography_key"], row["source_system"]): row for row in after["protected_scope_totals"]}
    for key in protected_before.keys() | protected_after.keys():
        a, b = protected_before.get(key), protected_after.get(key)
        if a is None or b is None:
            fail("protected_scope_missing_or_added", scope=key)
            continue
        left, right = serializable(a), serializable(b)
        equal_total = same_number(left.pop("total_value"), right.pop("total_value"))
        if not equal_total or left != right:
            fail("protected_scope_totals_changed", scope=key, before=a, after=b)

    history = defaultdict(list)
    for revision in revisions:
        history[fact_key(revision)].append(revision)
    required_revisions = preserved_revisions = 0
    for key, change in changes.items():
        if change["operation"] not in {"update", "withdraw_unsupported_narrative"} or key not in old:
            continue
        required_revisions += 1
        if any(revision_preserves(old[key], revision) for revision in history[key]):
            preserved_revisions += 1
        else:
            fail("original_state_missing_from_immutable_revisions", identity=key,
                 old_value=old[key].get("value"), available_revisions=len(history[key]))

    old_provinces, new_provinces = defaultdict(list), defaultdict(list)
    for row in old.values(): old_provinces[row["geography_key"].split(":")[1]].append(row)
    for row in current.values(): new_provinces[row["geography_key"].split(":")[1]].append(row)
    codes = set(province_codes) | old_provinces.keys() | new_provinces.keys()
    return {
        "status": "passed" if not failures else "failed", "verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Chinese province monthly source facts; national and Data Center protected aggregates",
        "plan_sha256": plan_sha256, "snapshot_sha256": snapshot_sha256,
        "checks": {"planned_observations": len(changes), "unplanned_old_observations_checked": unplanned,
                   "protected_scopes_checked": len(protected_before.keys() | protected_after.keys()),
                   "original_revisions_required": required_revisions, "original_revisions_preserved": preserved_revisions},
        "failure_counts": dict(failures), "failure_examples": examples,
        "before_observations": len(old), "after_observations": len(current),
        "provinces": [{"province": code, "before": coverage(old_provinces[code]),
                       "after": coverage(new_provinces[code]), "planned_changes": expected_counts.get(code, 0)} for code in sorted(codes)],
        "limits": ["Protected national/Data Center checks compare source totals, counts and date bounds, not every protected cell.",
                   "Publication output and scheduler activation are separate acceptance checks."],
    }


async def run(output: Path):
    import asyncpg
    from src.core.config import get_config
    from src.data.crawlers.cn_province_adapters import province_adapter_registry

    before, plan, receipt = (read_json(output / name) for name in ("before.json.gz", "plan.json", "apply_receipt.json"))
    baseline_hash = hashlib.sha256((output / "before.json.gz").read_bytes()).hexdigest()
    plan_hash = hashlib.sha256((output / "plan.json").read_bytes()).hexdigest()
    touched = {tuple(change["identity"]) for change in plan["changes"] if change["operation"] != "insert"}
    connection = await asyncpg.connect(get_config().database.url.replace("postgresql+asyncpg://", "postgresql://"), timeout=20)
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            await connection.execute("SET LOCAL statement_timeout='120s'")
            facts = [decode_record(row) for row in await connection.fetch(FACTS_SQL)]
            protected = [dict(row) for row in await connection.fetch(PROTECTED_SQL)]
            revisions = ([decode_record(row) for row in await connection.fetch(REVISIONS_SQL, sorted({key[1] for key in touched}))]
                         if touched else [])
            revisions = [row for row in revisions if fact_key(row) in touched]
        after = {"created_at": datetime.now(timezone.utc).isoformat(), "observation_count": len(facts),
                 "observations": facts, "protected_scope_totals": protected,
                 "reviewed_identity_revisions": revisions, "plan_sha256": plan_hash}
        result = verify_snapshot(before, plan, receipt, after, revisions, plan_sha256=plan_hash,
                                 snapshot_sha256=baseline_hash, province_codes=province_adapter_registry())
        write_json(output / "after.json.gz", after)
        result["after_snapshot_sha256"] = hashlib.sha256((output / "after.json.gz").read_bytes()).hexdigest()
        write_json(output / "verification.json", result)
        print(json.dumps({key: value for key, value in result.items() if key not in {"provinces", "failure_examples"}}, ensure_ascii=False))
        return result
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = asyncio.run(run(args.output))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
