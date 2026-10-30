"""Contract comparator for the data-line refactor.

Compares a run's stored observations (and legacy ``disease_records``) against
a fixture of expected rows, keyed on the source natural key so that row
reordering and surplus columns can never mask a real difference.

The observation identity is ``(time, series_code, geography_key,
dimension_key)``; the full compared value adds ``value``, ``suppressed``,
``quality_status`` and the metadata ``missing_value_status`` flag (section 13
of the refactor plan).  Legacy ``disease_records`` compare on
``(time, disease_id, country_id, cases, deaths)``.

Rows may be SQLAlchemy ORM instances, dicts, or any object exposing the
needed fields as attributes or keys.  ``metadata`` is read from either the
``metadata_`` (ORM) or ``metadata`` (JSON/dict) name.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

_MISSING = object()

OBSERVATION_KEY_FIELDS = ("time", "series_code", "geography_key", "dimension_key")
OBSERVATION_VALUE_FIELDS = ("value", "suppressed", "quality_status")
RECORD_KEY_FIELDS = ("time", "disease_id", "country_id")
RECORD_VALUE_FIELDS = ("cases", "deaths")


def _get(row: Any, name: str) -> Any:
    """Read ``name`` from an ORM instance, dict, or duck-typed object."""
    value = getattr(row, name, _MISSING)
    if value is not _MISSING:
        return value
    try:
        return row[name]
    except (TypeError, KeyError, IndexError):
        return _MISSING


def _normalize_time(value: Any) -> Any:
    """Canonical, timezone-independent string for a datetime/date/ISO string.

    Aware datetimes are folded to naive UTC so an equivalent instant written
    with a different offset still compares equal.  Non-time values (``None``)
    pass through unchanged.
    """
    if value is _MISSING or value is None:
        return value
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        return value
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _metadata(row: Any) -> dict:
    for name in ("metadata_", "metadata"):
        value = _get(row, name)
        if isinstance(value, dict):
            return value
    return {}


def observation_key(row: Any) -> tuple:
    """The source natural key of one observation."""
    return (
        _normalize_time(_get(row, "time")),
        _get(row, "series_code"),
        _get(row, "geography_key"),
        _get(row, "dimension_key"),
    )


def observation_fingerprint(row: Any) -> tuple:
    """Everything the contract compares for one observation."""
    return (
        observation_key(row)
        + tuple(_get(row, f) for f in OBSERVATION_VALUE_FIELDS)
        + (_metadata(row).get("missing_value_status"),)
    )


def record_key(row: Any) -> tuple:
    return (
        _normalize_time(_get(row, "time")),
        _get(row, "disease_id"),
        _get(row, "country_id"),
    )


def record_fingerprint(row: Any) -> tuple:
    return record_key(row) + tuple(_get(row, f) for f in RECORD_VALUE_FIELDS)


@dataclass
class Diff:
    """The difference between two row sets.

    ``changed`` holds ``(key, actual_fingerprint, expected_fingerprint)``.
    A duplicate key's value is a tuple of all its fingerprints. Duplicates on
    either side fail the contract, even when both sides contain identical rows.
    ``only_actual``/``only_expected`` hold keys present on exactly one side.
    """

    only_actual: list = field(default_factory=list)
    only_expected: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    duplicate_actual: list = field(default_factory=list)
    duplicate_expected: list = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.only_actual
            or self.only_expected
            or self.changed
            or self.duplicate_actual
            or self.duplicate_expected
        )

    def __bool__(self) -> bool:
        return not self.is_empty

    def render(self, kind: str = "row") -> str:
        lines: list[str] = []
        for side, duplicates in (
            ("actual", self.duplicate_actual),
            ("expected", self.duplicate_expected),
        ):
            if duplicates:
                lines.append(
                    f"{len(duplicates)} duplicate {kind} natural key(s) in {side}:"
                )
                for key, fingerprints in duplicates:
                    lines.append(f"  ! {key}: {len(fingerprints)} rows")
                    lines.extend(f"      {row}" for row in fingerprints)
        if self.only_actual:
            lines.append(
                f"{len(self.only_actual)} {kind}(s) in actual but not expected:"
            )
            lines.extend(f"  + {key}" for key in self.only_actual)
        if self.only_expected:
            lines.append(
                f"{len(self.only_expected)} {kind}(s) in expected but not actual:"
            )
            lines.extend(f"  - {key}" for key in self.only_expected)
        if self.changed:
            lines.append(f"{len(self.changed)} {kind}(s) differ:")
            for key, actual, expected in self.changed:
                lines.append(f"  ~ {key}")
                lines.append(f"      actual:   {actual}")
                lines.append(f"      expected: {expected}")
        return "\n".join(lines)


def _diff(
    actual: Iterable[Any],
    expected: Iterable[Any],
    *,
    key_fn: Callable[[Any], tuple],
    fingerprint_fn: Callable[[Any], tuple],
) -> Diff:
    def index(rows):
        grouped: dict[tuple, list[tuple]] = {}
        for row in rows:
            grouped.setdefault(key_fn(row), []).append(fingerprint_fn(row))
        return {key: sorted(values, key=repr) for key, values in grouped.items()}

    def value(rows):
        return rows[0] if len(rows) == 1 else tuple(rows)

    by_actual, by_expected = index(actual), index(expected)
    only_actual = sorted(set(by_actual) - set(by_expected), key=repr)
    only_expected = sorted(set(by_expected) - set(by_actual), key=repr)
    changed = [
        (key, value(by_actual[key]), value(by_expected[key]))
        for key in sorted(set(by_actual) & set(by_expected), key=repr)
        if by_actual[key] != by_expected[key]
    ]

    def duplicates(index):
        return [
            (key, tuple(index[key]))
            for key in sorted(index, key=repr)
            if len(index[key]) > 1
        ]

    return Diff(
        only_actual,
        only_expected,
        changed,
        duplicates(by_actual),
        duplicates(by_expected),
    )


def diff_observations(actual: Iterable[Any], expected: Iterable[Any]) -> Diff:
    return _diff(
        actual, expected, key_fn=observation_key, fingerprint_fn=observation_fingerprint
    )


def diff_records(actual: Iterable[Any], expected: Iterable[Any]) -> Diff:
    return _diff(actual, expected, key_fn=record_key, fingerprint_fn=record_fingerprint)


def assert_observations_match(actual: Iterable[Any], expected: Iterable[Any]) -> None:
    diff = diff_observations(actual, expected)
    if not diff.is_empty:
        raise AssertionError(diff.render("observation"))


def assert_records_match(actual: Iterable[Any], expected: Iterable[Any]) -> None:
    diff = diff_records(actual, expected)
    if not diff.is_empty:
        raise AssertionError(diff.render("record"))


__all__ = [
    "Diff",
    "assert_observations_match",
    "assert_records_match",
    "diff_observations",
    "diff_records",
    "observation_fingerprint",
    "observation_key",
    "record_fingerprint",
    "record_key",
]
