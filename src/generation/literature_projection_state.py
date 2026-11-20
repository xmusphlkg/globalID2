"""Private, publication-bound input state for article-scoped literature reads.

This is a derived read model, never a replacement for the editorial database.
Only a successfully published snapshot may become an incremental baseline.
The public JSON format and the publisher's pointer transaction are unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


SCHEMA = 1
STATE = "projection-state.json"
RECEIPT = "projection-state-receipt.json"
ROOT = Path(__file__).resolve().parents[2]


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def projection_dependencies(diseases: dict, *, root: Path = ROOT) -> str:
    """Fingerprint inputs baked into raw article/title projections.

    Surveillance coverage, Situation, weekly reviews, historical seeds and the
    clock are applied after the merge on every export. They deliberately do
    not invalidate the article SQL baseline. Disease labels/aliases and source
    code do: reusing stale classification policy would change public semantics.
    """
    names = {"src/generation/site_data_literature.py", "src/literature/content_policy.py",
             "src/generation/literature_projection_state.py",
             "configs/literature/disease_aliases.json", "configs/disease_ontology.json"}
    names.update(str(path.relative_to(root)) for path in (root / "src/generation").glob("literature*.py"))
    names.update(str(path.relative_to(root)) for path in (root / "src/generation/literature_export").glob("*.py"))
    inputs = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              if (root / name).is_file() else None for name in sorted(names)}
    return hashlib.sha256(canonical_bytes({"schema": SCHEMA, "diseases": diseases, "inputs": inputs})).hexdigest()


def write_projection_state(snapshot: Path, projection: dict, *, dependencies: str) -> dict:
    """Write only after public/build artifacts and article digests succeeded."""
    from src.generation.literature_publication import _atomic_write
    document = {"schema": SCHEMA, "dependencies": dependencies, "projection": projection}
    # Preserve record key order to retain the existing writer's byte format.
    body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    _atomic_write(Path(snapshot) / STATE, body)
    return {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}


def bind_projection_state(snapshot: Path, manifest: dict) -> dict | None:
    """Bind a completed private state to its acknowledged public manifest.

    The caller owns public verification. Older candidates without this state
    remain publishable and simply cannot serve as incremental baselines.
    """
    from src.generation.literature_publication import _atomic_write
    from src.generation.published_research_reuse import require_public_research_matches
    snapshot = Path(snapshot)
    path = snapshot / STATE
    if not path.exists():
        return None
    if snapshot.is_symlink() or path.is_symlink():
        raise ValueError("Unsafe literature projection state")
    body = path.read_bytes()
    state = json.loads(body)
    _validate_state(state)
    require_public_research_matches(snapshot / "public/research", manifest)
    receipt = {"schema": SCHEMA, "manifest_sha256": hashlib.sha256(canonical_bytes(manifest)).hexdigest(),
               "state_sha256": hashlib.sha256(body).hexdigest(), "state_bytes": len(body),
               "dependencies": state["dependencies"], "release_id": manifest["release_id"]}
    _atomic_write(snapshot / RECEIPT, canonical_bytes(receipt))
    return receipt


def _validate_state(state: dict) -> None:
    if not isinstance(state, dict) or state.get("schema") != SCHEMA or not isinstance(state.get("dependencies"), str):
        raise ValueError("Unsupported literature projection state")
    projection = state.get("projection")
    if not isinstance(projection, dict):
        raise ValueError("Incomplete literature projection state")
    at = datetime.fromisoformat(projection["evaluated_at"])
    if at.tzinfo is None or at > datetime.now(timezone.utc):
        raise ValueError("Invalid literature projection clock")
    for name in ("articles", "title_estimated_related_records", "signal_decisions"):
        rows = projection.get(name)
        if not isinstance(rows, list) or any(not isinstance(row, dict)
                or not isinstance(row.get("article_id"), str) or not row["article_id"] for row in rows):
            raise ValueError("Incomplete literature projection records")
        if name != "signal_decisions" and len({row["article_id"] for row in rows}) != len(rows):
            raise ValueError("Duplicate literature projection records")


def load_projection_state(snapshot: Path | None, *, expected_manifest_sha256: str | None,
                          dependencies: str) -> tuple[dict | None, str]:
    """Return a verified baseline or an explicit conservative fallback reason."""
    if snapshot is None or not expected_manifest_sha256:
        return None, "baseline_unavailable"
    snapshot = Path(snapshot)
    try:
        if snapshot.is_symlink() or any((snapshot / name).is_symlink() for name in (STATE, RECEIPT)):
            return None, "baseline_unsafe"
        receipt = json.loads((snapshot / RECEIPT).read_bytes())
        if receipt.get("schema") != SCHEMA or receipt.get("manifest_sha256") != expected_manifest_sha256:
            return None, "publication_baseline_changed"
        if receipt.get("dependencies") != dependencies:
            return None, "projection_dependencies_changed"
        body = (snapshot / STATE).read_bytes()
        if len(body) != receipt.get("state_bytes") or hashlib.sha256(body).hexdigest() != receipt.get("state_sha256"):
            return None, "projection_state_changed"
        state = json.loads(body)
        _validate_state(state)
        if state["dependencies"] != dependencies:
            return None, "projection_dependencies_changed"
        return state["projection"], "incremental"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, "projection_state_unavailable"


def reuse_build_artifacts(previous: Path, snapshot: Path) -> int:
    """Share immutable build inputs; atomic writers replace changed files.

    Browser shards are excluded because their retention code updates mtimes.
    Every expected build file is still regenerated/compared by the writer, and
    stale article/collection files are reconciled by its existing cleanup.
    """
    source, target = Path(previous) / "build/research", Path(snapshot) / "build/research"
    if not source.is_dir() or source.is_symlink():
        return 0
    paths = list(source.rglob("*.json"))
    if any(path.is_symlink() or any(parent.is_symlink() for parent in path.parents
                                   if parent.is_relative_to(source)) for path in paths):
        raise ValueError("Unsafe literature build baseline")
    for path in paths:
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, destination)
        except OSError:
            shutil.copy2(path, destination)
    return len(paths)
