"""Pure reviewed annual, disease-monthly and legacy-ICD workbook projections."""
from __future__ import annotations

from datetime import date
import re
from typing import Any
from src.data.crawlers.is_history import IcelandHistoryRawFile
from .contracts import IcelandWorkbookLayoutError, ReviewedSeries, ReviewedLegacySeries, _ParseContext
from .catalogue import (
    HISTORY_SOURCE_ID, HISTORY_SOURCE_NAME, LEGACY_SOURCE_ID, LEGACY_SOURCE_NAME,
    ANNUAL_ALIAS_LOOKUP, MONTHLY_SERIES, LEGACY_PAIR_LOOKUP,
    _as_year, _label_key, _text, _month_number, _explanation_key, _normalize_icd,
)
from .workbook import _Workbook, _count_value, _rate_value


def _base_row(
    definition: ReviewedSeries | ReviewedLegacySeries,
    *,
    source_kind: str,
    report_date: date,
    cases: int,
    incidence: float | None,
    value_status: str,
    raw_label_source: str,
    source_url: str,
    source_file: str,
    source_sha256: str,
    workbook_sheet: str,
    icd10: str = "",
    icd9: str = "",
) -> dict[str, Any]:
    legacy = isinstance(definition, ReviewedLegacySeries)
    if legacy:
        source_id = LEGACY_SOURCE_ID
        source_name = LEGACY_SOURCE_NAME
        source_series_code = definition.source_series_code
        local_code = definition.local_code
        frequency = "monthly"
        measure = "registered_diagnoses"
    else:
        source_id = HISTORY_SOURCE_ID
        source_name = HISTORY_SOURCE_NAME
        source_series_code = definition.source_series_code(source_kind)
        local_code = definition.local_code(source_kind)
        frequency = "annual" if source_kind == "registry_annual" else "monthly"
        measure = "case_notifications"
    return {
        "Date": report_date.isoformat(),
        "Year": str(report_date.year),
        "Month": str(report_date.month),
        "Diseases": definition.label_en,
        "RawDiseaseLabel": definition.label_is,
        "SourceRawDiseaseLabel": raw_label_source,
        "DiseaseCode": local_code,
        "DiseaseFull": definition.concept_id,
        "Cases": str(cases),
        "Incidence": "" if incidence is None else format(incidence, ".12g"),
        "Deaths": "",
        "CountryCode": "IS",
        "Frequency": frequency,
        "Measure": measure,
        "Unit": "count",
        "ValueStatus": value_status,
        "Source": source_name,
        "SourceId": source_id,
        "SourceKind": source_kind,
        "SourceSeriesCode": source_series_code,
        "SourceURL": source_url,
        "GeographyKey": "country:IS:national",
        "DatasetStatus": "historical_published_workbook",
        "AuthoritativeRevision": "true",
        "ICD10": icd10,
        "ICD9": icd9,
        "WorkbookSheet": workbook_sheet,
        "__source_file": source_file,
        "__source_sha256": source_sha256,
    }


def _find_year_header(book: _Workbook, sheet: str, *, require_count_metric: bool) -> tuple[int, dict[int, int]]:
    max_row, max_col = book.dimensions(sheet)
    for row in range(1, min(max_row, 30) + 1):
        years = {
            column: year
            for column in range(1, max_col + 1)
            if (year := _as_year(book.raw(sheet, row, column))) is not None
        }
        if len(years) < 2:
            continue
        if require_count_metric:
            count_markers = 0
            for column in years:
                below = " ".join(
                    _label_key(book.raw(sheet, candidate, column))
                    for candidate in range(row + 1, min(row + 3, max_row) + 1)
                )
                if "fjöldi" in below or re.search(r"\bnumber\b", below):
                    count_markers += 1
            if count_markers < max(1, len(years) // 2):
                continue
        return row, years
    raise IcelandWorkbookLayoutError(f"No reviewed year header found in {book.path.name}:{sheet}")


def _parse_annual(
    book: _Workbook,
    raw_file: IcelandHistoryRawFile,
    context: _ParseContext,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sheet in book.sheet_names:
        max_row, _ = book.dimensions(sheet)
        header_row, year_columns = _find_year_header(book, sheet, require_count_metric=True)
        blank_run = 0
        sheet_data_started = False
        for row_number in range(header_row + 1, max_row + 1):
            label_is = _text(book.raw(sheet, row_number, 1))
            label_en = _text(book.raw(sheet, row_number, 2))
            if not label_is and not label_en:
                blank_run += 1
                if blank_run >= 3 and sheet_data_started:
                    break
                continue
            blank_run = 0
            if label_is.startswith("*") or "number of cases may" in _label_key(label_en):
                continue
            sheet_data_started = True
            definition = ANNUAL_ALIAS_LOOKUP.get(_label_key(label_is)) or ANNUAL_ALIAS_LOOKUP.get(_label_key(label_en))
            for column, year in sorted(year_columns.items(), key=lambda item: item[1]):
                value, formula_status = book.resolved(sheet, row_number, column)
                cases, value_status = _count_value(value, formula_status, dash_is_zero=False)
                if value_status == "missing":
                    context.stats["annual.blank_unknown_cells"] += 1
                    continue
                if value_status == "not_applicable":
                    context.stats["annual.not_applicable_dash_cells"] += 1
                    continue
                if cases is None:
                    context.reject(
                        "invalid_annual_value",
                        source_file=raw_file.filename,
                        sheet=sheet,
                        row=row_number,
                        column=column,
                        raw_value=_text(value),
                        raw_label=label_is or label_en,
                    )
                    continue
                if definition is None:
                    context.reject(
                        "unreviewed_annual_disease",
                        source_file=raw_file.filename,
                        sheet=sheet,
                        row=row_number,
                        year=year,
                        raw_label_is=label_is,
                        raw_label_en=label_en,
                        cases=cases,
                    )
                    continue
                rows.append(
                    _base_row(
                        definition,
                        source_kind="registry_annual",
                        report_date=date(year, 1, 1),
                        cases=cases,
                        incidence=None,
                        value_status=value_status,
                        raw_label_source=label_is or label_en,
                        source_url=raw_file.source_url,
                        source_file=raw_file.filename,
                        source_sha256=raw_file.sha256,
                        workbook_sheet=sheet,
                    )
                )
                context.stats["annual.rows_parsed"] += 1
    return rows


def _find_rate_column(
    book: _Workbook,
    sheet: str,
    header_row: int,
    count_column: int,
    next_year_column: int,
) -> int | None:
    for column in range(count_column + 1, next_year_column):
        markers = " ".join(
            _label_key(book.raw(sheet, row, column))
            for row in range(header_row + 1, header_row + 3)
        )
        if "100.000" in markers or "100,000" in markers or "/100" in markers:
            return column
    return None


def _parse_disease_monthly(
    book: _Workbook,
    raw_file: IcelandHistoryRawFile,
    context: _ParseContext,
) -> list[dict[str, Any]]:
    definition = MONTHLY_SERIES.get(raw_file.disease_key)
    if definition is None:
        raise IcelandWorkbookLayoutError(
            f"No reviewed disease identity for {raw_file.key}:{raw_file.disease_key}"
        )
    candidate_sheets = [
        sheet for sheet in book.sheet_names if "greining" in _label_key(sheet)
    ] or list(book.sheet_names)
    selected: tuple[str, int, dict[int, int]] | None = None
    for sheet in candidate_sheets:
        try:
            header_row, year_columns = _find_year_header(book, sheet, require_count_metric=True)
        except IcelandWorkbookLayoutError:
            continue
        selected = sheet, header_row, year_columns
        break
    if selected is None:
        raise IcelandWorkbookLayoutError(f"No monthly diagnosis sheet found in {book.path.name}")
    sheet, header_row, year_columns = selected
    max_row, max_col = book.dimensions(sheet)
    ordered_years = sorted(year_columns.items(), key=lambda item: item[0])
    rate_columns: dict[int, int | None] = {}
    for index, (count_column, _) in enumerate(ordered_years):
        next_column = ordered_years[index + 1][0] if index + 1 < len(ordered_years) else max_col + 1
        rate_columns[count_column] = _find_rate_column(
            book, sheet, header_row, count_column, next_column
        )

    month_rows: dict[int, int] = {}
    for row_number in range(header_row + 1, max_row + 1):
        month = _month_number(book.raw(sheet, row_number, 1))
        if month is not None and month not in month_rows:
            month_rows[month] = row_number
    if set(month_rows) != set(range(1, 13)):
        raise IcelandWorkbookLayoutError(
            f"Expected 12 month rows in {book.path.name}:{sheet}; found {sorted(month_rows)}"
        )

    rows: list[dict[str, Any]] = []
    for count_column, year in ordered_years:
        rate_column = rate_columns[count_column]
        for month, row_number in sorted(month_rows.items()):
            raw_value = book.raw(sheet, row_number, count_column)
            value, formula_status = book.resolved(sheet, row_number, count_column)
            cases, value_status = _count_value(value, formula_status, dash_is_zero=True)
            if value_status == "missing":
                context.stats["disease_monthly.blank_unknown_cells"] += 1
                continue
            if cases is None:
                context.reject(
                    "invalid_disease_monthly_value",
                    source_file=raw_file.filename,
                    sheet=sheet,
                    row=row_number,
                    column=count_column,
                    raw_value=_text(raw_value),
                    disease_key=raw_file.disease_key,
                )
                continue
            incidence = None
            if rate_column is not None:
                rate_raw, _ = book.resolved(sheet, row_number, rate_column)
                incidence = _rate_value(rate_raw)
            rows.append(
                _base_row(
                    definition,
                    source_kind="registry_disease_monthly",
                    report_date=date(year, month, 1),
                    cases=cases,
                    incidence=incidence,
                    value_status=value_status,
                    raw_label_source=definition.label_is,
                    source_url=raw_file.source_url,
                    source_file=raw_file.filename,
                    source_sha256=raw_file.sha256,
                    workbook_sheet=sheet,
                )
            )
            context.stats["disease_monthly.rows_parsed"] += 1
            if value_status == "dash_zero":
                context.stats["disease_monthly.dash_zero_cells"] += 1
            if value_status.startswith("formula_"):
                context.stats[f"disease_monthly.{value_status}"] += 1
    return rows


def _month_header(book: _Workbook, sheet: str) -> tuple[int, dict[int, int]]:
    max_row, max_col = book.dimensions(sheet)
    for row in range(1, min(max_row, 20) + 1):
        columns: dict[int, int] = {}
        for column in range(1, max_col + 1):
            month = _month_number(book.raw(sheet, row, column))
            if month is not None:
                columns[column] = month
        if len(columns) >= 10:
            return row, columns
    raise IcelandWorkbookLayoutError(f"No month header found in {book.path.name}:{sheet}")


def _explanation_codes(book: _Workbook) -> dict[str, str]:
    codes: dict[str, str] = {}
    for sheet in book.sheet_names:
        if not _label_key(sheet).startswith("ský"):
            continue
        max_row, max_col = book.dimensions(sheet)
        if max_col < 2:
            continue
        for row in range(1, max_row + 1):
            label = _text(book.raw(sheet, row, 1))
            code = _normalize_icd(book.raw(sheet, row, 2))
            if label and code and code != "ICD-10":
                codes[_label_key(label)] = code
                codes.setdefault(_explanation_key(label), code)
    return codes


def _sheet_year(book: _Workbook, sheet: str) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", sheet)
    if match:
        return int(match.group(0))
    max_row, max_col = book.dimensions(sheet)
    for row in range(1, min(max_row, 5) + 1):
        for column in range(1, min(max_col, 5) + 1):
            if (year := _as_year(book.raw(sheet, row, column))) is not None:
                return year
    return None


def _parse_legacy_icd(
    book: _Workbook,
    raw_file: IcelandHistoryRawFile,
    context: _ParseContext,
) -> list[dict[str, Any]]:
    explanation = _explanation_codes(book)
    rows: list[dict[str, Any]] = []
    parsed_year_sheets = 0
    for sheet in book.sheet_names:
        year = _sheet_year(book, sheet)
        if year is None:
            continue
        try:
            header_row, month_columns = _month_header(book, sheet)
        except IcelandWorkbookLayoutError:
            continue
        parsed_year_sheets += 1
        max_row, _ = book.dimensions(sheet)
        header_values = {
            column: _label_key(book.raw(sheet, header_row, column))
            for column in range(1, min(month_columns) + 1)
        }
        icd10_column = next((column for column, value in header_values.items() if value == "icd-10"), None)
        icd9_column = next((column for column, value in header_values.items() if value == "icd-9"), None)
        first_month_column = min(month_columns)
        if icd9_column is not None:
            label_column = max(1, icd9_column - 1)
            english_column = None
        elif icd10_column is not None and icd10_column + 1 < first_month_column:
            label_column = icd10_column + 1
            english_column = None
        else:
            label_column = 1
            english_column = 2 if first_month_column >= 3 else None

        for row_number in range(header_row + 1, max_row + 1):
            label_is = _text(book.raw(sheet, row_number, label_column))
            label_en = _text(book.raw(sheet, row_number, english_column)) if english_column else ""
            if not label_is:
                continue
            explicit_icd10 = _normalize_icd(book.raw(sheet, row_number, icd10_column)) if icd10_column else ""
            icd10 = explicit_icd10 or explanation.get(_label_key(label_is), "") or explanation.get(_explanation_key(label_is), "")
            icd9 = _normalize_icd(book.raw(sheet, row_number, icd9_column)) if icd9_column else ""
            definition = LEGACY_PAIR_LOOKUP.get((_normalize_icd(icd10), _label_key(label_is)))
            any_observation = False
            for column, month in sorted(month_columns.items(), key=lambda item: item[1]):
                raw_value = book.raw(sheet, row_number, column)
                value, formula_status = book.resolved(sheet, row_number, column)
                cases, value_status = _count_value(value, formula_status, dash_is_zero=True)
                if value_status == "missing":
                    context.stats["legacy_icd.blank_unknown_cells"] += 1
                    continue
                any_observation = True
                if cases is None:
                    context.reject(
                        "invalid_legacy_icd_value",
                        source_file=raw_file.filename,
                        sheet=sheet,
                        row=row_number,
                        column=column,
                        raw_value=_text(raw_value),
                        raw_label_is=label_is,
                        icd10=icd10,
                    )
                    continue
                if definition is None:
                    context.reject(
                        "unreviewed_legacy_icd_series",
                        source_file=raw_file.filename,
                        sheet=sheet,
                        row=row_number,
                        year=year,
                        month=month,
                        raw_label_is=label_is,
                        raw_label_en=label_en,
                        icd10=icd10,
                        icd9=icd9,
                        cases=cases,
                    )
                    continue
                rows.append(
                    _base_row(
                        definition,
                        source_kind="legacy_icd_monthly",
                        report_date=date(year, month, 1),
                        cases=cases,
                        incidence=None,
                        value_status=value_status,
                        raw_label_source=label_is,
                        source_url=raw_file.source_url,
                        source_file=raw_file.filename,
                        source_sha256=raw_file.sha256,
                        workbook_sheet=sheet,
                        icd10=icd10,
                        icd9=icd9,
                    )
                )
                context.stats["legacy_icd.rows_parsed"] += 1
                if value_status == "dash_zero":
                    context.stats["legacy_icd.dash_zero_cells"] += 1
            if any_observation and not icd10:
                context.stats["legacy_icd.rows_without_icd10"] += 1
    if parsed_year_sheets == 0:
        raise IcelandWorkbookLayoutError(f"No legacy ICD year sheets parsed in {book.path.name}")
    return rows
