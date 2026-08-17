"""Load reviewed source-category mappings from country mapping CSV files."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from src.ontology import DiseaseOntology, load_disease_ontology


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING_DIR = ROOT / "configs" / "mapping"

_TARGET_KINDS = {"concept", "group"}
_RELATIONS = {"exact", "narrower", "broader", "aggregate", "related", "ambiguous", "unmapped"}
_COMPARABILITY = {"direct", "conditional", "not_comparable", "unknown"}
_PROJECTION_POLICIES = {"canonical", "discovery_only", "no_projection"}
_AGGREGATION_POLICIES = {"direct_only", "reported_total", "sum_disjoint", "non_additive", "no_rollup"}


class DiseaseMappingConfigError(ValueError):
    """Raised when a reviewed mapping manifest is structurally unsafe."""


@dataclass(frozen=True)
class ReviewedSourceCategoryMapping:
    country_code: str
    source_id: str
    source_code: str
    local_name: str
    target_kind: str
    target_code: str
    mapping_relation: str
    comparability: str
    projection_policy: str
    aggregation_policy: str
    definition_version: str
    notes: str
    source_path: str
    row_number: int

    def evidence(self) -> dict[str, object]:
        return {
            "type": "reviewed_source_category_mapping",
            "source_path": self.source_path,
            "row_number": self.row_number,
            "source_id": self.source_id,
            "source_code": self.source_code,
            "definition_version": self.definition_version,
            "notes": self.notes,
        }


def _value(row: dict[str, str | None], key: str) -> str:
    return str(row.get(key) or "").strip()


def _error(path: Path, row_number: int, message: str) -> DiseaseMappingConfigError:
    return DiseaseMappingConfigError(f"{path}:{row_number}: {message}")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_reviewed_source_category_mappings(
    mapping_dir: Path = DEFAULT_MAPPING_DIR,
    *,
    ontology: DiseaseOntology | None = None,
) -> list[ReviewedSourceCategoryMapping]:
    """Load Registry-enabled rows while leaving legacy-only CSVs untouched.

    Files without ``target_kind`` keep their historical behavior. Once a file
    opts in, every row must explicitly declare either a canonical concept or a
    reviewed no-projection group target.
    """

    registry = ontology or load_disease_ontology()
    document = registry.to_dict()
    concepts = set(registry.concept_ids)
    groups = set(registry.group_ids)
    sources = {item["id"]: item for item in document["sources"]}
    mappings: list[ReviewedSourceCategoryMapping] = []
    identities: dict[tuple[str, str, str], tuple[Path, int]] = {}

    mapping_root = Path(mapping_dir)
    paths = [*mapping_root.glob("*.csv"), *(mapping_root / "reviewed").glob("*.csv")]
    for path in sorted(paths):
        if path.stem.casefold() == "en":
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or ())
            if "target_kind" not in fieldnames:
                continue
            required = {
                "local_name",
                "local_code",
                "source_id",
                "mapping_scope",
                "target_code",
                "mapping_relation",
                "comparability",
                "projection_policy",
                "aggregation_policy",
            }
            missing = sorted(required - fieldnames)
            if missing:
                raise DiseaseMappingConfigError(
                    f"{path}: Registry mapping columns missing: {', '.join(missing)}"
                )

            country_code = path.stem.upper()
            for row_number, raw in enumerate(reader, start=2):
                row = dict(raw)
                target_kind = _value(row, "target_kind").casefold()
                if not target_kind:
                    raise _error(path, row_number, "target_kind is required")
                if target_kind not in _TARGET_KINDS:
                    raise _error(path, row_number, f"invalid target_kind {target_kind!r}")

                source_id = _value(row, "source_id").upper()
                source_code = _value(row, "local_code")
                local_name = _value(row, "local_name")
                target_code = _value(row, "target_code").upper()
                definition_version = _value(row, "definition_version") or "*"
                if not source_id or source_id == "*":
                    raise _error(path, row_number, "a concrete source_id is required")
                if not source_code:
                    raise _error(path, row_number, "local_code is required")
                if not local_name:
                    raise _error(path, row_number, "local_name is required")
                if _value(row, "mapping_scope").casefold() != "source_category_dimension":
                    raise _error(
                        path,
                        row_number,
                        "mapping_scope must be 'source_category_dimension'",
                    )
                source = sources.get(source_id)
                if source is None:
                    raise _error(path, row_number, f"unknown source_id {source_id!r}")
                if str(source.get("country_code") or "").upper() != country_code:
                    raise _error(path, row_number, f"source {source_id} does not belong to {country_code}")

                disease_id = _value(row, "disease_id").upper()
                if target_kind == "concept":
                    if target_code not in concepts:
                        raise _error(path, row_number, f"unknown concept {target_code!r}")
                    if disease_id != target_code:
                        raise _error(path, row_number, "concept rows require disease_id == target_code")
                else:
                    if target_code not in groups:
                        raise _error(path, row_number, f"unknown group {target_code!r}")
                    if disease_id:
                        raise _error(path, row_number, "group rows must leave disease_id empty")

                relation = _value(row, "mapping_relation").casefold()
                comparability = _value(row, "comparability").casefold()
                projection = _value(row, "projection_policy").casefold()
                aggregation = _value(row, "aggregation_policy").casefold()
                if relation not in _RELATIONS:
                    raise _error(path, row_number, f"invalid mapping_relation {relation!r}")
                if comparability not in _COMPARABILITY:
                    raise _error(path, row_number, f"invalid comparability {comparability!r}")
                if projection not in _PROJECTION_POLICIES:
                    raise _error(path, row_number, f"invalid projection_policy {projection!r}")
                if aggregation not in _AGGREGATION_POLICIES:
                    raise _error(path, row_number, f"invalid aggregation_policy {aggregation!r}")
                if projection == "canonical" and not (
                    target_kind == "concept"
                    and relation in {"exact", "narrower"}
                    and comparability in {"direct", "conditional"}
                ):
                    raise _error(
                        path,
                        row_number,
                        "canonical projection requires a comparable exact/narrower concept mapping",
                    )
                if target_kind == "group" and projection != "no_projection":
                    raise _error(path, row_number, "group targets must use no_projection")

                identity = (source_id, source_code.casefold(), definition_version)
                if identity in identities:
                    previous_path, previous_row = identities[identity]
                    raise _error(
                        path,
                        row_number,
                        f"duplicate reviewed source identity; first declared at {previous_path}:{previous_row}",
                    )
                identities[identity] = (path, row_number)
                mappings.append(
                    ReviewedSourceCategoryMapping(
                        country_code=country_code,
                        source_id=source_id,
                        source_code=source_code,
                        local_name=local_name,
                        target_kind=target_kind,
                        target_code=target_code,
                        mapping_relation=relation,
                        comparability=comparability,
                        projection_policy=projection,
                        aggregation_policy=aggregation,
                        definition_version=definition_version,
                        notes=_value(row, "notes"),
                        source_path=_display_path(path),
                        row_number=row_number,
                    )
                )

    return sorted(
        mappings,
        key=lambda item: (
            item.country_code,
            item.source_id,
            item.source_code.casefold(),
            item.definition_version,
        ),
    )


__all__ = [
    "DEFAULT_MAPPING_DIR",
    "DiseaseMappingConfigError",
    "ReviewedSourceCategoryMapping",
    "load_reviewed_source_category_mappings",
]
