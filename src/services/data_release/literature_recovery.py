"""Persist the exact Papers catalogue and template bytes before any upload."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from src.generation.literature_publication import _atomic_write
from .literature_binding import bind_literature_candidate
from .papers_content import PapersContentCandidate, _source_objects, catalogue_from_entries
from .papers_immutable import _source


def freeze_papers(run: Path, papers: PapersContentCandidate, build: dict | None) -> None:
    # Bind once, before the external commit. A retry does not rerun the HTML
    # parser/template splitter or select a newer projection implementation.
    bound = bind_literature_candidate(json.loads((run / "current.json").read_bytes()), papers,
                                     expected_papers_origin=papers.origin)
    body = json.dumps(bound, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    _atomic_write(run / "current.json", body)
    value = {"schema": 1, "root": str(papers.root.resolve()), "origin": papers.origin,
        "release_id": papers.release_id, "generation": papers.generation,
        "entries": papers.entries, "shards": [(key, base64.b64encode(data).decode()) for key, data in papers.shards],
        "manifest": base64.b64encode(papers.manifest).decode(),
        "template_root": str(papers.template_root.resolve()) if papers.template_root else None,
        "template_objects": papers.template_objects, "verification_keys": papers.verification_keys,
        "current_sha256": hashlib.sha256(body).hexdigest(), "build": build}
    _atomic_write(run / "papers-prepared.json", json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def load_papers(run: Path, *, stage: Path, origin: str, release_id: str,
                template_dir: Path | None = None) -> tuple[PapersContentCandidate, dict | None]:
    try:
        path = run / "papers-prepared.json"
        if path.is_symlink():
            raise ValueError()
        value = json.loads(path.read_bytes())
        root = Path(value["root"])
        template = Path(value["template_root"]) if value["template_root"] else None
        if (value["schema"] != 1 or value["origin"] != origin or value["release_id"] != release_id
                or root != stage.resolve() or (template and template != (template_dir or run / "paper-templates").resolve())
                or hashlib.sha256((run / "current.json").read_bytes()).hexdigest() != value["current_sha256"]):
            raise ValueError()
        papers = PapersContentCandidate(root=root, origin=origin, release_id=release_id,
            generation=value["generation"], entries=tuple(value["entries"]),
            shards=tuple((key, base64.b64decode(body, validate=True)) for key, body in value["shards"]),
            manifest=base64.b64decode(value["manifest"], validate=True), template_root=template,
            template_objects=tuple(value["template_objects"]), verification_keys=tuple(value["verification_keys"]))
        basis = json.loads(papers.manifest)
        generation = basis.pop("generation_sha256")
        if (generation != papers.generation or hashlib.sha256(json.dumps(basis,
                ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != generation):
            raise ValueError()
        rebuilt = catalogue_from_entries(root=root, origin=origin, release_id=release_id,
            entries=list(papers.entries), template_root=template, template_objects=papers.template_objects)
        if (rebuilt.manifest != papers.manifest or rebuilt.shards != papers.shards
                or rebuilt.generation != papers.generation):
            raise ValueError()
        current = json.loads((run / "current.json").read_bytes())
        if bind_literature_candidate(current, papers, expected_papers_origin=origin) != current:
            raise ValueError()
        dependencies = {entry["key"]: entry for entry in papers.template_objects}
        if len(dependencies) != len(papers.template_objects):
            raise ValueError()
        required = set()
        for entry in papers.entries:
            descriptor = entry.get("paper_template")
            if descriptor is None:
                continue
            # The separate projected-v3 workflow owns its own frozen loader.
            # This helper restores legacy HTML-derived v2 candidates only.
            if descriptor.get("schema") not in (None, 2):
                raise ValueError()
            for family, field in (("records", "record_sha256"), ("templates", "template_sha256")):
                sha = descriptor[field]
                relative = f"{family}/{sha}.json"
                key = f"paper-template-v2/{relative}"
                dependency = dependencies.get(key)
                if (dependency is None or dependency["sha256"] != sha or dependency["path"] != relative
                        or (family == "records" and entry["key"] != key)):
                    raise ValueError()
                required.add(key)
        if set(dependencies) != required:
            raise ValueError()
        for entry, source in _source_objects(papers):
            payload = _source(source, entry["path"]).read_bytes()
            if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError()
        return papers, value["build"]
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError("Frozen literature candidate unavailable; regeneration is forbidden") from error
