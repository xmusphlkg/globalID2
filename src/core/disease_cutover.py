"""Validated operational policy for the disease series cutover.

The ontology describes surveillance semantics.  Cutover state is deliberately
kept separate because it is an operational deployment decision that can be
rolled back without changing disease definitions.

Two different grains are supported:

* read policy is resolved for one ``(country_code, concept_id)`` target;
* legacy-write policy is resolved for one ontology ``source_id``.

The built-in defaults preserve the current compatibility behavior: read the
series-first projection with an explicit legacy fallback, compare the strict
series candidate in shadow, and continue atomic dual writes.  A strict
``series_only`` read is intentionally forbidden as a global default.  Likewise,
stopping legacy writes is permitted only for an explicitly declared source
whose source-partition checkpoint and approval have been recorded.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

READ_MODES = frozenset({"legacy", "series_with_fallback", "series_only"})
LEGACY_WRITE_MODES = frozenset({"dual", "compare_only", "off"})

SAFE_DEFAULT_READ_MODE = "series_with_fallback"
SAFE_DEFAULT_SHADOW_COMPARE = True
SAFE_DEFAULT_LEGACY_WRITE_MODE = "dual"

DEFAULT_DISEASE_CUTOVER_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "disease_cutover.json"
)


class DiseaseCutoverConfigError(ValueError):
    """Raised when a disease cutover policy is unsafe or malformed."""


@dataclass(frozen=True)
class CutoverApproval:
    """Human approval attached to a state-changing source cutover."""

    approved_by: str
    approved_at: str
    reason: str | None = None
    change_ref: str | None = None


@dataclass(frozen=True)
class DiseaseReadPolicy:
    """Effective read behavior for one country/concept pair."""

    country_code: str
    concept_id: str
    read_mode: str
    shadow_compare: bool
    required_series: tuple[str, ...] = ()
    allowed_projection_policy: str | None = None
    approval: CutoverApproval | None = None
    notes: str | None = None
    target_override: bool = False

    @property
    def may_query_legacy(self) -> bool:
        """Whether the public read path is allowed to query the legacy table."""

        return self.read_mode != "series_only"

    @property
    def requires_series(self) -> bool:
        return self.read_mode == "series_only"


@dataclass(frozen=True)
class DiseaseWritePolicy:
    """Effective legacy-write behavior for one ontology source."""

    source_id: str
    legacy_write_mode: str
    source_partition_checkpoint: str | dict[str, Any] | None = None
    approval: CutoverApproval | None = None
    notes: str | None = None
    source_override: bool = False

    @property
    def writes_legacy(self) -> bool:
        return self.legacy_write_mode == "dual"

    @property
    def builds_legacy_comparison(self) -> bool:
        return self.legacy_write_mode in {"dual", "compare_only"}


@dataclass(frozen=True)
class _TargetOverride:
    country_code: str
    concept_id: str
    read_mode: str | None
    shadow_compare: bool | None
    required_series: tuple[str, ...]
    allowed_projection_policy: str | None
    approval: CutoverApproval | None
    notes: str | None


@dataclass(frozen=True)
class _SourceOverride:
    source_id: str
    legacy_write_mode: str
    source_partition_checkpoint: str | dict[str, Any] | None
    approval: CutoverApproval | None
    notes: str | None


@dataclass(frozen=True)
class DiseaseCutoverConfig:
    """Immutable, validated disease cutover configuration."""

    schema_version: int
    release_version: str
    default_read_mode: str
    default_shadow_compare: bool
    default_legacy_write_mode: str
    _targets: Mapping[tuple[str, str], _TargetOverride]
    _sources: Mapping[str, _SourceOverride]

    @classmethod
    def safe_default(cls) -> "DiseaseCutoverConfig":
        """Return a policy that cannot disable either compatibility layer."""

        return cls(
            schema_version=1,
            release_version="built-in-safe-default",
            default_read_mode=SAFE_DEFAULT_READ_MODE,
            default_shadow_compare=SAFE_DEFAULT_SHADOW_COMPARE,
            default_legacy_write_mode=SAFE_DEFAULT_LEGACY_WRITE_MODE,
            _targets={},
            _sources={},
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DiseaseCutoverConfig":
        document = _mapping(value, "disease cutover config")
        _reject_unknown_keys(
            document,
            {
                "schema_version",
                "release_version",
                "defaults",
                "targets",
                "sources",
            },
            "disease cutover config",
        )

        schema_version = document.get("schema_version")
        if schema_version != 1:
            raise DiseaseCutoverConfigError(
                "disease cutover config.schema_version must be 1"
            )
        release_version = _required_text(
            document.get("release_version"), "disease cutover config.release_version"
        )

        defaults = _mapping(document.get("defaults"), "defaults")
        _reject_unknown_keys(
            defaults,
            {"read_mode", "shadow_compare", "legacy_write_mode"},
            "defaults",
        )
        default_read_mode = _read_mode(
            defaults.get("read_mode", SAFE_DEFAULT_READ_MODE),
            "defaults.read_mode",
        )
        if default_read_mode == "series_only":
            raise DiseaseCutoverConfigError(
                "series_only may be configured only for an explicit "
                "country/concept target"
            )
        default_shadow_compare = _boolean(
            defaults.get("shadow_compare", SAFE_DEFAULT_SHADOW_COMPARE),
            "defaults.shadow_compare",
        )
        default_write_mode = _write_mode(
            defaults.get("legacy_write_mode", SAFE_DEFAULT_LEGACY_WRITE_MODE),
            "defaults.legacy_write_mode",
        )
        if default_write_mode != "dual":
            raise DiseaseCutoverConfigError(
                "defaults.legacy_write_mode must remain dual; non-dual writes "
                "require an explicit source checkpoint and approval"
            )

        targets: dict[tuple[str, str], _TargetOverride] = {}
        for index, raw_target in enumerate(
            _list(document.get("targets", []), "targets")
        ):
            path = f"targets[{index}]"
            target = _parse_target(raw_target, path)
            key = (target.country_code, target.concept_id)
            if key in targets:
                raise DiseaseCutoverConfigError(
                    f"duplicate disease cutover target: {key[0]}/{key[1]}"
                )
            targets[key] = target

        sources: dict[str, _SourceOverride] = {}
        for index, raw_source in enumerate(
            _list(document.get("sources", []), "sources")
        ):
            path = f"sources[{index}]"
            source = _parse_source(raw_source, path)
            if source.source_id in sources:
                raise DiseaseCutoverConfigError(
                    f"duplicate disease cutover source: {source.source_id}"
                )
            sources[source.source_id] = source

        return cls(
            schema_version=1,
            release_version=release_version,
            default_read_mode=default_read_mode,
            default_shadow_compare=default_shadow_compare,
            default_legacy_write_mode=default_write_mode,
            _targets=targets,
            _sources=sources,
        )

    def resolve_read_policy(
        self, country_code: str, concept_id: str
    ) -> DiseaseReadPolicy:
        country = _identity(country_code, "country_code").upper()
        concept = _identity(concept_id, "concept_id").upper()
        override = self._targets.get((country, concept))
        if override is None:
            return DiseaseReadPolicy(
                country_code=country,
                concept_id=concept,
                read_mode=self.default_read_mode,
                shadow_compare=self.default_shadow_compare,
            )
        return DiseaseReadPolicy(
            country_code=country,
            concept_id=concept,
            read_mode=override.read_mode or self.default_read_mode,
            shadow_compare=(
                self.default_shadow_compare
                if override.shadow_compare is None
                else override.shadow_compare
            ),
            required_series=override.required_series,
            allowed_projection_policy=override.allowed_projection_policy,
            approval=override.approval,
            notes=override.notes,
            target_override=True,
        )

    def resolve_write_policy(self, source_id: str) -> DiseaseWritePolicy:
        source = _identity(source_id, "source_id").upper()
        override = self._sources.get(source)
        if override is None:
            return DiseaseWritePolicy(
                source_id=source,
                legacy_write_mode=self.default_legacy_write_mode,
            )
        return DiseaseWritePolicy(
            source_id=source,
            legacy_write_mode=override.legacy_write_mode,
            source_partition_checkpoint=deepcopy(override.source_partition_checkpoint),
            approval=override.approval,
            notes=override.notes,
            source_override=True,
        )

    def configured_read_policies(self) -> tuple[DiseaseReadPolicy, ...]:
        """Return explicit targets in stable order for audits and health APIs."""

        return tuple(
            self.resolve_read_policy(country_code, concept_id)
            for country_code, concept_id in sorted(self._targets)
        )

    def configured_write_policies(self) -> tuple[DiseaseWritePolicy, ...]:
        """Return explicit source cutovers in stable order."""

        return tuple(
            self.resolve_write_policy(source_id) for source_id in sorted(self._sources)
        )

    def operational_summary(self) -> dict[str, Any]:
        """Return a secret-free, JSON-ready deployment status."""

        read_policies = self.configured_read_policies()
        write_policies = self.configured_write_policies()
        return {
            "schema_version": self.schema_version,
            "release_version": self.release_version,
            "defaults": {
                "read_mode": self.default_read_mode,
                "shadow_compare": self.default_shadow_compare,
                "legacy_write_mode": self.default_legacy_write_mode,
            },
            "target_count": len(read_policies),
            "series_only_target_count": sum(
                policy.read_mode == "series_only" for policy in read_policies
            ),
            "source_override_count": len(write_policies),
            "legacy_write_off_source_count": sum(
                policy.legacy_write_mode == "off" for policy in write_policies
            ),
        }

    # Short aliases make call sites readable while retaining explicit method
    # names for discoverability.
    read_policy = resolve_read_policy
    write_policy = resolve_write_policy


def load_disease_cutover_config(
    path: str | Path = DEFAULT_DISEASE_CUTOVER_PATH,
) -> DiseaseCutoverConfig:
    """Load and validate one disease cutover JSON document."""

    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DiseaseCutoverConfigError(
            f"disease cutover config not found: {config_path}"
        ) from None
    except json.JSONDecodeError as exc:
        raise DiseaseCutoverConfigError(
            f"invalid disease cutover JSON at {config_path}: {exc}"
        ) from exc
    return DiseaseCutoverConfig.from_mapping(payload)


load_disease_cutover = load_disease_cutover_config


@lru_cache(maxsize=1)
def get_disease_cutover_config() -> DiseaseCutoverConfig:
    """Return the process-wide validated cutover policy."""

    return load_disease_cutover_config()


def _parse_target(value: object, path: str) -> _TargetOverride:
    item = _mapping(value, path)
    _reject_unknown_keys(
        item,
        {
            "country_code",
            "concept_id",
            "read_mode",
            "shadow_compare",
            "required_series",
            "allowed_projection_policy",
            "approval",
            "notes",
        },
        path,
    )
    country_code = _identity(item.get("country_code"), f"{path}.country_code").upper()
    concept_id = _identity(item.get("concept_id"), f"{path}.concept_id").upper()
    read_mode = (
        _read_mode(item["read_mode"], f"{path}.read_mode")
        if "read_mode" in item
        else None
    )
    shadow_compare = (
        _boolean(item["shadow_compare"], f"{path}.shadow_compare")
        if "shadow_compare" in item
        else None
    )
    required_series = _unique_text_tuple(
        item.get("required_series", []), f"{path}.required_series"
    )
    allowed_projection_policy = _optional_text(
        item.get("allowed_projection_policy"),
        f"{path}.allowed_projection_policy",
    )
    approval = _approval(
        item.get("approval"),
        f"{path}.approval",
        required=read_mode == "series_only",
    )
    if read_mode == "series_only" and not required_series:
        raise DiseaseCutoverConfigError(
            f"{path}.required_series is required for series_only"
        )
    if read_mode == "series_only" and not allowed_projection_policy:
        raise DiseaseCutoverConfigError(
            f"{path}.allowed_projection_policy is required for series_only"
        )
    notes = _optional_text(item.get("notes"), f"{path}.notes")
    return _TargetOverride(
        country_code=country_code,
        concept_id=concept_id,
        read_mode=read_mode,
        shadow_compare=shadow_compare,
        required_series=required_series,
        allowed_projection_policy=allowed_projection_policy,
        approval=approval,
        notes=notes,
    )


def _parse_source(value: object, path: str) -> _SourceOverride:
    item = _mapping(value, path)
    _reject_unknown_keys(
        item,
        {
            "source_id",
            "legacy_write_mode",
            "source_partition_checkpoint",
            "approval",
            "notes",
        },
        path,
    )
    source_id = _identity(item.get("source_id"), f"{path}.source_id").upper()
    write_mode = _write_mode(item.get("legacy_write_mode"), f"{path}.legacy_write_mode")
    checkpoint = _checkpoint(
        item.get("source_partition_checkpoint"),
        f"{path}.source_partition_checkpoint",
    )
    approval = _approval(
        item.get("approval"),
        f"{path}.approval",
        required=write_mode != "dual",
    )
    if write_mode != "dual" and checkpoint is None:
        raise DiseaseCutoverConfigError(
            f"{path} uses {write_mode!r} but has no source_partition_checkpoint"
        )
    notes = _optional_text(item.get("notes"), f"{path}.notes")
    return _SourceOverride(
        source_id=source_id,
        legacy_write_mode=write_mode,
        source_partition_checkpoint=checkpoint,
        approval=approval,
        notes=notes,
    )


def _approval(value: object, path: str, *, required: bool) -> CutoverApproval | None:
    if value is None:
        if required:
            raise DiseaseCutoverConfigError(f"{path} is required")
        return None
    item = _mapping(value, path)
    _reject_unknown_keys(
        item,
        {"approved_by", "approved_at", "reason", "change_ref"},
        path,
    )
    approved_by = _required_text(item.get("approved_by"), f"{path}.approved_by")
    approved_at = _required_text(item.get("approved_at"), f"{path}.approved_at")
    _iso_date_or_datetime(approved_at, f"{path}.approved_at")
    return CutoverApproval(
        approved_by=approved_by,
        approved_at=approved_at,
        reason=_optional_text(item.get("reason"), f"{path}.reason"),
        change_ref=_optional_text(item.get("change_ref"), f"{path}.change_ref"),
    )


def _checkpoint(value: object, path: str) -> str | dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise DiseaseCutoverConfigError(f"{path} must not be empty")
        return text
    if isinstance(value, Mapping):
        checkpoint = dict(value)
        if not checkpoint:
            raise DiseaseCutoverConfigError(f"{path} must not be empty")
        return deepcopy(checkpoint)
    raise DiseaseCutoverConfigError(f"{path} must be a string or object")


def _read_mode(value: object, path: str) -> str:
    mode = _required_text(value, path).casefold()
    if mode not in READ_MODES:
        raise DiseaseCutoverConfigError(
            f"{path} must be one of: {', '.join(sorted(READ_MODES))}"
        )
    return mode


def _write_mode(value: object, path: str) -> str:
    mode = _required_text(value, path).casefold()
    if mode not in LEGACY_WRITE_MODES:
        raise DiseaseCutoverConfigError(
            f"{path} must be one of: {', '.join(sorted(LEGACY_WRITE_MODES))}"
        )
    return mode


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise DiseaseCutoverConfigError(f"{path} must be a boolean")
    return value


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DiseaseCutoverConfigError(f"{path} must be an object")
    return dict(value)


def _list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DiseaseCutoverConfigError(f"{path} must be a list")
    return value


def _required_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiseaseCutoverConfigError(f"{path} must be a non-empty string")
    return value.strip()


def _identity(value: object, path: str) -> str:
    text = _required_text(value, path)
    if any(character.isspace() for character in text):
        raise DiseaseCutoverConfigError(f"{path} must not contain whitespace")
    return text


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, path)


def _unique_text_tuple(value: object, path: str) -> tuple[str, ...]:
    items = _list(value, path)
    result: list[str] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(items):
        item = _identity(raw_item, f"{path}[{index}]")
        if item in seen:
            raise DiseaseCutoverConfigError(f"{path} contains duplicate {item!r}")
        seen.add(item)
        result.append(item)
    return tuple(result)


def _iso_date_or_datetime(value: str, path: str) -> None:
    try:
        if "T" in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            date.fromisoformat(value)
    except ValueError as exc:
        raise DiseaseCutoverConfigError(
            f"{path} must be an ISO-8601 date or datetime"
        ) from exc


def _reject_unknown_keys(
    value: Mapping[str, Any], allowed: set[str], path: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DiseaseCutoverConfigError(
            f"{path} contains unsupported field(s): {', '.join(unknown)}"
        )


__all__ = [
    "DEFAULT_DISEASE_CUTOVER_PATH",
    "LEGACY_WRITE_MODES",
    "READ_MODES",
    "CutoverApproval",
    "DiseaseCutoverConfig",
    "DiseaseCutoverConfigError",
    "DiseaseReadPolicy",
    "DiseaseWritePolicy",
    "get_disease_cutover_config",
    "load_disease_cutover",
    "load_disease_cutover_config",
]
