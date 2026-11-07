"""Complete, coherent Situation JSON generations for the independent channel."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from src.generation.surveillance_lock import LOCKED_ENV, surveillance_output_lock
from src.core.country_library import get_country_display_name, get_standard_country_codes
from src.services.global_sources.catalogue import country_catalogue, country_row, disease_catalogue
from .export import export_site


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


async def _export(state_dir: Path, public_dir: Path, *, release_id: str) -> dict:
    summary = await export_site(output_dir=public_dir, derive_outbreaks=True)
    read = lambda name: json.loads((public_dir / name).read_text())
    mapping, monitoring, sources = read("map.json"), read("monitoring.json"), read("sources.json")
    # Runtime panels distinguish a valid empty entity from a missing/corrupt
    # object. Cover the complete registered identity catalogue in each release.
    for code in sorted(set(country_catalogue()) | set(get_standard_country_codes())):
        path = public_dir / "by-country" / f"{code}.json"
        if not path.exists():
            country = country_row(code)
            if "-" in code:
                country = {"code": code, "name_en": get_country_display_name(code, "en"),
                    "name_zh": get_country_display_name(code, "zh")}
            _write(path, {"schema_version": mapping["schema_version"], "as_of": mapping["as_of"],
                "country": country, "alert_count": 0, "alerts": []})
    for row in disease_catalogue():
        path = public_dir / "by-disease" / f"{row['disease_id']}.json"
        if not path.exists():
            _write(path, {"schema_version": mapping["schema_version"], "as_of": mapping["as_of"],
                "disease": {key: row.get(key) for key in ("disease_id", "name_en", "name_zh", "slug")},
                "alert_count": 0, "alerts": []})
    _write(public_dir / "map-data.json", {
        "schema_version": mapping["schema_version"], "generated_at": mapping["generated_at"],
        "as_of": mapping["as_of"], "points": mapping["points"],
        "monitoring": monitoring["products"],
        "active_outbreaks": sum(row["status"] == "active" for row in mapping["outbreaks"]),
        "sources_ok": sum(row["status"] == "ok" for row in sources["sources"]),
        "source_count": len(sources["sources"]),
    })
    logical = ["map.json", "map-data.json", "monitoring.json", "sources.json"]
    logical.extend(str(path.relative_to(public_dir)) for folder in
        ("outbreaks", "by-country", "by-disease", "weekly") for path in (public_dir / folder).glob("*.json"))
    files = {}
    for name in sorted(logical):
        body = (public_dir / name).read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        relative = f"objects/{digest}.json"
        path = public_dir / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(body)
            temporary.replace(path)
        elif path.is_symlink() or path.read_bytes() != body:
            raise ValueError("Damaged immutable Situation object")
        files[name] = {"url": relative, "sha256": digest, "bytes": len(body)}
    manifest = {"schema_version": 1, "release_id": release_id,
        "generated_at": mapping["generated_at"], "files": files}
    _write(public_dir / "current.json", manifest)
    return {**summary, "release_id": release_id, "file_count": len(files)}


async def export_json_publication(state_dir: Path, public_dir: Path, *, release_id: str) -> dict:
    """Caller may inherit a parent-held lock; helper itself never bypasses it."""
    if not release_id.strip():
        raise ValueError("Situation publication requires release_id")
    state_dir, public_dir = Path(state_dir), Path(public_dir)
    if os.environ.get(LOCKED_ENV) == "1":
        return await _export(state_dir, public_dir, release_id=release_id)
    async with surveillance_output_lock(state_dir, public_dir):
        return await _export(state_dir, public_dir, release_id=release_id)
