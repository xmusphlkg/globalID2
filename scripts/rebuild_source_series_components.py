#!/usr/bin/env python3
"""Safely rebuild split source-series facts from source CSV extracts.

This command is intentionally different from the legacy disease backfill: it
starts from source labels/codes and the ontology Registry, never from a flattened
``disease_id``.  Missing source cells are reported and omitted, not rewritten as
zero.  The default mode is a read-only plan; ``--apply`` delegates all writes to
``SeriesObservationStore`` and therefore its normal quality gate.
"""

from __future__ import annotations



import argparse
import asyncio
from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import sys
import unicodedata
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.source_series_rows import (
    _SUPPRESSED_VALUES as _SUPPRESSED_VALUES,
    SourceAdapter as SourceAdapter,
    _normalize as _normalize,
    _first_text as _first_text,
    _value_kind as _value_kind,
    _is_national as _is_national,
    _row_identity as _row_identity,
    _mmwr_week_end as _mmwr_week_end,
    _prepare_row as _prepare_row,
    _source_file_stat as _source_file_stat,
    _time_coverage as _time_coverage,
)

from src.core.database import get_db  # noqa: E402
from src.core.disease_mutation_lock import acquire_disease_data_mutation_lock  # noqa: E402
from src.data.storage import (  # noqa: E402
    SeriesObservationQualityPolicy,
    SeriesObservationStore,
)
from src.ontology import load_disease_ontology  # noqa: E402

TRANSITION_CSV = ROOT / "configs" / "disease_mapping_transitions.csv"
REINGEST_ACTIONS = frozenset({"source_reingest", "remap_and_reingest"})


DEFAULT_ADAPTERS = {
    ("JP", "SRC_JP_NIID"): SourceAdapter(
        country_code="JP",
        source_id="SRC_JP_NIID",
        paths=(
            ROOT / "data/history/jp/weekly_cases_total_merged_standardized.csv",
            ROOT / "data/current/jp/weekly_cases_standardized.csv",
        ),
        label_fields=("Disease", "RawDiseaseLabel"),
        value_field="Current week",
        national_fields=("Reporting Area", "ReportingArea"),
        national_values=("総数", "全国", "total", "national"),
    ),
    ("AU", "SRC_AU_NINDSS"): SourceAdapter(
        country_code="AU",
        source_id="SRC_AU_NINDSS",
        paths=(
            ROOT / "data/history/au/australia_national_data.csv",
            ROOT / "data/current/au/australia_national_data.csv",
        ),
        label_fields=("DiseaseFull", "Disease", "RawDiseaseLabel"),
        value_field="Cases",
        national_fields=("Group",),
        national_values=("location_aggregated", "national_total"),
    ),
    ("US", "SRC_US_NNDSS"): SourceAdapter(
        country_code="US",
        source_id="SRC_US_NNDSS",
        paths=(ROOT / "data/history/us/NNDSS_Weekly_Data_20260317.csv",),
        label_fields=("Label", "RawDiseaseLabel", "Disease"),
        value_field="Current week",
        national_fields=("Reporting Area", "ReportingArea"),
        national_values=(
            "US RESIDENTS",
            "U.S. RESIDENTS",
            "UNITED STATES RESIDENTS",
        ),
        week_calendar="mmwr_saturday",
    ),
    ("BR", "SRC_BR_SINAN"): SourceAdapter(
        country_code="BR",
        source_id="SRC_BR_SINAN",
        paths=(),
        label_fields=("DiseaseCode", "Disease"),
        value_field="Cases",
        blocked_reason=(
            "BR source components require file-family-specific parsing. In "
            "particular, NTRA must be rebuilt from NU_CASOPOS rather than DBF "
            "row counts; generic normalized totals are not authoritative."
        ),
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or apply source-series component reconstruction for ontology "
            "mapping transitions."
        )
    )
    parser.add_argument(
        "--transitions",
        type=Path,
        default=TRANSITION_CSV,
        help="Transition CSV; defaults to configs/disease_mapping_transitions.csv.",
    )
    parser.add_argument(
        "--country",
        action="append",
        help="Limit to a two-letter country code; may be repeated.",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="COUNTRY=PATH",
        help=(
            "Replace a country's default source files; may be repeated to supply "
            "ordered history/current files."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write ready facts through SeriesObservationStore quality gates.",
    )
    parser.add_argument(
        "--quality-mode",
        choices=("quarantine", "fail_closed"),
        default="quarantine",
        help="Blocking quality policy used by --apply (default: quarantine).",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "With --apply, write ready transitions while retaining blocked ones "
            "in the report. Without this explicit flag any blocked transition "
            "prevents all writes."
        ),
    )
    return parser.parse_args()


def _load_transitions(path: Path, countries: set[str] | None) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        row
        for row in rows
        if row.get("action", "").strip() in REINGEST_ACTIONS
        and (countries is None or row.get("country_code", "").upper() in countries)
    ]


def _input_overrides(values: Iterable[str]) -> dict[str, tuple[Path, ...]]:
    result: dict[str, list[Path]] = defaultdict(list)
    for value in values:
        country, separator, raw_path = value.partition("=")
        country = country.strip().upper()
        if separator != "=" or len(country) != 2 or not raw_path.strip():
            raise ValueError("--input must use COUNTRY=PATH, for example JP=data.csv")
        result[country].append(Path(raw_path).expanduser().resolve())
    return {country: tuple(paths) for country, paths in result.items()}


def _adapter_for(
    country_code: str,
    source_id: str,
    overrides: dict[str, tuple[Path, ...]],
) -> SourceAdapter | None:
    adapter = DEFAULT_ADAPTERS.get((country_code, source_id))
    if adapter is None:
        return None
    if country_code not in overrides:
        return adapter
    return SourceAdapter(
        country_code=adapter.country_code,
        source_id=adapter.source_id,
        paths=overrides[country_code],
        label_fields=adapter.label_fields,
        value_field=adapter.value_field,
        national_fields=adapter.national_fields,
        national_values=adapter.national_values,
        week_calendar=adapter.week_calendar,
        blocked_reason=adapter.blocked_reason,
    )


def _resolve_transition_series(
    store: SeriesObservationStore, transition: dict[str, str]
) -> list[dict[str, Any]]:
    source_id = transition["source_id"].strip()
    if source_id == "*":
        return []
    labels = [transition.get("local_name", ""), transition.get("evidence_value", "")]
    matches: dict[str, dict[str, Any]] = {}
    for label in labels:
        if not label:
            continue
        for series in store.ontology.series_lookup(
            source_id=source_id,
            country_code=transition["country_code"],
            concept_id=transition["new_disease_id"],
            local_label=label,
        ):
            if series["status"] in {"active", "historical"}:
                matches[series["id"]] = series
    return [matches[key] for key in sorted(matches)]


def build_plan(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    countries = (
        {str(value).strip().upper() for value in args.country} if args.country else None
    )
    if countries and any(len(country) != 2 for country in countries):
        raise ValueError("--country must contain two-letter country codes")
    overrides = _input_overrides(args.input)
    transitions = _load_transitions(args.transitions, countries)
    store = SeriesObservationStore(load_disease_ontology())
    reports: list[dict[str, Any]] = []
    ready_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # Prepare exact Registry label routes. A source row matching two transitions
    # is ambiguous and is never assigned by iteration order.
    routes: dict[tuple[str, str], dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    transition_series: list[list[dict[str, Any]]] = []
    for index, transition in enumerate(transitions):
        matched_series = _resolve_transition_series(store, transition)
        transition_series.append(matched_series)
        for series in matched_series:
            for label in series["local_labels"]:
                routes[(transition["country_code"], transition["source_id"])][
                    _normalize(label)
                ].add(index)

    matched_rows: list[list[tuple[Path, dict[str, str], str, str, float | None]]] = [
        [] for _ in transitions
    ]
    file_stats: list[dict[str, dict[str, Any]]] = [
        defaultdict(dict) for _ in transitions
    ]
    for source_key, label_routes in routes.items():
        country_code, source_id = source_key
        adapter = _adapter_for(country_code, source_id, overrides)
        if adapter is None or adapter.blocked_reason:
            continue
        for path in adapter.paths:
            affected = {index for indices in label_routes.values() for index in indices}
            for index in affected:
                file_stats[index][str(path)] = _source_file_stat(path)
            if not path.is_file():
                continue
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for raw_row in csv.DictReader(handle):
                    if not _is_national(raw_row, adapter):
                        continue
                    label = _first_text(raw_row, adapter.label_fields)
                    if not label:
                        continue
                    indices = label_routes.get(_normalize(label), set())
                    if len(indices) != 1:
                        # Zero means irrelevant; multiple means unsafe ambiguity.
                        continue
                    index = next(iter(indices))
                    kind, numeric = _value_kind(raw_row.get(adapter.value_field))
                    stat = file_stats[index][str(path)]
                    stat["matched_rows"] += 1
                    stat[kind] += 1
                    if kind in {"zero", "nonzero", "suppressed"}:
                        stat["non_empty"] += 1
                    row = _prepare_row(raw_row, adapter, label, path)
                    matched_rows[index].append((path, row, label, kind, numeric))

    for index, transition in enumerate(transitions):
        country_code = transition["country_code"].upper()
        source_id = transition["source_id"].strip()
        adapter = _adapter_for(country_code, source_id, overrides)
        series = transition_series[index]
        report: dict[str, Any] = {
            "country_code": country_code,
            "source_id": source_id,
            "local_name": transition["local_name"],
            "action": transition["action"],
            "old_disease_id": transition["old_disease_id"],
            "new_disease_id": transition["new_disease_id"],
            "series_code": series[0]["id"] if len(series) == 1 else None,
            "source_files": list(file_stats[index].values()),
            "matched_rows": len(matched_rows[index]),
        }
        counts = defaultdict(int)
        for _, _, _, kind, _ in matched_rows[index]:
            counts[kind] += 1
        report.update(
            {
                "non_empty": counts["zero"] + counts["nonzero"] + counts["suppressed"],
                "nonzero": counts["nonzero"],
                "zero": counts["zero"],
                "missing": counts["missing"],
                "suppressed": counts["suppressed"],
                "invalid": counts["invalid"],
            }
        )

        unresolved: str | None = None
        if source_id == "*":
            unresolved = "Wildcard source transition has no authoritative source-series identity."
        elif adapter is None:
            unresolved = "No source-specific reconstruction adapter is implemented."
        elif adapter.blocked_reason:
            unresolved = adapter.blocked_reason
        elif len(series) == 0:
            unresolved = "No exact active Registry series matches this transition."
        elif len(series) > 1:
            unresolved = (
                "Multiple Registry series match; refusing to choose by row order."
            )
        elif not adapter.paths or not any(path.is_file() for path in adapter.paths):
            unresolved = "No configured source extract exists."
        elif counts["invalid"]:
            unresolved = (
                f"Source component contains {counts['invalid']} non-empty value(s) "
                "that cannot be parsed safely."
            )

        # Deduplicate overlapping history/current extracts without allowing a
        # newer missing cell to erase a real value. Current files occur last in
        # adapter order and therefore win only when semantic values agree.
        deduplicated: dict[
            tuple[str, ...], tuple[dict[str, str], str, float | None]
        ] = {}
        conflicts: list[dict[str, Any]] = []
        for _, row, label, kind, numeric in matched_rows[index]:
            if kind not in {"zero", "nonzero", "suppressed"}:
                continue
            identity = _row_identity(row, label)
            previous = deduplicated.get(identity)
            if previous is not None:
                previous_kind, previous_numeric = previous[1], previous[2]
                if (previous_kind, previous_numeric) != (kind, numeric):
                    conflicts.append(
                        {
                            "identity": list(identity),
                            "first": {"kind": previous_kind, "value": previous_numeric},
                            "second": {"kind": kind, "value": numeric},
                        }
                    )
                    continue
            deduplicated[identity] = (row, kind, numeric)
        usable_rows = [item[0] for item in deduplicated.values()]
        if conflicts:
            unresolved = (
                "Overlapping source extracts contain conflicting component values."
            )
            report["conflict_count"] = len(conflicts)
            report["conflict_examples"] = conflicts[:20]

        observations: list[dict[str, Any]] = []
        if unresolved is None and usable_rows:
            try:
                built = store.build_observations(
                    usable_rows,
                    country_code,
                    source_id=source_id,
                    value_field=adapter.value_field,
                    geography_key=f"country:{country_code}:national",
                )
            except ValueError as exc:
                unresolved = f"Registry observation identity conflict: {exc}"
            else:
                if (
                    built.skipped_unmatched
                    or built.skipped_ambiguous
                    or built.skipped_invalid
                ):
                    unresolved = (
                        "Registry did not resolve every non-missing source component "
                        f"(unmatched={built.skipped_unmatched}, "
                        f"ambiguous={built.skipped_ambiguous}, invalid={built.skipped_invalid})."
                    )
                observations = built.observations
        elif unresolved is None:
            unresolved = "Source component has no non-missing observations to rebuild."

        report["rebuildable_observations"] = len(observations)
        report["time_coverage"] = _time_coverage(observations)
        report["status"] = "ready" if unresolved is None else "blocked"
        report["unresolved_reason"] = unresolved
        if unresolved is None:
            ready_rows[country_code].extend(usable_rows)
        reports.append(report)

    preview: dict[str, Any] = {}
    for country_code, rows in sorted(ready_rows.items()):
        source_ids = {
            report["source_id"]
            for report in reports
            if report["country_code"] == country_code and report["status"] == "ready"
        }
        if len(source_ids) != 1:
            continue
        adapter = _adapter_for(country_code, next(iter(source_ids)), overrides)
        assert adapter is not None
        built = store.build_observations(
            rows,
            country_code,
            source_id=next(iter(source_ids)),
            value_field=adapter.value_field,
            geography_key=f"country:{country_code}:national",
        )
        preview[country_code] = store.assess_quality(
            built.observations,
            country_code=country_code,
            source_row_count=len(rows),
            policy=SeriesObservationQualityPolicy(
                mode=args.quality_mode, registry_coverage="required"
            ),
        ).to_dict()

    summary = {
        "mode": "apply" if args.apply else "dry_run",
        "transitions": str(args.transitions),
        "selected_transitions": len(reports),
        "ready_transitions": sum(report["status"] == "ready" for report in reports),
        "blocked_transitions": sum(report["status"] != "ready" for report in reports),
        "rebuildable_observations": sum(
            report["rebuildable_observations"] for report in reports
        ),
        "transitions_report": reports,
        "quality_preview": preview,
    }
    return summary, dict(ready_rows)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.apply and not args.country:
        raise ValueError("--apply requires at least one explicit --country scope")
    summary, ready_rows = build_plan(args)
    if not args.apply:
        return summary
    blocked = [
        report
        for report in summary["transitions_report"]
        if report["status"] != "ready"
    ]
    if blocked and not getattr(args, "allow_partial", False):
        raise ValueError(
            "Refusing partial source-series reconstruction: "
            f"{len(blocked)} selected transition(s) are blocked"
        )

    store = SeriesObservationStore(load_disease_ontology())
    overrides = _input_overrides(args.input)
    saved: dict[str, Any] = {}
    async with get_db() as db:
        # A reconstruction can span multiple countries. Take the exclusive
        # maintenance gate before any per-country store call, never upgrade
        # after already holding a shared country gate.
        await acquire_disease_data_mutation_lock(db)
        for country_code, rows in sorted(ready_rows.items()):
            source_ids = {
                report["source_id"]
                for report in summary["transitions_report"]
                if report["country_code"] == country_code
                and report["status"] == "ready"
            }
            if len(source_ids) != 1:
                raise ValueError(f"{country_code} has multiple selected source IDs")
            source_id = next(iter(source_ids))
            adapter = _adapter_for(country_code, source_id, overrides)
            assert adapter is not None
            result = await store.save_rows(
                db,
                rows,
                country_code,
                source_id=source_id,
                value_field=adapter.value_field,
                geography_key=f"country:{country_code}:national",
                quality_policy=SeriesObservationQualityPolicy(
                    mode=args.quality_mode,
                    registry_coverage="required",
                ),
            )
            saved[country_code] = {
                "upserted": result.upserted,
                "skipped_unmatched": result.skipped_unmatched,
                "skipped_ambiguous": result.skipped_ambiguous,
                "skipped_invalid": result.skipped_invalid,
                "skipped_registry_not_synced": result.skipped_registry_not_synced,
                "quality_report": result.quality_report.to_dict(),
            }
    summary["saved"] = saved
    return summary


def main() -> None:
    result = asyncio.run(run(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
