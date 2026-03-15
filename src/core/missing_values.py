"""Utilities for normalizing sentinel missing values in epidemiology data."""

from __future__ import annotations

import math
from typing import Any, Iterable

import pandas as pd


DEFAULT_RATE_COLUMNS: tuple[str, ...] = (
    "incidence_rate",
    "mortality_rate",
    "Incidence",
    "Mortality",
    "IncidenceRate",
    "MortalityRate",
)


def normalize_rate_value(value: Any) -> float | None:
    """Return None for missing/sentinel rate values and a float otherwise."""
    if value is None or pd.isna(value):
        return None

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(numeric) or numeric < 0:
        return None

    return numeric


def normalize_rate_columns(
    df: pd.DataFrame,
    columns: Iterable[str] | None = None,
    *,
    copy: bool = True,
) -> pd.DataFrame:
    """Replace sentinel negative rate values in known rate columns with missing values."""
    if df is None:
        return df

    result = df.copy() if copy else df
    target_columns = tuple(columns or DEFAULT_RATE_COLUMNS)

    for column in target_columns:
        if column not in result.columns:
            continue

        numeric = pd.to_numeric(result[column], errors="coerce")
        result[column] = numeric.where(numeric.notna() & (numeric >= 0))

    return result