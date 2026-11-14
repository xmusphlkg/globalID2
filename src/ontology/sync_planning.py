"""Pure plans for reconciling ontology mappings; callers own I/O and transactions."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from src.data.processors.mapping_lookup import normalize_mapping_key
from .sync_contracts import MappingTransition
from .sync_migrations.br_2026_08_ntra import json_object as _json_object


def mapping_target_changes(
    existing_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]],
    *, declared_transition: Callable[..., MappingTransition | None],
) -> list[dict[str, Any]]:
    """Compare active DB semantics with the authoritative source-aware config."""

    incoming_by_label: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in incoming_rows:
        key = (
            str(row["country_code"]).upper(),
            normalize_mapping_key(row["local_name"]),
        )
        incoming_by_label.setdefault(key, []).append(row)

    changes: dict[tuple[int, str, str], dict[str, Any]] = {}
    for existing in existing_rows:
        country_code = str(existing["country_code"]).upper()
        old_id = str(existing["disease_id"]).upper()
        old_source = str(existing.get("source_id") or "*").strip().upper() or "*"
        key = (country_code, normalize_mapping_key(existing["local_name"]))
        for incoming in incoming_by_label.get(key, []):
            incoming_source = (
                str(incoming.get("source_id") or "*").strip().upper() or "*"
            )
            if old_source != "*" and incoming_source not in {"*", old_source}:
                continue
            new_id = str(incoming["disease_id"]).upper()
            if new_id == old_id:
                continue
            declaration = declared_transition(
                country_code=country_code, old_id=old_id, new_id=new_id
            )
            change_key = (int(existing["id"]), new_id, incoming_source)
            changes[change_key] = {
                "existing_mapping_id": int(existing["id"]),
                "country_code": country_code,
                "existing_source_id": old_source,
                "source_id": incoming_source,
                "local_name": str(incoming["local_name"]),
                "old_disease_id": old_id,
                "new_disease_id": new_id,
                "declared": declaration is not None,
                "migration_action": declaration.action if declaration else None,
            }
    return [changes[key] for key in sorted(changes)]



def mapping_deactivation_plan(
    existing_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]],
    *, declared_transition: Callable[..., MappingTransition | None],
) -> list[dict[str, Any]]:
    """Explain every active mapping that authoritative reconciliation will stop.

    A registry-owned row disappears when it is removed from CSV.  A manual or
    bootstrap row is retained unless its normalized label conflicts with the
    current authoritative CSV target.
    """

    incoming: dict[tuple[str, str, str], str] = {}
    for row in incoming_rows:
        key = (
            str(row["country_code"]).upper(),
            str(row.get("source_id") or "*").strip().upper(),
            normalize_mapping_key(row["local_name"]),
        )
        previous = incoming.setdefault(key, str(row["disease_id"]))
        if previous != str(row["disease_id"]):
            raise RuntimeError(
                f"Authoritative mapping conflict for {key}: "
                f"{previous} vs {row['disease_id']}"
            )

    incoming_by_label: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in incoming_rows:
        label_key = (
            str(row["country_code"]).upper(),
            normalize_mapping_key(row["local_name"]),
        )
        incoming_by_label.setdefault(label_key, []).append(row)

    plan: list[dict[str, Any]] = []
    for row in existing_rows:
        country_code = str(row["country_code"]).upper()
        source_id = str(row.get("source_id") or "*").strip().upper()
        normalized_label = normalize_mapping_key(row["local_name"])
        key = (
            country_code,
            source_id,
            normalized_label,
        )
        expected = incoming.get(key)
        label_candidates = incoming_by_label.get((country_code, normalized_label), [])
        metadata = _json_object(row.get("metadata"))
        origin = str(metadata.get("origin") or "")
        registry_owned = origin.startswith("configs/mapping/")
        conflicts = expected is not None and expected != str(row["disease_id"])
        removed_from_registry = registry_owned and expected is None
        wildcard_shadow_conflict = (
            source_id == "*"
            and expected is None
            and any(
                str(candidate.get("source_id") or "*").strip().upper() != "*"
                and str(candidate["disease_id"]) != str(row["disease_id"])
                and declared_transition(
                    country_code=country_code,
                    old_id=str(row["disease_id"]),
                    new_id=str(candidate["disease_id"]),
                )
                is not None
                for candidate in label_candidates
            )
        )
        if not (conflicts or removed_from_registry or wildcard_shadow_conflict):
            continue

        replacements = sorted(
            {
                (
                    str(candidate.get("source_id") or "*").strip().upper(),
                    str(candidate["disease_id"]),
                    str(candidate["local_name"]),
                )
                for candidate in label_candidates
            }
        )
        if conflicts:
            reason = "target_conflict"
        elif wildcard_shadow_conflict:
            reason = "wildcard_shadow_conflict"
        elif not replacements:
            reason = "removed_from_registry"
        else:
            source_changed = any(item[0] != source_id for item in replacements)
            target_changed = any(
                item[1] != str(row["disease_id"]) for item in replacements
            )
            if source_changed and target_changed:
                reason = "target_and_source_rekey"
            elif source_changed:
                reason = "source_rekey"
            elif target_changed:
                reason = "target_rekey"
            else:
                reason = "registry_identity_rekey"
        plan.append(
            {
                "mapping_id": int(row["id"]),
                "country_code": country_code,
                "source_id": source_id,
                "local_name": str(row["local_name"]),
                "normalized_local_name": normalized_label,
                "old_disease_id": str(row["disease_id"]),
                "origin": origin or None,
                "reason": reason,
                "replacement_candidates": [
                    {
                        "source_id": replacement_source,
                        "disease_id": replacement_disease,
                        "local_name": replacement_name,
                    }
                    for replacement_source, replacement_disease, replacement_name in replacements
                ],
            }
        )
    return sorted(plan, key=lambda item: item["mapping_id"])



def build_sync_plan(*, document: dict, payload_summary: dict, standard_rows: list,
                    mapping_rows: list, mapping_transitions: tuple, fact_remaps: tuple,
                    series_geography_remaps: tuple, br_repair_reason: str) -> dict[str, Any]:
    return {
        "mode": "dry_run",
        "registry_id": document["registry_id"],
        "schema_version": document["schema_version"],
        "release_version": document.get("release_version"),
        "catalogue_rows": len(standard_rows),
        "managed_mapping_rows": len(mapping_rows),
        "managed_mapping_scopes": sorted({row["country_code"] for row in mapping_rows}),
        "ontology_rows": payload_summary,
        "mapping_transitions": [asdict(item) for item in mapping_transitions],
        "fact_remaps": [asdict(item) for item in fact_remaps],
        "series_geography_remaps": [asdict(item) for item in series_geography_remaps],
        "semantic_repairs": [
            {
                "country_code": "BR",
                "old_disease_id": "D193",
                "source_code": "NTRA",
                "action": "remove_invalid_legacy_projection",
                "reason": br_repair_reason,
            }
        ],
    }
