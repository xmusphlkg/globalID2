"""Read XLS/XLSX cells and evaluate the supported arithmetic formula subset."""
from __future__ import annotations

import ast
import math
from pathlib import Path
import re
import openpyxl
import xlrd
from openpyxl.utils.cell import column_index_from_string

from .catalogue import _text


class _Workbook:
    """Uniform 1-based cell access for xls and xlsx files."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.kind = path.suffix.casefold()
        if self.kind == ".xlsx":
            self.formula_book = openpyxl.load_workbook(path, data_only=False)
            self.cached_book = openpyxl.load_workbook(path, data_only=True)
            self.sheet_names = list(self.formula_book.sheetnames)
        elif self.kind == ".xls":
            self.formula_book = xlrd.open_workbook(path, on_demand=True)
            self.cached_book = self.formula_book
            self.sheet_names = list(self.formula_book.sheet_names())
        else:
            raise ValueError(f"Unsupported Iceland workbook extension: {path}")

    def dimensions(self, sheet: str) -> tuple[int, int]:
        if self.kind == ".xlsx":
            ws = self.formula_book[sheet]
            return ws.max_row, ws.max_column
        ws = self.formula_book.sheet_by_name(sheet)
        return ws.nrows, ws.ncols

    def raw(self, sheet: str, row: int, column: int) -> object:
        if self.kind == ".xlsx":
            return self.formula_book[sheet].cell(row, column).value
        ws = self.formula_book.sheet_by_name(sheet)
        if row < 1 or column < 1 or row > ws.nrows or column > ws.ncols:
            return None
        value = ws.cell_value(row - 1, column - 1)
        return None if value == "" else value

    def cached(self, sheet: str, row: int, column: int) -> object:
        if self.kind == ".xlsx":
            return self.cached_book[sheet].cell(row, column).value
        return self.raw(sheet, row, column)

    def resolved(self, sheet: str, row: int, column: int) -> tuple[object, str]:
        raw = self.raw(sheet, row, column)
        if not (isinstance(raw, str) and raw.startswith("=")):
            return raw, "reported"
        cached = self.cached(sheet, row, column)
        if cached is not None and _text(cached) != "":
            return cached, "formula_cached"
        try:
            return _evaluate_formula(self, sheet, raw), "formula_evaluated"
        except (ValueError, ZeroDivisionError, TypeError):
            return None, "formula_unresolved"

    def close(self) -> None:
        if self.kind == ".xlsx":
            self.formula_book.close()
            self.cached_book.close()
        else:
            self.formula_book.release_resources()


_CELL_RE = re.compile(r"(?<![A-Za-z0-9_])\$?([A-Z]{1,3})\$?(\d+)", re.I)


_RANGE_RE = re.compile(r"\$?([A-Z]{1,3})\$?(\d+):\$?([A-Z]{1,3})\$?(\d+)", re.I)


def _numeric_cell(book: _Workbook, sheet: str, column: int, row: int) -> float:
    value, _ = book.resolved(sheet, row, column)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Formula references a non-numeric cell")
    return float(value)


def _safe_arithmetic(expression: str) -> float:
    tree = ast.parse(expression, mode="eval")
    allowed_binary = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
    allowed_unary = (ast.UAdd, ast.USub)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load, ast.Constant)):
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, allowed_binary):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, allowed_unary):
            continue
        if isinstance(node, (*allowed_binary, *allowed_unary)):
            continue
        raise ValueError("Unsupported Excel formula expression")
    value = eval(compile(tree, "<iceland-excel-formula>", "eval"), {"__builtins__": {}}, {})
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Excel formula did not evaluate to a finite number")
    return float(value)


def _evaluate_formula(book: _Workbook, sheet: str, formula: str) -> float:
    expression = formula.lstrip("=").strip()

    def sum_range(match: re.Match[str]) -> str:
        c1, r1, c2, r2 = match.groups()
        first_col, last_col = column_index_from_string(c1), column_index_from_string(c2)
        first_row, last_row = int(r1), int(r2)
        total = 0.0
        for row in range(min(first_row, last_row), max(first_row, last_row) + 1):
            for column in range(min(first_col, last_col), max(first_col, last_col) + 1):
                total += _numeric_cell(book, sheet, column, row)
        return repr(total)

    # SUM is the only aggregate used by the official files.  IFERROR and ROUND
    # are accepted for synthetic/source revisions without implementing a full
    # spreadsheet engine.
    while re.search(r"SUM\s*\(", expression, re.I):
        updated = re.sub(
            r"SUM\s*\(\s*([A-Z]{1,3}\$?\d+\s*:\s*\$?[A-Z]{1,3}\$?\d+)\s*\)",
            lambda match: sum_range(_RANGE_RE.search(match.group(1).replace(" ", ""))),
            expression,
            flags=re.I,
        )
        if updated == expression:
            raise ValueError("Unsupported SUM formula")
        expression = updated

    if re.fullmatch(r"IFERROR\s*\(.+\)", expression, re.I):
        inner = expression[expression.find("(") + 1 : -1]
        parts = inner.rsplit(",", 1)
        if len(parts) != 2:
            raise ValueError("Unsupported IFERROR formula")
        try:
            return _evaluate_formula(book, sheet, "=" + parts[0])
        except (ValueError, ZeroDivisionError, TypeError):
            return _safe_arithmetic(parts[1])

    round_match = re.fullmatch(r"ROUND\s*\((.+),\s*(\d+)\s*\)", expression, re.I)
    if round_match:
        return float(round(_evaluate_formula(book, sheet, "=" + round_match.group(1)), int(round_match.group(2))))

    def cell_value(match: re.Match[str]) -> str:
        column, row = match.groups()
        return repr(_numeric_cell(book, sheet, column_index_from_string(column), int(row)))

    expression = _CELL_RE.sub(cell_value, expression)
    return _safe_arithmetic(expression)


def _count_value(
    value: object,
    status: str,
    *,
    dash_is_zero: bool,
) -> tuple[int | None, str]:
    text_value = _text(value)
    if not text_value:
        return None, "missing"
    if text_value in {"-", "–", "—"}:
        return (0, "dash_zero") if dash_is_zero else (None, "not_applicable")
    if isinstance(value, bool):
        return None, "invalid"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "invalid"
    if not math.isfinite(number) or number < 0 or not math.isclose(number, round(number), abs_tol=1e-8):
        return None, "invalid"
    integer = int(round(number))
    if status.startswith("formula_"):
        return integer, status
    return integer, "reported_zero" if integer == 0 else "reported"


def _rate_value(value: object) -> float | None:
    text_value = _text(value)
    if not text_value or text_value in {"-", "–", "—"} or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None
