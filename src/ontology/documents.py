"""Read and maintain an ontology split into reviewed source documents.

The root owns concepts and shared metadata. ``source_documents`` lists relative
JSON files owning sources, source series and availability declarations. Readers
receive the same ordinary document as a single-file registry; writers retain
the source ownership and any review metadata in each included document.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


SOURCE_SECTIONS = ("sources", "source_series", "availability")


class OntologyValidationError(ValueError):
    """Raised when an ontology document is structurally or semantically invalid."""


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OntologyValidationError(
            f"invalid ontology JSON at {path}: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(value, dict):
        raise OntologyValidationError(f"ontology document at {path} must be an object")
    return value


def _source_paths(path: Path, document: Mapping[str, Any]) -> tuple[Path, ...]:
    names = document.get("source_documents", [])
    if not isinstance(names, list):
        raise OntologyValidationError("source_documents must be a list of relative JSON paths")
    paths = []
    for name in names:
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise OntologyValidationError("source_documents must contain relative JSON paths")
        target = (path.parent / name).resolve()
        if not target.is_relative_to(path.parent.resolve()) or target.suffix != ".json":
            raise OntologyValidationError(f"source document must be a JSON file within {path.parent}: {name}")
        if target == path.resolve() or target in paths:
            raise OntologyValidationError(f"duplicate or recursive source document: {name}")
        paths.append(target)
    return tuple(paths)


def ontology_document_paths(path: str | Path) -> tuple[Path, ...]:
    """Return every physical input, in loading order, for dependency hashes."""
    root = Path(path)
    return (root, *_source_paths(root, _read(root)))


def read_ontology_document(path: str | Path) -> dict[str, Any]:
    """Load complete explicit contracts, without expanding source templates."""
    root = Path(path)
    document = _read(root)
    paths = _source_paths(root, document)
    document.pop("source_documents", None)
    seen = {}
    for key in SOURCE_SECTIONS:
        rows = document.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get("id") for row in rows):
            raise OntologyValidationError(f"{root}: {key} must contain identified objects")
        seen[key] = {row["id"] for row in rows}
    for source_path in paths:
        source = _read(source_path)
        if "source_documents" in source:
            raise OntologyValidationError(f"nested source documents are not supported: {source_path}")
        source_ids = {row.get("id") for row in source.get("sources", []) if isinstance(row, dict)}
        for key in SOURCE_SECTIONS:
            rows = source.get(key)
            if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get("id") for row in rows):
                raise OntologyValidationError(f"{source_path}: {key} must contain identified objects")
            for row in rows:
                if key != "sources" and row.get("source_id") not in source_ids:
                    raise OntologyValidationError(f"{source_path}: {row['id']} references a source owned elsewhere")
                if row["id"] in seen[key]:
                    raise OntologyValidationError(f"duplicate {key} id across ontology documents: {row['id']}")
                seen[key].add(row["id"])
            document.setdefault(key, []).extend(rows)
    return document


def write_ontology_document(path: str | Path, document: Mapping[str, Any]) -> None:
    """Save explicit contracts while preserving each included source owner."""
    root = Path(path)
    physical_root = _read(root)
    source_paths = _source_paths(root, physical_root)
    sources = [(source_path, _read(source_path)) for source_path in source_paths]
    owners = {}
    for source_path, source in sources:
        for row in source["sources"]:
            if row["id"] in owners:
                raise OntologyValidationError(f"duplicate source owner: {row['id']}")
            owners[row["id"]] = source_path
    output = dict(document)
    output.pop("source_documents", None)
    for key in SOURCE_SECTIONS:
        rows = list(document.get(key, []))
        output[key] = [row for row in rows if row.get("source_id", row.get("id")) not in owners]
        for source_path, source in sources:
            source[key] = [row for row in rows if owners.get(row.get("source_id", row.get("id"))) == source_path]
    if source_paths:
        output["source_documents"] = physical_root["source_documents"]
    for target, value in [*sources, (root, output)]:
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
