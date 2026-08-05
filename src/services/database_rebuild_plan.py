"""Pure rebuild planning and input validation for database rebuilds.

This module deliberately contains no database access or user interaction.  The
destructive workflow in ``scripts/full_rebuild_database.py`` consumes these
plans without changing its existing execution or transaction boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd


REBUILD_OPTION_KEYS = (
    "clear_data",
    "import_standard",
    "import_mappings",
    "sync_diseases",
    "import_history",
)

US_NNDSS_SOURCE_NAME = "US CDC NNDSS"
US_NNDSS_RESIDENT_ALIASES = frozenset(
    {
        "us residents",
        "u.s. residents",
        "united states residents",
    }
)


@dataclass(frozen=True)
class RebuildPlan:
    """Validated, immutable description of a database rebuild mode."""

    mode: str
    clear_data: bool
    import_standard: bool
    import_mappings: bool
    sync_diseases: bool
    import_history: bool
    tables_to_clear: tuple[str, ...]
    preserved_tables: tuple[str, ...]
    deletion_tables: tuple[str, ...]

    def options(self) -> dict[str, bool]:
        """Return the legacy option mapping consumed by the rebuild script."""
        return {key: getattr(self, key) for key in REBUILD_OPTION_KEYS}


_MODE_OPTIONS: dict[str, dict[str, bool]] = {
    "full": {
        "clear_data": True,
        "import_standard": True,
        "import_mappings": True,
        "sync_diseases": True,
        "import_history": True,
    },
    "mappings": {
        "clear_data": True,
        "import_standard": True,
        "import_mappings": True,
        "sync_diseases": True,
        "import_history": False,
    },
    "history": {
        "clear_data": True,
        "import_standard": False,
        "import_mappings": False,
        "sync_diseases": False,
        "import_history": True,
    },
}


def build_rebuild_plan(
    mode: str,
    custom_options: Mapping[str, bool] | None = None,
) -> RebuildPlan:
    """Build a validated plan while preserving the script's legacy semantics."""
    if mode not in (*_MODE_OPTIONS, "custom"):
        allowed = ", ".join((*_MODE_OPTIONS, "custom"))
        raise ValueError(f"Unsupported rebuild mode {mode!r}; expected one of: {allowed}")

    if mode == "custom":
        if custom_options is None:
            raise ValueError("Custom rebuild mode requires explicit step options")
        missing = set(REBUILD_OPTION_KEYS) - set(custom_options)
        unknown = set(custom_options) - set(REBUILD_OPTION_KEYS)
        if missing or unknown:
            details = []
            if missing:
                details.append(f"missing: {', '.join(sorted(missing))}")
            if unknown:
                details.append(f"unknown: {', '.join(sorted(unknown))}")
            raise ValueError("Invalid custom rebuild options (" + "; ".join(details) + ")")
        invalid = [key for key in REBUILD_OPTION_KEYS if type(custom_options[key]) is not bool]
        if invalid:
            raise TypeError(
                "Custom rebuild options must be booleans: " + ", ".join(invalid)
            )
        options = {key: custom_options[key] for key in REBUILD_OPTION_KEYS}
    else:
        if custom_options is not None:
            raise ValueError("Custom step options may only be used with custom mode")
        options = _MODE_OPTIONS[mode]

    if mode == "history":
        tables_to_clear = ("disease_records",)
        preserved_tables = (
            "diseases",
            "disease_mappings",
            "standard_diseases",
            "crawl_runs",
            "crawl_raw_pages",
        )
        deletion_tables = ("disease_records",)
    elif mode == "mappings":
        tables_to_clear = ("disease_mappings", "standard_diseases")
        preserved_tables = (
            "disease_records (历史数据)",
            "crawl_runs",
            "crawl_raw_pages",
        )
        deletion_tables = ("disease_mappings",)
    else:
        tables_to_clear = (
            "disease_records",
            "diseases",
            "disease_mappings",
            "standard_diseases",
        )
        preserved_tables = ("crawl_runs", "crawl_raw_pages")
        deletion_tables = (
            "disease_records",
            "disease_mappings",
            "disease_learning_suggestions",
        )

    return RebuildPlan(
        mode=mode,
        tables_to_clear=tables_to_clear,
        preserved_tables=preserved_tables,
        deletion_tables=deletion_tables,
        **options,
    )


def validate_us_nndss_history_scope(df: pd.DataFrame) -> None:
    """Refuse a legacy US rebuild containing broader NNDSS Total rows."""
    if "Source" not in df.columns:
        return
    source_mask = (
        df["Source"].fillna("").astype(str).str.strip().str.casefold()
        == US_NNDSS_SOURCE_NAME.casefold()
    )
    if not source_mask.any():
        return
    if "ReportingArea" not in df.columns:
        raise ValueError(
            "US NNDSS history requires ReportingArea evidence for the legacy "
            "national projection"
        )
    areas = (
        df.loc[source_mask, "ReportingArea"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
    )
    invalid = sorted(set(areas) - US_NNDSS_RESIDENT_ALIASES)
    if invalid:
        raise ValueError(
            "US NNDSS legacy history contains non-resident reporting scopes: "
            + ", ".join(repr(value) for value in invalid[:20])
        )
