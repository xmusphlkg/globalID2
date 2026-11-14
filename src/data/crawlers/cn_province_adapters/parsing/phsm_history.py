"""Read PHSM source sheets and account for every excluded or conflicting row."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import pandas as pd
from .normalization import _norm, _normalize_count, _is_report_total_label


def load_phsm_history_with_audit(
    workbook: str | Path,
    *,
    include_datacenter: bool = True,
    include_monthly_reports: bool = True,
    config_path: str | Path,
    fail_on_unmapped: bool = True,
    load_config,
    resolve_province_code,
    resolve_disease,
    source_row,
    audit_type,
    load_type,
    datacenter_source_id,
    monthly_source_id,
) -> object:
    """Load PHSM history and account for every skipped source row.

    National/category totals and blank values are expected exclusions. Unknown
    province or disease labels fail closed by default so newly published labels
    cannot disappear silently during a refresh.
    """

    cfg = load_config(config_path)
    sheets: list[tuple[str, str]] = []
    if include_datacenter:
        sheets.append((cfg["history"]["datacenter_sheet"], datacenter_source_id))
    if include_monthly_reports:
        sheets.append((cfg["history"]["monthly_report_sheet"], monthly_source_id))

    rows_by_identity: dict[tuple[str, str, str, str], dict[str, object]] = {}
    source_rows = duplicate_rows = blank_value_rows = total_rows = 0
    unmapped_diseases: list[str] = []
    unmapped_provinces: list[str] = []
    for sheet_name, source_id in sheets:
        frame = pd.read_excel(workbook, sheet_name=sheet_name)
        required = {"year", "month", "disease_cn", "province", "value", "url"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{sheet_name} missing columns: {sorted(missing)}")
        for raw in frame.to_dict(orient="records"):
            source_rows += 1
            province_code = resolve_province_code(raw.get("province"), config_path)
            if province_code is None and _norm(raw.get("province")).casefold() in {
                "total", "全国", "全国合计"
            }:
                total_rows += 1
                continue
            if province_code is None:
                unmapped_provinces.append(str(raw.get("province") or "<blank>"))
                continue
            if _is_report_total_label(raw.get("disease_en")) or _is_report_total_label(
                raw.get("disease_cn")
            ):
                total_rows += 1
                continue
            disease = resolve_disease(raw.get("disease_cn"), config_path)
            if disease is None:
                disease = resolve_disease(raw.get("disease_en"), config_path)
            if disease is None:
                label = raw.get("disease_cn") or raw.get("disease_en") or "<blank>"
                unmapped_diseases.append(str(label))
                continue
            cases = _normalize_count(raw.get("value"))
            if cases is None:
                blank_value_rows += 1
                continue
            report_date = date(int(raw["year"]), int(raw["month"]), 1)
            source_url = str(raw.get("url") or "")
            row = source_row(
                report_date=report_date,
                province_code=province_code,
                disease=disease,
                raw_label=str(raw.get("disease_cn") or raw.get("disease_en") or ""),
                cases=cases,
                source_id=source_id,
                source_url=source_url,
                source_name=(
                    "China Public Health Science Data Center"
                    if source_id == datacenter_source_id
                    else "Chinese provincial statutory infectious disease monthly report"
                ),
                quality_status="validated" if source_id == datacenter_source_id else "raw",
                raw=raw,
            )
            identity = (
                row["Date"], str(disease["code"]), province_code, source_id
            )
            previous = rows_by_identity.get(identity)
            if previous is not None and previous["Cases"] != cases:
                raise ValueError(f"Conflicting PHSM duplicate: {identity}")
            if previous is not None:
                duplicate_rows += 1
            rows_by_identity.setdefault(identity, row)
    rows = [rows_by_identity[key] for key in sorted(rows_by_identity)]
    audit = audit_type(
        source_rows=source_rows,
        imported_rows=len(rows),
        duplicate_rows=duplicate_rows,
        blank_value_rows=blank_value_rows,
        total_rows=total_rows,
        unmapped_disease_rows=len(unmapped_diseases),
        unmapped_disease_labels=tuple(sorted(set(unmapped_diseases))),
        unmapped_province_rows=len(unmapped_provinces),
        unmapped_province_labels=tuple(sorted(set(unmapped_provinces))),
    )
    if fail_on_unmapped and (unmapped_diseases or unmapped_provinces):
        raise ValueError(
            "PHSM history contains unmapped labels: "
            f"diseases={list(audit.unmapped_disease_labels)!r}, "
            f"provinces={list(audit.unmapped_province_labels)!r}"
        )
    return load_type(rows=rows, audit=audit)
