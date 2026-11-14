"""Generate deterministic, time-partitioned public download files."""

from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from datetime import date
from pathlib import Path

from src.generation.direct_download import (
    disease_merge,
    formats,
    partitions,
    rows,
    runtime,
    windows,
)
from src.generation.direct_download.disease_merge import (
    _merge_existing_disease_rows,
    _read_disease_baseline_json,  # noqa: F401 - compatibility re-export
)
from src.generation.direct_download.formats import (
    _XLSX_TIMESTAMP,  # noqa: F401 - compatibility re-export
    DOWNLOAD_FIELDS,
    _csv_bytes,
    _json_bytes,
    _xlsx_bytes,
    _xlsx_cell,  # noqa: F401 - compatibility re-export
)
from src.generation.direct_download.rows import (
    SOURCE_SERIES_FIELDS,  # noqa: F401 - compatibility re-export
    _projection_provenance_columns,  # noqa: F401 - compatibility re-export
    _require_lossless_download_series,  # noqa: F401 - compatibility re-export
    _sort_rows,  # noqa: F401 - compatibility re-export
    _source_columns,  # noqa: F401 - compatibility re-export
    _source_observation_rows,  # noqa: F401 - compatibility re-export
    build_country_download_rows,
    build_disease_download_rows,
)
from src.generation.direct_download.runtime import (
    _default_export_workers,
    _read_cgroup_memory_limit_bytes,
)
from src.generation.direct_download.windows import (
    BRIDGE_WINDOW_END,  # noqa: F401 - compatibility re-export
    BRIDGE_WINDOW_START,  # noqa: F401 - compatibility re-export
    ROLLING_WINDOW_START,
    ROLLING_WINDOW_YEARS,  # noqa: F401 - compatibility re-export
    SPECIAL_WINDOW_END,  # noqa: F401 - compatibility re-export
    SPECIAL_WINDOW_START,  # noqa: F401 - compatibility re-export
    PartitionSpec,
    _window_for_year,  # noqa: F401 - compatibility re-export
    partition_rows,
)
from src.generation.site_data_writer import _write_if_changed
from src.services import disease_series_policy

PUBLIC_FORMATS = ("csv", "json", "xlsx")
GITHUB_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_TARGET_FILE_BYTES = 90 * 1024 * 1024


DEFAULT_EXPORT_WORKERS = _default_export_workers(os.cpu_count(), _read_cgroup_memory_limit_bytes())


def _projection_code_sha256() -> str:
    """Invalidate dataset reuse whenever download projection rules change."""
    digest = hashlib.sha256()
    for source in (
        Path(__file__),
        Path(disease_series_policy.__file__),
        *(Path(module.__file__) for module in (rows, windows, disease_merge, runtime, formats, partitions)),
    ):
        digest.update(source.read_bytes())
    return digest.hexdigest()


def _input_sha256(value: dict, *, projection_code: str, max_file_bytes: int) -> str:
    stable = dict(value)
    stable.pop("generated_at", None)
    for key in ("country_data", "disease_data"):
        if isinstance(stable.get(key), dict):
            snapshot = dict(stable[key])
            snapshot.pop("generated_at", None)
            stable[key] = snapshot
    digest = hashlib.sha256()
    digest.update(projection_code.encode("ascii"))
    digest.update(str(max_file_bytes).encode("ascii"))
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    pending: list[str] = []
    pending_chars = 0
    for chunk in encoder.iterencode(stable):
        pending.append(chunk)
        pending_chars += len(chunk)
        if pending_chars >= 64 * 1024:
            digest.update("".join(pending).encode("utf-8"))
            pending.clear()
            pending_chars = 0
    if pending:
        digest.update("".join(pending).encode("utf-8"))
    return digest.hexdigest()


def _entry_files_available(
    entry: dict,
    output_dir: Path,
    directory: str,
    path_id: str,
    *,
    verify_hash: bool = True,
    trusted_paths: set[str] | None = None,
) -> bool:
    """Require every reused part to exist at its declared path and size.

    Hash verification reads the file bytes. Callers that already know the
    published curve is unchanged pass verify_hash=False and trust the size.
    """
    parts = entry.get("parts") or []
    if not parts:
        return bool(entry) and entry.get("record_count") == 0 and isinstance(entry.get("parts"), list)
    prefix = f"{directory}/{path_id}/"
    for part in parts:
        files = part.get("files") or {}
        if set(files) != set(PUBLIC_FORMATS):
            return False
        for file_meta in files.values():
            relative = str(file_meta.get("relative_path") or "")
            if not relative.startswith(prefix) or ".." in Path(relative).parts:
                return False
            path = output_dir / relative
            if not path.is_file() or path.stat().st_size != file_meta.get("bytes"):
                return False
            if verify_hash and relative not in (trusted_paths or ()) and _file_sha256(path) != file_meta.get("sha256"):
                return False
    return True


def _git_unchanged_paths(output_dir: Path) -> set[str]:
    """Reuse committed bytes only while their declared checksums also match."""
    if not (output_dir / ".git").exists():
        return set()
    try:
        def git(*args):
            return subprocess.run(
                ["git", *args], cwd=output_dir, check=True,
                capture_output=True, text=True,
            ).stdout
        tracked = set(git("ls-files", "-z", "--", "countries", "diseases").split("\0"))
        changed = set(git("diff", "HEAD", "--name-only", "--no-renames", "-z", "--", "countries", "diseases").split("\0"))
        def assets(manifest):
            return {
                item["relative_path"]: (item["bytes"], item["sha256"])
                for entry in [*manifest.get("countries", []), *manifest.get("diseases", [])]
                for part in entry.get("parts", [])
                for item in part.get("files", {}).values()
            }
        committed = assets(json.loads(git("show", "HEAD:manifest.json")))
        current = assets(json.loads((output_dir / "manifest.json").read_text()))
        return {path for path in tracked - changed - {""} if path in committed and committed[path] == current.get(path)}
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        return set()


def _process_pool_context() -> multiprocessing.context.BaseContext:
    try:
        return multiprocessing.get_context("forkserver")
    except ValueError:
        return multiprocessing.get_context()


def _download_url(base_url: str, relative_path: str) -> str:
    return f"{base_url.rstrip('/')}/{relative_path.lstrip('/')}"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_sha256(path: Path) -> str:
    """Hash an existing artifact without loading large downloads into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _row_provenance_counts(rows: list[dict]) -> dict:
    return partitions._row_provenance_counts(rows)


def _sum_measure(rows: list[dict], field: str) -> int | float:
    return partitions._sum_measure(rows, field)


def _partition_metadata(dataset: dict, spec: PartitionSpec, rows: list[dict]) -> dict:
    return partitions._partition_metadata(dataset, spec, rows)


def _split_spec(spec: PartitionSpec, rows: list[dict]) -> tuple[tuple[PartitionSpec, list[dict]], tuple[PartitionSpec, list[dict]]]:
    return partitions._split_spec(spec, rows)


def _partition_writer() -> partitions.PartitionWriter:
    return partitions.PartitionWriter(json_bytes=_json_bytes, csv_bytes=_csv_bytes,
        xlsx_bytes=_xlsx_bytes, file_sha256=_file_sha256, write_if_changed=_write_if_changed,
        planned_partition_rows=_planned_partition_rows, public_formats=PUBLIC_FORMATS,
        github_max_file_bytes=GITHUB_MAX_FILE_BYTES)


def _bounded_partition_artifacts(dataset: dict, spec: PartitionSpec, rows: list[dict],
                                 max_file_bytes: int, *, json_content: bytes | None = None
                                 ) -> list[tuple[PartitionSpec, list[dict], dict[str, bytes]]]:
    return _partition_writer()._bounded_partition_artifacts(
        dataset, spec, rows, max_file_bytes, json_content=json_content)


def _remove_stale_files(root: Path, expected: set[Path]) -> int:
    removed = 0
    if not root.exists():
        return removed
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and path not in expected:
            path.unlink()
            removed += 1
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed


def _planned_partition_rows(rows: list[dict], existing_parts: dict[str, dict]
                            ) -> list[tuple[PartitionSpec, list[dict]]]:
    return partitions._planned_partition_rows(rows, existing_parts, partitioner=partition_rows)


def _dataset_parts(*, dataset: dict, rows: list[dict], output_dir: Path,
                   download_url_base: str, max_file_bytes: int, expected_paths: set[Path],
                   existing_parts: dict[str, dict], trusted_paths: set[str] | None = None,
                   counters: dict[str, int] | None = None) -> tuple[list[dict], int]:
    return _partition_writer()._dataset_parts(dataset=dataset, rows=rows, output_dir=output_dir,
        download_url_base=download_url_base, max_file_bytes=max_file_bytes,
        expected_paths=expected_paths, existing_parts=existing_parts,
        trusted_paths=trusted_paths, counters=counters)


def _build_dataset_parts_task(
    *,
    kind: str,
    dataset: dict,
    rows: list[dict],
    output_dir: Path,
    download_url_base: str,
    max_file_bytes: int,
    existing_parts: dict[str, dict],
    trusted_paths: set[str] | None = None,
) -> dict:
    """Build one dataset tree independently so changed partitions can overlap."""

    expected_paths: set[Path] = set()
    counters = {"reused_partitions": 0, "rebuilt_partitions": 0}
    parts, changed = _dataset_parts(
        dataset=dataset,
        rows=rows,
        output_dir=output_dir,
        download_url_base=download_url_base,
        max_file_bytes=max_file_bytes,
        expected_paths=expected_paths,
        existing_parts=existing_parts,
        trusted_paths=trusted_paths,
        counters=counters,
    )
    return {
        "kind": kind,
        "dataset": dataset,
        "row_count": len(rows),
        "provenance_counts": _row_provenance_counts(rows),
        "parts": parts,
        "changed": changed,
        "paths": expected_paths,
        **counters,
    }


def _build_country_parts_task(
    country_export: dict,
    output_dir: Path,
    download_url_base: str,
    max_file_bytes: int,
    existing_parts: dict[str, dict],
    trusted_paths: set[str] | None = None,
) -> dict:
    code = country_export["code"]
    path_id = code.lower()
    dataset = {
        "kind": "country",
        "directory": "countries",
        "dataset_id": path_id,
        "path_id": path_id,
        "slug": path_id,
        "name": country_export["country_name"],
    }
    rows = build_country_download_rows(
        country_export["country_data"], country_export["source_info"]
    )
    result = _build_dataset_parts_task(
        kind="country",
        dataset=dataset,
        rows=rows,
        output_dir=output_dir,
        download_url_base=download_url_base,
        max_file_bytes=max_file_bytes,
        existing_parts=existing_parts,
        trusted_paths=trusted_paths,
    )
    result["source_info"] = country_export["source_info"]
    return result


def _build_disease_parts_task(
    disease_export: dict,
    country_sources_by_code: dict[str, dict],
    country_names: dict[str, str],
    output_dir: Path,
    download_url_base: str,
    max_file_bytes: int,
    existing_parts: dict[str, dict],
    trusted_paths: set[str] | None = None,
    changed_country_codes: set[str] | None = None,
) -> dict:
    did = disease_export["disease_id"]
    path_id = did.lower()
    disease_data = disease_export["disease_data"]
    dataset = {
        "kind": "disease",
        "directory": "diseases",
        "dataset_id": did,
        "path_id": path_id,
        "slug": disease_data.get("slug"),
        "name": disease_data.get("name_en"),
    }
    fresh_data = disease_data
    if changed_country_codes is not None:
        fresh_data = {**disease_data, "country_series": {
            code: series for code, series in (disease_data.get("country_series") or {}).items()
            if code in changed_country_codes
        }}
    rows = build_disease_download_rows(
        fresh_data,
        country_sources_by_code,
        country_names,
    )
    legacy_count = 0
    if changed_country_codes is not None:
        rows, legacy_count = _merge_existing_disease_rows(
            disease_data, rows, changed_country_codes=changed_country_codes,
            country_names=country_names, output_dir=output_dir, existing_parts=existing_parts,
        )
    result = _build_dataset_parts_task(
        kind="disease",
        dataset=dataset,
        rows=rows,
        output_dir=output_dir,
        download_url_base=download_url_base,
        max_file_bytes=max_file_bytes,
        existing_parts=existing_parts,
        trusted_paths=trusted_paths,
    )
    result["country_codes"] = sorted({row["country_code"] for row in rows})
    result["source_info"] = disease_data.get("source_info") or []
    result["preserved_legacy_projection_rows"] = legacy_count
    return result


def _disease_dependency_sha256(
    item: dict, existing: dict, country_entries: dict, country_fingerprints: dict,
    country_names: dict, country_sources: dict, *, projection_code: str, max_file_bytes: int,
) -> str:
    """Fingerprint country inputs, never the previous disease output itself."""
    data = item["disease_data"]
    codes = set((data.get("country_series") or {}).keys())
    codes.update(str(country["code"]) for country in existing.get("countries", []) if country.get("code"))
    codes &= set(country_names)
    dependencies = {}
    for code in sorted(codes):
        entry = country_entries.get(code.lower()) or {}
        fingerprint = country_fingerprints.get(code.lower()) or entry.get("input_sha256")
        if not fingerprint:
            fingerprint = {part.get("id"): {
                "content_sha256": part.get("content_sha256"),
                "files": {name: file.get("sha256") for name, file in (part.get("files") or {}).items()},
            } for part in entry.get("parts") or []}
        dependencies[code] = {"input": fingerprint, "name": country_names.get(code), "source_info": country_sources.get(code)}
    return _input_sha256({
        "contract": "disease-country-inputs-v1", "countries": dependencies,
        "metadata": {key: data.get(key) for key in ("disease_id", "slug", "name_en", "name_zh", "category", "source_info")},
    }, projection_code=projection_code, max_file_bytes=max_file_bytes)


def build_direct_download_files(
    context: dict,
    output_dir: Path,
    *,
    download_url_base: str,
    max_file_bytes: int = DEFAULT_TARGET_FILE_BYTES,
    workers: int | None = None,
    changed_country_codes: set[str] | None = None,
    changed_disease_ids: set[str] | None = None,
) -> dict:
    """Write deterministic CSV/JSON/XLSX partitions and return the manifest.

    Unchanged windows are reused by content hash.  Changed country and disease
    trees write to disjoint paths, allowing their CSV/JSON/XLSX generation to
    run concurrently without changing manifest order.
    """

    if not download_url_base.startswith("https://raw.githubusercontent.com/"):
        raise ValueError("Public downloads require an absolute GitHub Raw URL base")
    if max_file_bytes >= GITHUB_MAX_FILE_BYTES:
        raise ValueError("The partition target must stay below GitHub's 100 MiB limit")

    output_dir.mkdir(parents=True, exist_ok=True)
    misspelled_country_dir = output_dir / "countrys"
    if misspelled_country_dir.exists():
        shutil.rmtree(misspelled_country_dir)
    existing_manifest_path = output_dir / "manifest.json"
    try:
        existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        existing_manifest = {}
    existing_country_parts = {
        entry.get("id"): {part.get("id"): part for part in entry.get("parts") or []}
        for entry in existing_manifest.get("countries") or []
    }
    existing_disease_parts = {
        entry.get("disease_id"): {part.get("id"): part for part in entry.get("parts") or []}
        for entry in existing_manifest.get("diseases") or []
    }
    expected_paths: set[Path] = set()
    generated_at = context["generated_at"]
    country_names = {
        country["code"]: country["name"] for country in context["countries_simple"]
    }
    country_entries_by_id = {
        entry["id"]: entry for entry in context["country_download_entries"]
    }
    disease_entries_by_id = {
        entry["disease_id"]: entry for entry in context["disease_download_entries"]
    }
    country_exports = context["country_exports"]
    disease_exports = context["disease_exports"]
    existing_country_entries = {
        str(entry.get("id")): entry for entry in existing_manifest.get("countries") or []
    }
    existing_disease_entries = {
        str(entry.get("disease_id")): entry for entry in existing_manifest.get("diseases") or []
    }
    projection_code = _projection_code_sha256()
    trusted_paths = _git_unchanged_paths(output_dir)
    verified_paths = set(trusted_paths)
    country_fingerprints: dict[str, str] = {}
    disease_fingerprints: dict[str, str] = {}
    disease_dependencies: dict[str, str] = {}
    requested_country_codes = {str(code).strip().upper() for code in changed_country_codes or ()}
    requested_disease_ids = {str(disease_id).strip() for disease_id in changed_disease_ids or ()}
    selected_country_codes: set[str] = set()
    selected_disease_ids: set[str] = set()
    fingerprint_started = time.perf_counter()
    for index, item in enumerate(country_exports, start=1):
        code = str(item["code"]).upper()
        path_id = code.lower()
        existing = existing_country_entries.get(path_id) or {}
        if changed_country_codes is not None and code not in requested_country_codes:
            if not _entry_files_available(
                existing,
                output_dir,
                "countries",
                path_id,
                verify_hash=False,
            ):
                raise RuntimeError(
                    f"Cannot reuse downloads for unchanged country {code}: files are missing or invalid. "
                    f"Refresh country scope {code} (or run a full data export) before publishing."
                )
            if index % 25 == 0 or index == len(country_exports):
                print(
                    f"  Download fingerprints: {index}/{len(country_exports)} countries",
                    flush=True,
                )
            continue
        available = _entry_files_available(existing, output_dir, "countries", path_id, trusted_paths=trusted_paths)
        if available:
            verified_paths.update(file["relative_path"] for part in existing["parts"] for file in part["files"].values())
        if changed_country_codes is None or code in requested_country_codes or not available:
            fingerprint = _input_sha256(
                {"country_data": item["country_data"], "country_name": item["country_name"], "source_info": item["source_info"]},
                projection_code=projection_code,
                max_file_bytes=max_file_bytes,
            )
            country_fingerprints[path_id] = fingerprint
            if fingerprint != existing.get("input_sha256") or not available:
                selected_country_codes.add(code)
        if index % 25 == 0 or index == len(country_exports):
            print(
                f"  Download fingerprints: {index}/{len(country_exports)} countries",
                flush=True,
            )
    for index, item in enumerate(disease_exports, start=1):
        disease_id = str(item["disease_id"])
        path_id = disease_id.lower()
        existing = existing_disease_entries.get(disease_id) or {}
        if changed_disease_ids is not None and disease_id not in requested_disease_ids:
            if not _entry_files_available(
                existing,
                output_dir,
                "diseases",
                path_id,
                verify_hash=False,
            ):
                raise RuntimeError(
                    f"Cannot reuse downloads for unchanged disease {disease_id}: files are missing or invalid. "
                    f"Refresh country scopes contributing to disease {disease_id} "
                    "(or run a full data export) before publishing."
                )
            if index % 25 == 0 or index == len(disease_exports):
                print(
                    f"  Download fingerprints: {index}/{len(disease_exports)} diseases",
                    flush=True,
                )
            continue
        available = _entry_files_available(existing, output_dir, "diseases", path_id, trusted_paths=trusted_paths)
        if available:
            verified_paths.update(file["relative_path"] for part in existing["parts"] for file in part["files"].values())
        if changed_disease_ids is None or disease_id in requested_disease_ids or not available:
            dependency = _disease_dependency_sha256(
                item, existing, existing_country_entries, country_fingerprints,
                country_names, context["country_sources_by_code"],
                projection_code=projection_code, max_file_bytes=max_file_bytes,
            )
            disease_dependencies[disease_id] = dependency
            country_codes = (item["disease_data"].get("country_series") or {}).keys()
            fingerprint = dependency if changed_country_codes is not None else _input_sha256(
                {
                    "disease_data": item["disease_data"],
                    "country_sources": {code: context["country_sources_by_code"].get(code) for code in country_codes},
                    "country_names": {code: country_names.get(code) for code in country_codes},
                },
                projection_code=projection_code,
                max_file_bytes=max_file_bytes,
            )
            disease_fingerprints[disease_id] = fingerprint
            expected_fingerprint = existing.get("dependency_sha256") if changed_country_codes is not None else existing.get("input_sha256")
            if fingerprint != expected_fingerprint or not available:
                selected_disease_ids.add(disease_id)
        if index % 25 == 0 or index == len(disease_exports):
            print(
                f"  Download fingerprints: {index}/{len(disease_exports)} diseases",
                flush=True,
            )
    print(
        "  Download fingerprint checks took "
        f"{time.perf_counter() - fingerprint_started:.1f}s",
        flush=True,
    )
    selected_country_exports = [
        item for item in country_exports if item["code"].upper() in selected_country_codes
    ]
    selected_disease_exports = [
        item for item in disease_exports if item["disease_id"] in selected_disease_ids
    ]
    tasks: list[tuple[str, dict]] = [
        *[("country", item) for item in selected_country_exports],
        *[("disease", item) for item in selected_disease_exports],
    ]
    task_count = len(tasks)
    worker_count = min(max(1, workers or DEFAULT_EXPORT_WORKERS), max(1, task_count))
    results_by_index: dict[int, dict] = {}

    if task_count:
        with ProcessPoolExecutor(
            max_workers=worker_count,
            mp_context=_process_pool_context(),
        ) as executor:
            pending: dict[Future, int] = {}
            next_index = 0
            max_pending = min(task_count, worker_count * 2)

            def submit_next() -> None:
                nonlocal next_index
                index = next_index
                next_index += 1
                kind, item = tasks[index]
                if kind == "country":
                    country_export = item
                    path_id = country_export["code"].lower()
                    previous_parts = existing_country_parts.get(path_id, {})
                    future = executor.submit(
                        _build_country_parts_task,
                        country_export,
                        output_dir,
                        download_url_base,
                        max_file_bytes,
                        previous_parts,
                        {file["relative_path"] for part in previous_parts.values()
                         for file in part.get("files", {}).values()
                         if file.get("relative_path") in verified_paths},
                    )
                else:
                    disease_export = item
                    previous_parts = existing_disease_parts.get(disease_export["disease_id"], {})
                    if changed_country_codes is not None:
                        # Avoid pickling historical curves into every worker.
                        # Unselected rows come from verified published JSON.
                        disease_data = disease_export["disease_data"]
                        if not previous_parts:
                            missing = sorted(code for code, series in (disease_data.get("country_series") or {}).items()
                                             if code not in requested_country_codes and (series.get("dates") or series.get("source_series")))
                            if missing:
                                raise ValueError(
                                    f"Disease {disease_export['disease_id']}, countries {', '.join(missing)}: "
                                    "no published download baseline exists; refresh these country scopes before merging."
                                )
                        disease_export = {**disease_export, "disease_data": {
                            **disease_data, "country_series": {
                                code: series for code, series in (disease_data.get("country_series") or {}).items()
                                if code in requested_country_codes
                            },
                        }}
                    future = executor.submit(
                        _build_disease_parts_task,
                        disease_export,
                        context["country_sources_by_code"],
                        country_names,
                        output_dir,
                        download_url_base,
                        max_file_bytes,
                        previous_parts,
                        {file["relative_path"] for part in previous_parts.values()
                         for file in part.get("files", {}).values()
                         if file.get("relative_path") in verified_paths},
                        requested_country_codes if changed_country_codes is not None else None,
                    )
                pending[future] = index

            while next_index < task_count and len(pending) < max_pending:
                submit_next()

            completed_count = 0
            while pending:
                done, _ = wait(pending, timeout=60, return_when=FIRST_COMPLETED)
                if not done:
                    active = [
                        f"{tasks[index][0]}:{tasks[index][1].get('code') or tasks[index][1].get('disease_id')}"
                        for index in pending.values()
                    ]
                    print(
                        f"  Download partitions: {completed_count}/{task_count} datasets complete; "
                        f"waiting for {', '.join(active)}",
                        flush=True,
                    )
                    continue
                for future in done:
                    index = pending.pop(future)
                    results_by_index[index] = future.result()
                    completed_count += 1
                    if next_index < task_count:
                        submit_next()
                if completed_count % 25 < len(done) or completed_count == task_count:
                    print(
                        f"  Download partitions: {completed_count}/{task_count} datasets complete",
                        flush=True,
                    )

    processed_country_results: dict[str, dict] = {}
    processed_disease_results: dict[str, dict] = {}
    changed_files = 0
    for index in range(task_count):
        result = results_by_index[index]
        kind = result["kind"]
        dataset = result["dataset"]
        expected_paths.update(result["paths"])
        changed_files += result["changed"]
        if kind == "country":
            processed_country_results[dataset["path_id"]] = result
            continue
        processed_disease_results[dataset["dataset_id"]] = result

    def reuse_existing_entry(entry: dict) -> dict:
        reused = copy.deepcopy(entry)
        for part in reused.get("parts") or []:
            for file_meta in (part.get("files") or {}).values():
                relative_path = str(file_meta.get("relative_path") or "")
                if relative_path:
                    expected_paths.add(output_dir / relative_path)
                    file_meta["url"] = _download_url(
                        download_url_base,
                        relative_path,
                    )
        return reused

    country_entries: list[dict] = []
    for country_export in country_exports:
        path_id = country_export["code"].lower()
        result = processed_country_results.get(path_id)
        if result is None:
            existing_entry = existing_country_entries.get(path_id)
            if existing_entry is None:
                raise RuntimeError(
                    f"Incremental download manifest is missing country {path_id}"
                )
            country_entries.append(reuse_existing_entry(existing_entry))
            continue
        dataset = result["dataset"]
        entry = dict(country_entries_by_id[dataset["path_id"]])
        entry.update(
            {
                "input_sha256": country_fingerprints.get(dataset["path_id"]) or _input_sha256(
                    {"country_data": country_export["country_data"], "country_name": country_export["country_name"], "source_info": country_export["source_info"]},
                    projection_code=projection_code,
                    max_file_bytes=max_file_bytes,
                ),
                "record_count": result["row_count"],
                **result["provenance_counts"],
                "includes_series_provenance": True,
                "parts": result["parts"],
                "source_info": result["source_info"],
            }
        )
        country_entries.append(entry)

    disease_entries: list[dict] = []
    for disease_export in disease_exports:
        disease_id = disease_export["disease_id"]
        result = processed_disease_results.get(disease_id)
        if result is None:
            existing_entry = existing_disease_entries.get(disease_id)
            if existing_entry is None:
                raise RuntimeError(
                    f"Incremental download manifest is missing disease {disease_id}"
                )
            disease_entries.append(reuse_existing_entry(existing_entry))
            continue
        dataset = result["dataset"]
        entry = dict(disease_entries_by_id[dataset["dataset_id"]])
        entry.update(
            {
                "dependency_sha256": disease_dependencies.get(disease_id),
                "input_sha256": disease_fingerprints.get(disease_id) or _input_sha256(
                    {
                        "disease_data": disease_export["disease_data"],
                        "country_sources": {code: context["country_sources_by_code"].get(code) for code in result["country_codes"]},
                        "country_names": {code: country_names.get(code) for code in result["country_codes"]},
                    },
                    projection_code=projection_code,
                    max_file_bytes=max_file_bytes,
                ),
                "record_count": result["row_count"],
                **result["provenance_counts"],
                "includes_series_provenance": True,
                "parts": result["parts"],
                "countries": [
                    {"code": code, "name": country_names.get(code)}
                    for code in result["country_codes"]
                ],
                "source_info": result["source_info"],
            }
        )
        disease_entries.append(entry)

    removed_files = _remove_stale_files(output_dir / "countries", expected_paths)
    removed_files += _remove_stale_files(output_dir / "diseases", expected_paths)
    manifest = {
        "schema_version": 4,
        "generated_at": generated_at,
        "includes_source_info": True,
        "includes_series_provenance": True,
        "formats": list(PUBLIC_FORMATS),
        "partitioning": {
            "strategy": "stable_calendar_windows",
            "historical_years": 5,
            "special_window": "2020-2025",
            "bridge_window": "2026-2029",
            "rolling_anchor_year": ROLLING_WINDOW_START,
            "target_file_bytes": max_file_bytes,
            "github_max_file_bytes": GITHUB_MAX_FILE_BYTES,
        },
        "download_url_base": download_url_base.rstrip("/"),
        "countries": country_entries,
        "diseases": disease_entries,
        "generation": {
            "changed_files": changed_files,
            "removed_files": removed_files,
            "preserved_legacy_projection_rows": sum(result.get("preserved_legacy_projection_rows", 0) for result in results_by_index.values()),
            "selected_datasets": task_count,
            "rebuilt_partitions": sum(result["rebuilt_partitions"] for result in results_by_index.values()),
            "reused_partitions": sum(result["reused_partitions"] for result in results_by_index.values())
            + sum(len(entry.get("parts") or []) for entry in country_entries
                  if entry["id"] not in processed_country_results)
            + sum(len(entry.get("parts") or []) for entry in disease_entries
                  if entry["disease_id"] not in processed_disease_results),
        },
    }
    # Export timings/counters describe a run, not a dataset version. Keep them
    # in the return value for diagnostics, never in the committed manifest.
    generation = manifest.pop("generation")
    def stable(value):
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items() if key not in {"generated_at", "generation"}}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    if stable(manifest) == stable(existing_manifest):
        manifest = copy.deepcopy(existing_manifest)
        manifest.pop("generation", None)
    manifest_path = output_dir / "manifest.json"
    _write_if_changed(
        manifest_path,
        json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
    )
    return {**manifest, "generation": generation}


__all__ = [
    "DEFAULT_TARGET_FILE_BYTES",
    "DOWNLOAD_FIELDS",
    "GITHUB_MAX_FILE_BYTES",
    "PUBLIC_FORMATS",
    "PartitionSpec",
    "build_country_download_rows",
    "build_direct_download_files",
    "build_disease_download_rows",
    "partition_rows",
]
