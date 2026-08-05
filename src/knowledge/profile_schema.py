"""Configuration-driven disease knowledge profile schemas.

The storage model keeps stable field names for compatibility.  A profile
schema defines the semantic label and applicability of each field so an injury,
occupational condition, or classification bucket is not graded as though it
were an infectious disease.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


SECTION_FIELDS = (
    "definition",
    "clinical_features",
    "epidemiology",
    "transmission",
    "prevention",
    "surveillance_note",
    "risk_groups",
)
DEFAULT_PROFILE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "knowledge_profile_schemas.json"
)


@dataclass(frozen=True)
class KnowledgeProfileSchema:
    profile_type: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    not_applicable_fields: tuple[str, ...]
    labels: dict[str, dict[str, str]]

    @property
    def applicable_fields(self) -> tuple[str, ...]:
        return tuple(
            field
            for field in SECTION_FIELDS
            if field not in self.not_applicable_fields
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_type": self.profile_type,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "not_applicable_fields": list(self.not_applicable_fields),
            "applicable_fields": list(self.applicable_fields),
            "labels": self.labels,
        }


@lru_cache(maxsize=1)
def _profile_config() -> dict[str, Any]:
    with DEFAULT_PROFILE_SCHEMA_PATH.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if document.get("schema_version") != 1:
        raise ValueError("knowledge profile schema_version must be 1")
    return document


def resolve_knowledge_profile_schema(disease: Any) -> KnowledgeProfileSchema:
    """Resolve an entity profile without disease-ID-specific branches."""
    explicit = _value(disease, "knowledge_profile_type") or _value(disease, "profile_type")
    config = _profile_config()
    profile_type = explicit if explicit in config["schemas"] else None
    if profile_type is None:
        for rule in config.get("rules") or []:
            fields = rule.get("fields") or (
                "name_en",
                "standard_name_en",
                "name_zh",
                "standard_name_zh",
                "category",
                "description",
            )
            corpus = " ".join(_value(disease, str(key)) for key in fields).strip()
            if any(re.search(pattern, corpus, flags=re.I) for pattern in rule.get("patterns") or []):
                profile_type = str(rule["profile_type"])
                break
    profile_type = profile_type or str(config["default_profile_type"])
    raw = config["schemas"][profile_type]
    _validate_fields(profile_type, raw)
    return KnowledgeProfileSchema(
        profile_type=profile_type,
        required_fields=tuple(raw.get("required_fields") or ()),
        optional_fields=tuple(raw.get("optional_fields") or ()),
        not_applicable_fields=tuple(raw.get("not_applicable_fields") or ()),
        labels={str(key): dict(value) for key, value in (raw.get("labels") or {}).items()},
    )


def profile_schema_from_payload(payload: Any) -> KnowledgeProfileSchema:
    """Read a persisted schema, falling back to profile inference/defaults."""
    metadata = _raw_value(payload, "metadata")
    if not isinstance(metadata, dict):
        metadata = _raw_value(payload, "metadata_")
    raw = metadata.get("profile_schema") if isinstance(metadata, dict) else None
    if isinstance(raw, dict) and raw.get("profile_type"):
        profile_type = str(raw["profile_type"])
        config_schema = _profile_config().get("schemas", {}).get(profile_type)
        if isinstance(config_schema, dict):
            merged = {**config_schema, **raw}
            _validate_fields(profile_type, merged)
            return KnowledgeProfileSchema(
                profile_type=profile_type,
                required_fields=tuple(merged.get("required_fields") or ()),
                optional_fields=tuple(merged.get("optional_fields") or ()),
                not_applicable_fields=tuple(merged.get("not_applicable_fields") or ()),
                labels={str(key): dict(value) for key, value in (merged.get("labels") or {}).items()},
            )
    return resolve_knowledge_profile_schema(payload)


def attach_profile_schema(disease: dict[str, Any]) -> dict[str, Any]:
    schema = resolve_knowledge_profile_schema(disease)
    return {**disease, "knowledge_profile_type": schema.profile_type, "profile_schema": schema.to_dict()}


def _validate_fields(profile_type: str, raw: dict[str, Any]) -> None:
    required = set(raw.get("required_fields") or ())
    optional = set(raw.get("optional_fields") or ())
    not_applicable = set(raw.get("not_applicable_fields") or ())
    unknown = (required | optional | not_applicable) - set(SECTION_FIELDS)
    overlap = (required & optional) | (required & not_applicable) | (optional & not_applicable)
    if unknown or overlap or required | optional | not_applicable != set(SECTION_FIELDS):
        raise ValueError(
            f"invalid knowledge profile schema {profile_type}: unknown={sorted(unknown)}, overlap={sorted(overlap)}"
        )


def _value(obj: Any, key: str) -> str:
    value = _raw_value(obj, key)
    return str(value).strip() if value not in (None, "") else ""


def _raw_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
