#!/usr/bin/env python3
"""Read-only, point-by-point high-frequency ledger/public-export acceptance.

Expected source points come from the ledger plus independently reviewed static
contracts and category mappings, never from the publication guard being tested.
CO is an independently managed integration and may remain explicitly pending.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASE_METRICS = {"cases", "case_notifications", "confirmed_case_notifications"}
STATIC_PATCHES = ("high_frequency_global_ontology.json", "high_frequency_europe_ontology.json",
                  "high_frequency_mexico_ontology.json", "high_frequency_at_ontology.json")
MAPPING_PATCHES = ("onboarding/high_frequency_global_mappings.json", "high_frequency_europe_reviewed_mappings.json",
                   "high_frequency_mexico_reviewed_mappings.json", "onboarding/high_frequency_at_mappings.json")


def json_key(value):
    if isinstance(value, str):
        value = json.loads(value)
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def day(value):
    return str(value)[:10]


def number(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except InvalidOperation:
        return None


def same_value(left, right):
    a, b = number(left), number(right)
    if a is None or b is None:
        return a is None and b is None
    return a == b or math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-10)


def point_key(row):
    return tuple(str(row.get(k) or "") for k in (
        "source_system", "series_code", "disease_id", "metric_type", "temporal_granularity", "geography_key",
    )) + (str(row.get("series_unit") or row.get("unit") or ""), day(row.get("date")), json_key(row.get("dimensions")))


def index_points(rows):
    index, duplicates = {}, []
    for row in rows:
        key = point_key(row)
        if key in index:
            duplicates.append(key)
        index[key] = row
    return index, duplicates


def compare_points(expected, actual):
    wanted, expected_duplicates = index_points(expected)
    found, duplicates = index_points(actual)
    missing = wanted.keys() - found.keys()
    unexpected = found.keys() - wanted.keys()
    mismatches = [key for key in wanted.keys() & found.keys()
                  if not same_value(wanted[key].get("cases"), found[key].get("cases"))]
    result = {"expected_points": len(wanted), "actual_points": len(found),
              "missing_count": len(missing), "unexpected_count": len(unexpected),
              "mismatch_count": len(mismatches), "duplicate_count": len(duplicates),
              "expected_duplicate_count": len(expected_duplicates),
              "missing_sample": sorted(missing)[:3], "unexpected_sample": sorted(unexpected)[:3],
              "mismatch_sample": mismatches[:3], "duplicate_sample": duplicates[:3]}
    result["pass"] = not any(result[k] for k in (
        "missing_count", "unexpected_count", "mismatch_count", "duplicate_count", "expected_duplicate_count"))
    return result


def load_contracts(root=ROOT):
    from src.ontology import ontology_document_paths, read_ontology_document
    series, mappings, hashes = {}, {}, {}
    for name in ("disease_ontology.json", *STATIC_PATCHES):
        path = root / "configs" / name
        if not path.exists():
            continue
        if name == "disease_ontology.json":
            document = read_ontology_document(path)
            paths = ontology_document_paths(path)
        else:
            document = json.loads(path.read_bytes())
            paths = (path,)
        for item in paths:
            hashes[str(item.relative_to(root))] = hashlib.sha256(item.read_bytes()).hexdigest()
        series.update({r["id"]: r for r in document.get("source_series", [])})
    from src.services.disease_mapping_config import load_reviewed_source_category_mappings
    for r in load_reviewed_source_category_mappings():
        mappings[r.country_code, r.source_id, r.source_code, r.definition_version] = {
            "target_kind": r.target_kind, "target_code": r.target_code, "projection_policy": r.projection_policy,
            "mapping_relation": r.mapping_relation, "comparability": r.comparability,
            "aggregation_policy": r.aggregation_policy}
    for name in MAPPING_PATCHES:
        path = root / "configs" / name
        if not path.exists():
            continue
        payload = path.read_bytes()
        hashes[str(path.relative_to(root))] = hashlib.sha256(payload).hexdigest()
        document = json.loads(payload)
        if "rows" in document:
            batches = [(None, document["rows"])]
        else:
            batches = [(Path(key).stem.upper(), rows) for key, rows in document.items() if isinstance(rows, list)]
        for country, rows in batches:
            for row in rows:
                code = row.get("country_code") or country
                mappings[code, row["source_id"], row.get("local_code") or row.get("source_code"), row["definition_version"]] = row
    return series, mappings, hashes


def expected_points(country, ledger, series, mappings):
    """Resolve approved concepts independently; explicitly account for exclusions."""
    expected, excluded, errors = [], Counter(), []
    for row in ledger:
        source = row["source_system"]
        if source == "SRC_AT_AGES_RADAR" and row["series_code"] != "SER_AT_AGES_RADAR_SOURCE_NATIVE":
            excluded["legacy_parallel_holding_identity"] += 1
            continue
        if row.get("suppressed"):
            excluded["source_suppressed"] += 1
            continue
        if row.get("value") is None:
            excluded["source_missing"] += 1
            continue
        if row.get("quality_status") == "rejected":
            excluded["rejected_quality"] += 1
            continue
        spec = series.get(row["series_code"])
        raw = row.get("raw_data") or {}
        dims = row.get("dimensions") or {}
        code = (dims.get("source_disease_code") if source == "SRC_AT_AGES_RADAR" else None)
        code = code or raw.get("SourceDiseaseCode") or row.get("source_series_code")
        mapping = mappings.get((country, source, code, row["definition_version"])) or mappings.get((country, source, code, "*"))
        target = None
        if mapping and mapping.get("target_kind") == "concept":
            # European monthly source ledgers retain discovery-only concepts
            # as lossless source observations. Other integrations expose only
            # their canonical reviewed identities in the public source layer.
            if (mapping.get("projection_policy") in {"no_projection", "discovery_only"}
                    and source not in {"SRC_CZ_SZU_MONTHLY", "SRC_EE_TERVISEAMET_MONTHLY"}):
                excluded["reviewed_unmapped_or_source_only_category"] += 1
                continue
            target = mapping["target_code"]
            if mapping.get("projection_policy") in {"no_projection", "discovery_only"}:
                excluded["source_only_projection_policy"] += 1
        elif mapping and mapping.get("projection_policy") in {"no_projection", "discovery_only"}:
            # Group/unmapped categories have no canonical disease point.
            excluded["reviewed_unmapped_or_source_only_category"] += 1
            continue
        elif spec:
            target = spec.get("concept_id")
        if target is None:
            excluded["no_reviewed_concept"] += 1
            continue
        if row.get("geography_key") != f"country:{country}:national":
            excluded["non_national_source_population"] += 1
            continue
        for field, spec_field in (("metric_type", "measure"), ("temporal_granularity", "frequency"),
                                  ("unit", "unit"), ("reporting_basis", "reporting_basis"),
                                  ("definition_version", "definition_version")):
            if spec and row.get(field) != spec.get(spec_field):
                errors.append({"source": source, "series": row["series_code"], "field": field,
                               "stored": row.get(field), "expected": spec.get(spec_field)})
        if row.get("observation_unit") != row.get("unit"):
            errors.append({"source": source, "series": row["series_code"], "field": "observation_unit"})
        if raw.get("Dimensions") and json_key(raw["Dimensions"]) != json_key(dims):
            errors.append({"source": source, "series": row["series_code"], "field": "raw_dimensions"})
        expected.append({**row, "date": day(row["time"]), "disease_id": target,
                         "series_unit": row["unit"], "cases": row["value"]})
    return expected, dict(excluded), errors


def coverage(rows):
    numeric = [r for r in rows if r.get("value") is not None and not r.get("suppressed")]
    periods = sorted({day(r["time"]) for r in numeric})
    strata = defaultdict(set)
    for row in numeric:
        strata[row["series_code"], json_key(row.get("dimensions")), row["temporal_granularity"]].add(day(row["time"]))
    gap_count, gap_samples = 0, []
    for (series, dims, frequency), dates in strata.items():
        if frequency not in {"weekly", "monthly"} or len(dates) < 2:
            continue
        current, latest = date.fromisoformat(min(dates)), date.fromisoformat(max(dates))
        while current < latest:
            if current.isoformat() not in dates:
                gap_count += 1
                if len(gap_samples) < 8:
                    gap_samples.append({"series": series, "dimensions": json.loads(dims), "period": current.isoformat()})
            current = (current + timedelta(days=7) if frequency == "weekly" else
                       date(current.year + (current.month == 12), current.month % 12 + 1, 1))
    return {"ledger_observations": len(rows), "numeric_observations": len(numeric),
            "reported_zero_observations": sum(number(r.get("value")) == 0 for r in numeric),
            "missing_observations": sum(r.get("value") is None and not r.get("suppressed") for r in rows),
            "suppressed_observations": sum(bool(r.get("suppressed")) for r in rows),
            "typed_measure_counts": dict(Counter(f"{r['metric_type']}|{r['temporal_granularity']}|{r['unit']}" for r in numeric)),
            "month_observation_counts": dict(sorted(Counter(day(r["time"])[:7] for r in numeric).items())),
            "numeric_periods": periods, "strata_count": len(strata),
            "inferred_internal_gap_count": gap_count, "inferred_internal_gap_sample": gap_samples,
            "coverage_note": "Only observed source periods; inferred gaps are unknown, never zero. No claim of all diseases or all archive years."}


def public_source_points(payload, source_ids):
    points = []
    for disease, details in (payload.get("series") or {}).items():
        for detail in details:
            if detail.get("source_system") not in source_ids:
                continue
            dates, values = detail.get("dates") or [], detail.get("values") or []
            if len(dates) != len(values):
                raise ValueError("Public source dates/value lengths disagree")
            source_dims = detail.get("point_source_dimensions")
            if source_dims is not None and len(source_dims) != len(dates):
                raise ValueError("Public point source dimensions have wrong length")
            for index, when in enumerate(dates):
                points.append({**detail, "disease_id": disease, "date": when, "cases": values[index],
                               "dimensions": source_dims[index] if source_dims is not None else detail.get("dimensions")})
    return points


def verify_public_files(directory, country, source_rows, projected_rows, source_ids):
    source_path = directory / "countries" / f"{country.lower()}-source-series.json"
    country_path = directory / "countries" / f"{country.lower()}.json"
    if not source_path.is_file() or not country_path.is_file():
        return {"pass": False, "error": "missing_public_country_or_source_series_json"}
    result = compare_points(source_rows, public_source_points(json.loads(source_path.read_bytes()), source_ids))
    document = json.loads(country_path.read_bytes())
    curves = {}
    if "disease_series" in document:
        for disease, item in document["disease_series"].items():
            for i, when in enumerate(item.get("dates") or []):
                curves[disease, day(when)] = (item["cases"][i], (item.get("point_series_codes") or [None]*len(item["dates"]))[i])
    else:
        dates = document.get("dates") or []
        for item in document.get("series") or []:
            for i, date_index in enumerate(item.get("x") or []):
                curves[item["id"], day(dates[date_index])] = (item["c"][i], (item.get("point_series_codes") or [None]*len(item["x"]))[i])
    wrong_curves = []
    for row in projected_rows:
        found = curves.get((row["disease_id"], day(row["date"])))
        if found is None or not same_value(row["cases"], found[0]) or found[1] != row["series_code"]:
            wrong_curves.append((row["disease_id"], day(row["date"]), row["series_code"]))
    result["projected_curve_mismatch_count"] = len(wrong_curves)
    result["projected_curve_mismatch_sample"] = wrong_curves[:3]
    result["pass"] = result["pass"] and not wrong_curves
    result["file_sha256"] = {str(p.name): hashlib.sha256(p.read_bytes()).hexdigest() for p in (country_path, source_path)}
    return result


def projected_rows_for_source(projected_rows, source_rows, source_id):
    """Attribute projected points through their retained source-series context.

    The public curve intentionally carries a compact row shape and therefore
    does not repeat ``source_system`` on every point.  Its ``_series_context``
    retains the selected series identity; use that identity for source-level
    projection checks.
    """
    source_series = {str(r.get("series_code")): str(r.get("source_system") or "")
                     for r in source_rows if r.get("series_code")}
    selected = []
    for row in projected_rows:
        if row.get("source_system") == source_id:
            selected.append(row)
            continue
        context = row.get("_series_context") or {}
        systems = {str(s.get("source_system") or "")
                   for s in (context.get("source_series") or [])
                   if str(s.get("series_code") or "") in context.get("selected_series_codes", [])}
        if source_id in systems:
            selected.append(row)
    return selected


async def verify(args):
    from sqlalchemy import bindparam, text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from src.core.config import get_config
    from src.core.high_frequency_sources import HIGH_FREQUENCY_SOURCES
    from src.generation.site_data_queries import fetch_disease_export_layers

    specs = [dict(country=s.country_code, scope=s.scope, source=s.source_id, frequency=s.frequency,
                  measurement=s.measurement) for s in HIGH_FREQUENCY_SOURCES.values()]
    specs.append(dict(country="AT", scope="ages_radar", source="SRC_AT_AGES_RADAR", frequency="monthly", measurement="case_notifications"))
    if args.countries:
        specs = [s for s in specs if s["country"] in args.countries]
    series, mappings, hashes = load_contracts()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
              "mode": "read_only_independent_ledger_export_acceptance", "writes_to_database": False,
              "contract_sha256": hashes, "verifier_sha256": script_hash, "countries": []}
    engine = create_async_engine(get_config().database.url)
    releases = set()
    try:
        # Keep every country inside one repeatable-read transaction.  A new
        # session per country could observe different active mapping releases
        # while the background publisher is advancing them.
        async with AsyncSession(engine) as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await db.execute(text("SET LOCAL statement_timeout = '180s'"))
            release = (await db.execute(text("SELECT release_code,checksum FROM disease_mapping_releases_v3 WHERE status='active'"))).mappings().one()
            releases.add((release["release_code"], release["checksum"]))
            for country in sorted({s["country"] for s in specs}):
                source_specs = [s for s in specs if s["country"] == country]
                source_ids = {s["source"] for s in source_specs}
                item = {"country_code": country, "sources": [], "pass": False}
                try:
                    query = text("""SELECT s.source_system,s.series_code,s.source_series_code,s.definition_version,
                        s.metric_type,s.reporting_basis,s.temporal_granularity,s.unit,o.unit AS observation_unit,
                        o.time,o.value,o.geography_key,o.dimensions,o.raw_data,o.metadata AS observation_metadata,
                        o.suppressed,o.quality_status FROM disease_series_observations o
                        JOIN disease_surveillance_series s USING(series_code)
                        WHERE s.country_code=:country AND s.source_system IN :sources""").bindparams(bindparam("sources", expanding=True))
                    ledger = [dict(r) for r in (await db.execute(query, {"country": country,"sources": sorted(source_ids)})).mappings()]
                    projected, exported = await fetch_disease_export_layers(db, country, False)
                    exported = [r for r in exported if r.get("source_system") in source_ids]
                    expected, excluded, errors = expected_points(country, ledger, series, mappings)
                    # Public projected rows omit per-point source identifiers;
                    # verify contamination through their selected source context.
                    bad_projection = []
                    for source in source_ids:
                        for row in projected_rows_for_source(projected, exported, source):
                            if row.get("metric_type") not in CASE_METRICS:
                                bad_projection.append((source, row.get("disease_id"), row.get("date")))
                    for spec in source_specs:
                        source = spec["source"]
                        stored = [r for r in ledger if r["source_system"] == source]
                        wanted = [r for r in expected if r["source_system"] == source]
                        found = [r for r in exported if r["source_system"] == source]
                        selected = projected_rows_for_source(projected, exported, source)
                        check = compare_points(wanted, found)
                        source_errors = [e for e in errors if e["source"] == source]
                        _, source_excluded, _ = expected_points(country, stored, series, mappings)
                        pending = country == "CO" and (not stored or not wanted or not check["pass"])
                        status = ("pending_external_integration" if pending else "source_not_ingested" if not stored else
                                  "no_reviewed_numeric_export_expected" if not wanted else "verified" if check["pass"] and not source_errors else "failed")
                        must_project = source in {"SRC_MX_DGE_WEEKLY", "SRC_MX_DGE_MONTHLY", "SRC_CZ_SZU_MONTHLY", "SRC_EE_TERVISEAMET_MONTHLY", "SRC_AT_AGES_RADAR"}
                        projection_ok = bool(selected) if must_project else not selected
                        source_pass = bool(wanted) and check["pass"] and not source_errors and projection_ok
                        item["sources"].append({**spec, "status": status, "pass": source_pass,
                            "pending_external_integration": pending, "coverage": coverage(stored),
                            "export_acceptance": check, "excluded_observations": source_excluded,
                            "contract_error_count": len(source_errors), "contract_error_sample": source_errors[:3],
                            "projected_case_points": len(selected), "case_projection_policy_pass": projection_ok})
                    item["projection_contamination_count"] = len(bad_projection)
                    item["projection_contamination_sample"] = bad_projection[:3]
                    item["pass"] = all(s["pass"] or s["pending_external_integration"] for s in item["sources"]) and not bad_projection
                    if args.public_dir:
                        item["public_json"] = verify_public_files(args.public_dir, country, exported, projected, source_ids)
                        item["pass"] = item["pass"] and item["public_json"]["pass"]
                except Exception as exc:
                    item["error"] = f"{type(exc).__name__}: {exc}"
                report["countries"].append(item)
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                args.receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)+"\n")
                print(json.dumps({"country": country,"pass": item["pass"],"error": item.get("error"),
                                  "sources": [{"source": s["source"],"status": s["status"],"expected": s["export_acceptance"]["expected_points"],"actual": s["export_acceptance"]["actual_points"]} for s in item["sources"]]},ensure_ascii=False),flush=True)
    finally:
        await engine.dispose()
    report["mapping_releases"] = sorted(releases)
    report["source_scopes"] = len(specs)
    report["country_count"] = len(report["countries"])
    report["pending_external_scopes"] = [s["scope"] for c in report["countries"] for s in c["sources"] if s["pending_external_integration"]]
    report["pass"] = all(c["pass"] for c in report["countries"]) and len(releases) == 1
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != script_hash:
        report["pass"] = False
        report["error"] = "Verifier changed during the acceptance snapshot"
    for name, digest in hashes.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest:
            report["pass"] = False
            report["error"] = "Reviewed contracts changed during acceptance; rerun"
    args.receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)+"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", nargs="+")
    parser.add_argument("--receipt", type=Path, default=Path("reports/high-frequency-public-export-acceptance.json"))
    parser.add_argument("--public-dir", type=Path, help="Directory containing countries/<code>.json and <code>-source-series.json")
    result = asyncio.run(verify(parser.parse_args()))
    print(json.dumps({key: result[key] for key in ("country_count","source_scopes","pending_external_scopes","pass")},ensure_ascii=False))
    sys.exit(0 if result["pass"] else 1)
