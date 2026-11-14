"""Normalize source identities, reporting periods, values and revision semantics."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any, Mapping

_SUPPRESSED_VALUES = {"*", "suppressed", "suppression", "<5", "-"}
_SUPPRESSED_THRESHOLD = re.compile(r"^<\s*\d+\s*$")


def _as_utc(value: object) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if not isinstance(value, datetime):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _advance_period(value: datetime, frequency: str) -> datetime | None:
    if frequency == "daily":
        return value + timedelta(days=1)
    if frequency == "weekly":
        return value + timedelta(days=7)
    if frequency in {"monthly", "quarterly"}:
        step = 1 if frequency == "monthly" else 3
        zero_based = value.month - 1 + step
        year = value.year + zero_based // 12
        month = zero_based % 12 + 1
        day = min(value.day, _days_in_month(year, month))
        return value.replace(year=year, month=month, day=day)
    if frequency == "annual":
        try:
            return value.replace(year=value.year + 1)
        except ValueError:
            return value.replace(year=value.year + 1, month=2, day=28)
    return None


def _previous_period(value: datetime, frequency: str) -> datetime | None:
    if frequency == "daily":
        return value - timedelta(days=1)
    if frequency == "weekly":
        return value - timedelta(days=7)
    if frequency in {"monthly", "quarterly"}:
        step = 1 if frequency == "monthly" else 3
        zero_based = value.month - 1 - step
        year = value.year + zero_based // 12
        month = zero_based % 12 + 1
        day = min(value.day, _days_in_month(year, month))
        return value.replace(year=year, month=month, day=day)
    if frequency == "annual":
        try:
            return value.replace(year=value.year - 1)
        except ValueError:
            return value.replace(year=value.year - 1, month=2, day=28)
    return None


def _periods_are_consecutive(
    earlier: datetime, later: datetime, frequency: str
) -> bool:
    expected = _advance_period(earlier, frequency)
    return expected == later if expected is not None else False


def _period_range(start: datetime, end: datetime, frequency: str) -> list[datetime]:
    result = [start]
    current = start
    # Hard cap protects the quality reporter from malformed century-spanning
    # input while still covering every supported surveillance history.
    for _ in range(5000):
        if current >= end:
            break
        following = _advance_period(current, frequency)
        if following is None or following <= current or following > end:
            break
        result.append(following)
        current = following
    return result


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, month + 1, 1)
    return (following - timedelta(days=1)).day


def _parse_report_time(row: dict[str, Any]) -> datetime | None:
    value = row.get("Date") or row.get("date") or row.get("time")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if value:
        text = str(value).strip()
        for parser in (datetime.fromisoformat,):
            try:
                parsed = parser(text.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

    mmwr_year = _first_text(row, "MMWRYear", "Current MMWR Year")
    mmwr_week = _first_text(row, "MMWRWeek", "MMWR WEEK")
    if mmwr_year and mmwr_week:
        try:
            year = int(float(mmwr_year))
            week = int(float(mmwr_week))
            if not 1 <= week <= 53:
                return None
            return datetime.combine(
                _mmwr_week_end_date(year, week), time.min, tzinfo=timezone.utc
            )
        except (OverflowError, TypeError, ValueError):
            return None

    try:
        year = int(row.get("Year"))
        month = int(row.get("Month"))
        return datetime.combine(
            datetime(year, month, 1).date(), time.min, tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def _mmwr_week_end_date(year: int, week: int) -> date:
    """Return the Sunday ending a Japanese IDWR epidemiological week.

    IDWR weeks run Monday through Sunday and follow ISO week-year numbering.
    In particular, 2015 week 53 ends on 2016-01-03 and must not collide with
    2016 week 1, which ends on 2016-01-10.
    """

    return date.fromisocalendar(year, week, 7)


def _parse_value(value: object) -> tuple[float | None, bool]:
    text = "" if value is None else str(value).strip()
    if (
        text.casefold() in _SUPPRESSED_VALUES
        or _SUPPRESSED_THRESHOLD.fullmatch(text) is not None
    ):
        return None, True
    if not text:
        return None, False
    try:
        numeric = float(text.replace(",", ""))
        return (numeric, False) if math.isfinite(numeric) else (None, False)
    except ValueError:
        return None, False


def _first_text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return None


def _normalized_identity(value: object) -> str:
    """Return a stable, whitespace-insensitive identity for source labels."""

    return " ".join(str(value or "").split()).casefold()


def _dimension_key(dimensions: dict[str, Any]) -> str:
    if not dimensions:
        return "all"
    canonical = json.dumps(
        dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_dimensions(value: object) -> dict[str, Any] | None:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return _json_safe(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _json_safe(parsed) if isinstance(parsed, dict) else None
    return None


def _source_id_for_row(
    row: Mapping[str, Any], source_id: str | Mapping[str, str] | None
) -> str | None:
    if not isinstance(source_id, Mapping):
        return source_id
    source_name = _first_text(dict(row), "Source", "source", "DataSource")
    if not source_name:
        return None
    normalized = source_name.casefold().strip()
    return next(
        (
            registry_id
            for configured_name, registry_id in source_id.items()
            if str(configured_name).casefold().strip() == normalized
        ),
        None,
    )


def _geography_key(
    row: dict[str, Any], country_code: str, *, source_id: str | None = None
) -> str:
    explicit = _first_text(row, "GeographyKey", "geography_key")
    reporting_area = _first_text(
        row, "ReportingArea", "Reporting Area", "states", "Province"
    )
    normalized_area = (reporting_area or "").casefold()
    if source_id == "SRC_US_NNDSS":
        if normalized_area == "total":
            expected = "source:SRC_US_NNDSS:reporting-area:total"
        elif normalized_area in {
            "us residents",
            "u.s. residents",
            "united states residents",
        }:
            expected = "country:US:national"
        else:
            jurisdiction = (
                _first_text(row, "JurisdictionCode", "jurisdiction_code") or ""
            ).upper()
            expected = f"country:{jurisdiction}:national"
            if (
                not re.fullmatch(r"US-[A-Z]{2}", jurisdiction)
                or not reporting_area
                or explicit != expected
            ):
                raise ValueError(
                    "Unsupported SRC_US_NNDSS ReportingArea; expected TOTAL, "
                    "US RESIDENTS, or a state/territory row with an explicit "
                    f"jurisdiction GeographyKey, received {reporting_area!r}"
                )
        if explicit and explicit != expected:
            raise ValueError(
                "Explicit geography_key conflicts with SRC_US_NNDSS "
                f"ReportingArea: expected {expected!r}, received {explicit!r}"
            )
        return explicit or expected
    if explicit:
        return explicit
    national_aliases = {
        "total",
        "national",
        "総数",
        "全国",
    }
    if not reporting_area or normalized_area in national_aliases:
        return f"country:{country_code.upper()}:national"
    geocode = _first_text(row, "Geocode", "geocode")
    identifier = geocode or reporting_area.casefold().replace(" ", "-")
    return f"country:{country_code.upper()}:source-area:{identifier}"


def _series_valid_at(series: dict[str, Any], report_time: datetime) -> bool:
    if series["status"] not in {"active", "historical"}:
        return False
    report_date = report_time.date()
    valid_from = series.get("valid_from")
    valid_to = series.get("valid_to")
    return not (
        (valid_from and report_date < datetime.fromisoformat(valid_from).date())
        or (valid_to and report_date > datetime.fromisoformat(valid_to).date())
    )


def _canonical_source_code(
    series: Mapping[str, Any],
    *,
    explicit_local_code: str | None,
    local_label: str | None,
) -> str | None:
    """Return a unique canonical code for one resolved ontology series."""

    codes = [str(value).strip() for value in series.get("local_codes", []) if str(value).strip()]
    labels = [str(value).strip() for value in series.get("local_labels", []) if str(value).strip()]
    if explicit_local_code:
        normalized_code = explicit_local_code.casefold()
        canonical_matches = [code for code in codes if code.casefold() == normalized_code]
        if len(canonical_matches) == 1:
            return canonical_matches[0]
        series_id = str(series.get("id") or "").strip()
        if not codes and series_id.casefold() == normalized_code:
            return series_id
        return None

    if len(codes) == 1:
        return codes[0]
    if not codes:
        return str(series.get("id") or "").strip() or None
    if local_label and len(labels) == len(codes):
        normalized_label = local_label.casefold()
        paired_codes = [
            codes[index]
            for index, label in enumerate(labels)
            if label.casefold() == normalized_label
        ]
        if len(set(paired_codes)) == 1:
            return paired_codes[0]
    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _quality_status(row: dict[str, Any]) -> str:
    status = " ".join(
        str(row.get(key) or "")
        for key in ("DatasetStatus", "DataStatus", "IsProvisional", "UpdateMode")
    ).casefold()
    if "final" in status:
        return "final"
    if any(marker in status for marker in ("revised", "revision", "corrected")):
        return "revised"
    if any(marker in status for marker in ("provisional", "preliminary", "true")):
        return "provisional"
    return "raw"


def _allows_authoritative_revision(row: Mapping[str, Any]) -> bool:
    explicit = row.get("AuthoritativeRevision", row.get("authoritative_revision"))
    if isinstance(explicit, bool):
        return explicit
    if str(explicit or "").strip().casefold() in {"1", "true", "yes"}:
        return True
    status = " ".join(
        str(row.get(key) or "")
        for key in ("DatasetStatus", "UpdateMode")
    ).casefold()
    if any(marker in status for marker in ("revised", "revision", "corrected")):
        return True
    semantics = " ".join(
        str(row.get(key) or "")
        for key in ("RevisionSemantics", "revision_semantics", "CorrectionType")
    ).strip().casefold()
    return semantics in {
        "authoritative_revision",
        "source_correction",
        "restatement",
    }


def _allows_equal_quality_overwrite(row: Mapping[str, Any]) -> bool:
    explicit = row.get(
        "AllowEqualQualityOverwrite",
        row.get("allow_equal_quality_overwrite"),
    )
    if explicit is None:
        # Preserve the established behavior for sources that have not opted
        # into replay protection. CA official-file replays set this false.
        return True
    if isinstance(explicit, bool):
        return explicit
    return str(explicit).strip().casefold() in {"1", "true", "yes"}


def _observation_allows_authoritative_revision(row: Mapping[str, Any]) -> bool:
    metadata = row.get("metadata") or row.get("metadata_") or {}
    if isinstance(metadata, Mapping) and metadata.get("authoritative_revision") is True:
        return True
    raw_data = row.get("raw_data") or {}
    return isinstance(raw_data, Mapping) and _allows_authoritative_revision(raw_data)
