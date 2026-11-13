"""Bounded, read-only checks of the single research pointer and its catalogue.

This verifies a JSON sample and one catalogue shard, not every article or its
rendered HTML. The catalogue's digest and generation bind both v2 and v3 papers.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urljoin, urlsplit

import httpx

from .papers_content import _json
from .realtime_monitor import _ProbeFailure, _read, _identifier, iso, validate_public_url
from .surveillance_publish import validate_manifest

HEX = re.compile(r"[a-f0-9]{64}\Z")


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def reserve_sampling_cursor(path: Path, *, target: str) -> int:
    """Reserve a local index before probing, so failures cannot stall rotation.

    Only sampling position is stored, never credentials or public response bodies.
    Overlapping timer/operator processes share the same atomic counter.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    target_sha = _sha(target.encode())
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cursor = 0
        if path.exists():
            if path.stat().st_size > 65536:
                raise ValueError("invalid_sampling_state")
            state = json.loads(path.read_bytes())
            if (not isinstance(state, dict) or state.get("schema_version") != 1
                    or type(state.get("next_cursor")) is not int or state["next_cursor"] < 0
                    or not isinstance(state.get("target_sha256"), str)
                    or not HEX.fullmatch(state["target_sha256"])):
                raise ValueError("invalid_sampling_state")
            if state["target_sha256"] == target_sha:
                cursor = state["next_cursor"]
        descriptor, temporary = tempfile.mkstemp(prefix=".sampling-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"schema_version": 1, "target_sha256": target_sha,
                           "next_cursor": cursor + 1}, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return cursor


async def probe_public_research(client, url: str, *, papers_origin: str,
                                timeout_seconds: float = 10,
                                object_limit_bytes: int = 2 * 1024 * 1024,
                                sample_cursor: int | None = None) -> dict:
    started = time.monotonic()
    cursor = int(time.time() // 60) if sample_cursor is None else sample_cursor
    if type(cursor) is not int or cursor < 0:
        raise ValueError("invalid_sample_cursor")
    sampling = {"sample_cursor": cursor, "sampling_strategy": "round_robin"}

    async def probe():
        validate_public_url(url)
        origin = papers_origin.rstrip("/")
        parsed = urlsplit(validate_public_url(origin))
        if parsed.scheme != "https" or parsed.path:
            raise ValueError("invalid_research_origin")
        body, headers = await _read(client, url, limit=2 * 1024 * 1024)
        # This is the same envelope validator used by bind_literature_candidate;
        # research additionally requires the Papers binding below.
        pointer, objects = validate_manifest(body)
        release = _identifier(pointer.get("release_id"))
        generated = iso(pointer.get("generated_at"))
        binding = pointer.get("papers")
        if not release or not generated or not isinstance(binding, dict):
            raise ValueError("research_binding_required")
        generation, manifest_sha = binding.get("papers_generation_sha256"), binding.get("papers_manifest_sha256")
        count = binding.get("papers_file_count")
        if (not isinstance(generation, str) or not HEX.fullmatch(generation)
                or not isinstance(manifest_sha, str) or not HEX.fullmatch(manifest_sha)
                or binding.get("papers_origin") != origin or type(count) is not int or count < 1):
            raise ValueError("invalid_research_binding")
        manifest_url = f"{origin}/releases/sha256/catalogues/{generation}.json"
        if binding.get("papers_manifest_url") != manifest_url:
            raise ValueError("invalid_research_catalogue_url")
        raw, _ = await _read(client, manifest_url, limit=2 * 1024 * 1024)
        if _sha(raw) != manifest_sha:
            raise _ProbeFailure("object_checksum_mismatch")
        catalogue = json.loads(raw)
        if (not isinstance(catalogue, dict) or type(catalogue.get("schema_version")) is not int
                or catalogue["schema_version"] != 2 or catalogue.get("generation_sha256") != generation
                or catalogue.get("release_id") != release or catalogue.get("papers_origin") != origin
                or type(catalogue.get("file_count")) is not int or catalogue["file_count"] != count
                or not isinstance(catalogue.get("shards"), dict) or not catalogue["shards"]):
            raise ValueError("invalid_research_catalogue")
        basis = {key: value for key, value in catalogue.items() if key != "generation_sha256"}
        if _sha(_json(basis)) != generation:
            raise _ProbeFailure("object_checksum_mismatch")
        shards = []
        for group, entry in catalogue["shards"].items():
            if (not re.fullmatch(r"[a-f0-9]{2}", group) or not isinstance(entry, dict)
                    or not isinstance(entry.get("sha256"), str) or not HEX.fullmatch(entry["sha256"])
                    or entry.get("key") != f"releases/sha256/catalogue-shards/{entry['sha256']}.json"
                    or type(entry.get("bytes")) is not int or entry["bytes"] < 1
                    or type(entry.get("count")) is not int or entry["count"] < 1):
                raise ValueError("invalid_research_shard")
            shards.append((group, entry["bytes"], entry))
        if sum(item[2]["count"] for item in shards) != count:
            raise ValueError("invalid_research_file_count")
        group, size, entry = sorted(shards)[cursor % len(shards)]
        samples = sorted(objects.items())
        relative, sample = samples[cursor % len(samples)]
        sampling.update(sample_shard=group, sample_object=relative,
                        papers_generation_sha256=generation)
        if size > object_limit_bytes:
            raise _ProbeFailure("sample_object_exceeds_limit")
        shard, _ = await _read(client, origin + "/" + entry["key"], limit=object_limit_bytes)
        if len(shard) != size or _sha(shard) != entry["sha256"]:
            raise _ProbeFailure("object_checksum_mismatch")
        shard_data = json.loads(shard)
        if (not isinstance(shard_data, dict) or type(shard_data.get("schema_version")) is not int
                or shard_data["schema_version"] != 1 or not isinstance(shard_data.get("items"), list)
                or len(shard_data["items"]) != entry["count"]):
            raise ValueError("invalid_research_shard")
        logical = [item.get("logical_key") if isinstance(item, dict) else None for item in shard_data["items"]]
        if (any(not isinstance(key, str) or not key or _sha(key.encode())[:2] != group for key in logical)
                or len(set(logical)) != len(logical)):
            raise ValueError("invalid_research_shard_membership")
        size = sample["bytes"]
        if size > object_limit_bytes:
            raise _ProbeFailure("sample_object_exceeds_limit")
        payload, _ = await _read(client, urljoin(url, relative), limit=object_limit_bytes)
        if len(payload) != size or _sha(payload) != sample["sha256"]:
            raise _ProbeFailure("object_checksum_mismatch")
        if not isinstance(json.loads(payload), (dict, list)):
            raise ValueError("invalid_research_json_sample")
        again, _ = await _read(client, url, limit=2 * 1024 * 1024)
        if again != body:
            raise _ProbeFailure("research_pointer_changed")
        return {"available": True, "http_status": 200, "release_id": release,
            "manifest_sha256": _sha(body), "generated_at": generated, "sample_verified": True,
            "sample_bytes": size, "papers_generation_sha256": generation,
            "verification_scope": "research_pointer_catalogue_one_shard_one_json_sample",
            "article_html_verified": False, "verified_shards": 1, "total_shards": len(shards),
            "logical_files": len(pointer["files"]), "immutable_objects": len(objects),
            "pointer_cache_control": headers.get("cache-control", "")[:160]}

    try:
        result = await asyncio.wait_for(probe(), timeout=timeout_seconds)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        result = {"available": False, "error_code": "http_timeout"}
    except _ProbeFailure as error:
        result = {"available": False, "error_code": error.code}
        if error.http_status is not None:
            result["http_status"] = error.http_status
    except httpx.HTTPError:
        result = {"available": False, "error_code": "http_unreachable"}
    except (ValueError, TypeError, KeyError):
        result = {"available": False, "error_code": "invalid_manifest"}
    return {**result, **sampling, "attempts": 1, "elapsed_seconds": round(time.monotonic() - started, 3)}
