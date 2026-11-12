"""Freeze download files from the very same context as surveillance JSON."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
from pathlib import Path

from .direct_download_files import build_direct_download_files
from .site_data_writer import write_compact_json


def downloads_initialized(directory: Path) -> bool:
    try:
        return bool(json.loads((directory / "manifest.json").read_text()).get("surveillance_release_id"))
    except (OSError, ValueError):
        return False


def generate_download_candidate(context: dict, output: Path, candidate: Path, *,
                                release_id: str, download_url_base: str) -> dict:
    if output is None:
        raise ValueError("Surveillance downloads require an output directory")
    output, candidate = output.resolve(), candidate.resolve()
    if output.is_relative_to(candidate) or candidate.is_relative_to(output):
        raise ValueError("Download candidate and output directories must be separate")
    if not download_url_base.startswith("https://raw.githubusercontent.com/"):
        raise ValueError("Surveillance downloads require a GitHub Raw URL")
    # Use the publisher's lock and atomic asset replacement. Hard links keep a
    # retry's private candidate immutable without copying gigabytes of history.
    output.parent.mkdir(parents=True, exist_ok=True)
    with (output.parent / f".{output.name}.publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = build_direct_download_files(context, output,
            download_url_base=download_url_base,
            changed_country_codes=set(context["incremental_country_codes"]) if context.get("incremental") else None,
            changed_disease_ids=set(context["changed_disease_ids"]) if context.get("incremental") else None)
        manifest.pop("generation", None)
        manifest["surveillance_release_id"] = release_id
        write_compact_json(output / "manifest.json", manifest)
        if candidate.exists():
            shutil.rmtree(candidate)
        candidate.mkdir(parents=True)
        for name in ("countries", "diseases"):
            shutil.copytree(output / name, candidate / name, copy_function=os.link)
        shutil.copy2(output / "manifest.json", candidate / "manifest.json")
        return manifest
