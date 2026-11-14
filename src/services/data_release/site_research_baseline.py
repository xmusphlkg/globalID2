"""Compose a static site with the exact already-published Papers v3 catalogue."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import re

from .papers_content import _hash, catalogue_from_entries
from .papers_immutable import _source
from .papers_projection import assert_v3_candidate, load_projection_candidate, save_projection_candidate

HASH = re.compile(r"[a-f0-9]{64}\Z")


def _read_remote(client, key, digest, size=None):
    body = client.get_object(key)
    if (body is None or _hash(body) != digest or (size is not None and len(body) != size)):
        raise ValueError("Captured Papers object is missing or has changed")
    return body


def _local_candidate(run, previous, origin):
    # Ordinary Literature and earlier site publications retain their immutable
    # candidates locally. Verify the captured generation before using this
    # optimization; otherwise the remote catalogue remains the authority.
    root = next((parent.parent for parent in run.parents if parent.name == "exports"), None)
    if root is None:
        return None
    release = previous["release_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.:-]{0,159}", release):
        raise ValueError("Invalid captured Research release identity")
    paths = []
    if release.startswith("literature-"):
        paths.append(root / "exports/research-runs" / release.removeprefix("literature-") / "projection-v3/candidate.json")
    else:
        paths.extend((root / "exports/site-research-runs").glob(f"*/{release}/projection-v3/candidate.json"))
    for path in paths:
        if not path.exists():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Unsafe captured Papers candidate")
        candidate = load_projection_candidate(path)
        if (candidate.release_id == release and candidate.origin == origin
                and candidate.generation == previous["papers"]["papers_generation_sha256"]
                and _hash(candidate.manifest) == previous["papers"]["papers_manifest_sha256"]):
            assert_v3_candidate(candidate)
            return candidate
    return None


def compose_site_candidate(*, run: Path, static, previous: dict | None, client,
                           release_id: str, directory: Path, workers: int):
    """Keep published article bytes/templates/routes; replace built static paths.

    No upload occurs here. Every reused source is checked against the captured
    catalogue and copied into this run, so retries need neither the old local
    candidate nor a newer remote pointer. The normal publisher still uses CAS.
    """
    binding = previous.get("papers") if isinstance(previous, dict) else None
    origin = static.origin
    if (not isinstance(binding, dict) or binding.get("papers_origin") != origin
            or not isinstance(binding.get("papers_generation_sha256"), str)
            or not HASH.fullmatch(binding["papers_generation_sha256"])
            or not isinstance(binding.get("papers_manifest_sha256"), str)
            or not HASH.fullmatch(binding["papers_manifest_sha256"])):
        raise ValueError("Static research publication requires a captured Papers v3 baseline")
    manifest_key = f'releases/sha256/catalogues/{binding["papers_generation_sha256"]}.json'
    if binding.get("papers_manifest_url") != origin + "/" + manifest_key:
        raise ValueError("Captured Papers catalogue URL differs from its generation")
    base = _local_candidate(run, previous, origin)
    remote_payloads = {}
    if base is None:
        body = _read_remote(client, manifest_key, binding["papers_manifest_sha256"])
        manifest = json.loads(body)
        if (manifest.get("schema_version") != 2 or manifest.get("release_id") != previous["release_id"]
                or manifest.get("papers_origin") != origin
                or manifest.get("generation_sha256") != binding["papers_generation_sha256"]
                or not isinstance(manifest.get("shards"), dict)):
            raise ValueError("Invalid captured Papers catalogue")
        def shard_items(descriptor):
            digest = descriptor["sha256"]
            if (not isinstance(digest, str) or not HASH.fullmatch(digest)
                    or descriptor["key"] != f"releases/sha256/catalogue-shards/{digest}.json"):
                raise ValueError("Invalid captured Papers catalogue shard")
            shard = json.loads(_read_remote(client, descriptor["key"], digest, descriptor["bytes"]))
            if shard.get("schema_version") != 1 or len(shard["items"]) != descriptor["count"]:
                raise ValueError("Captured Papers catalogue shard differs")
            return shard["items"]
        with ThreadPoolExecutor(max_workers=min(workers, 16)) as pool:
            entries = [entry for group in pool.map(shard_items, manifest["shards"].values()) for entry in group]
        base = catalogue_from_entries(root=run / "remote-baseline", origin=origin,
            release_id=previous["release_id"], entries=entries)
        if base.manifest != body or len(entries) != binding.get("papers_file_count"):
            raise ValueError("Captured Papers catalogue content differs from its binding")
        assert_v3_candidate(base)
        dependencies = {}
        for entry in entries:
            descriptor = entry.get("paper_template")
            if descriptor is None:
                continue
            for group, field in (("records", "record_sha256"), ("templates", "template_sha256"), ("assets", "assets_sha256")):
                digest = descriptor[field]
                if not isinstance(digest, str) or not HASH.fullmatch(digest):
                    raise ValueError("Invalid captured Papers v3 dependency")
                key = f"paper-template-v3/{group}/{digest}.json"
                dependencies[key] = digest
        def dependency(item):
            key, digest = item
            payload = _read_remote(client, key, digest)
            remote_payloads[key] = payload
            return {"path": key, "key": key, "sha256": digest, "bytes": len(payload),
                    "content_type": "application/json; charset=utf-8"}
        with ThreadPoolExecutor(max_workers=min(workers, 16)) as pool:
            objects = tuple(pool.map(dependency, dependencies.items()))
        base = replace(base, template_root=directory, template_objects=objects)
    assert_v3_candidate(base)
    dependencies = {item['key']: item for item in base.template_objects}
    for entry in base.entries:
        descriptor = entry.get('paper_template')
        if descriptor is None:
            continue
        for group, field in (("records", "record_sha256"), ("templates", "template_sha256"), ("assets", "assets_sha256")):
            digest = descriptor[field]
            key = f"paper-template-v3/{group}/{digest}.json"
            if key not in dependencies or dependencies[key]['sha256'] != digest:
                raise ValueError('Captured Papers v3 candidate has missing dependencies')
    # The new static build is authoritative for non-article page routes.
    # Preserve projected articles and their supporting resources, but do not
    # resurrect removed directory/collection pages from the captured catalogue.
    merged = {entry["logical_key"]: entry for entry in base.entries
              if "paper_template" in entry or not (
                  entry["logical_key"].endswith("/")
                  or entry["path"].lower().endswith((".html", ".htm")))}
    for entry in static.entries:
        existing = merged.get(entry["logical_key"])
        if existing and "paper_template" in existing:
            raise ValueError("A static build cannot replace a projected article")
        if (existing and entry["path"].startswith("_astro/")
                and existing["sha256"] != entry["sha256"]):
            raise ValueError("Captured Papers immutable asset collision")
        merged[entry["logical_key"]] = entry
    # A valid source catalogue must include all three routes for each paper;
    # an incomplete historical/corrupt candidate cannot silently drop a locale.
    locales = {}
    for entry in base.entries:
        descriptor = entry.get("paper_template")
        if descriptor:
            locales.setdefault(descriptor["slug"], set()).add(descriptor["locale"])
    if not locales or any(value != {"en", "zh", "fr"} for value in locales.values()):
        raise ValueError("Captured Papers v3 baseline has incomplete article locales")
    directory.mkdir(parents=True, exist_ok=True)
    copies = [(entry, base.root, static.root) for entry in base.entries
              if "paper_template" not in entry and merged.get(entry["logical_key"]) is entry]
    copies.extend((entry, base.template_root, directory) for entry in base.template_objects)
    def freeze(item):
        entry, source_root, target_root = item
        relative = entry["path"]
        if (not isinstance(relative, str) or relative.startswith("/") or "\\" in relative
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise ValueError("Invalid captured Papers object path")
        if entry["key"] in remote_payloads:
            payload = remote_payloads[entry["key"]]
        elif source_root == run / "remote-baseline":
            payload = _read_remote(client, entry["key"], entry["sha256"], entry["bytes"])
        else:
            payload = _source(source_root, relative).read_bytes()
        if _hash(payload) != entry["sha256"] or len(payload) != entry["bytes"]:
            raise ValueError("Captured Papers dependency changed before freezing")
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or (target.exists() and target.read_bytes() != payload):
            raise ValueError("Captured Papers object collides with the static candidate")
        if not target.exists():
            target.write_bytes(payload)
    with ThreadPoolExecutor(max_workers=min(workers, 16)) as pool:
        list(pool.map(freeze, copies))
    candidate = catalogue_from_entries(root=static.root, origin=origin, release_id=release_id,
        entries=list(merged.values()), template_root=directory, template_objects=base.template_objects)
    assert_v3_candidate(candidate)
    save_projection_candidate(candidate, directory / "candidate.json", base_generation=base.generation)
    return candidate
