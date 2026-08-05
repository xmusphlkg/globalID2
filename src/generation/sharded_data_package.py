"""Deterministic, year-partitioned gzip NDJSON data packages.

This module deliberately uses only the Python standard library.  It has no
publishing or remote-storage behaviour: callers provide records and a local
output directory, and receive a self-validating manifest v2 package.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import quote
from uuid import uuid4


MANIFEST_VERSION = 2
MANIFEST_FILENAME = "manifest.json"
PACKAGE_MARKER_FILENAME = ".sharded-data-package-v2"
PACKAGE_MARKER_CONTENT = "globalid-sharded-data-package-v2\n"
DEFAULT_MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ShardedDataPackageError(RuntimeError):
    """Base error for deterministic package construction and validation."""


class PackageBuildError(ShardedDataPackageError):
    """Raised when input records cannot form a valid deterministic package."""


class PackageValidationError(ShardedDataPackageError):
    """Raised when an on-disk package does not match its manifest."""


class UnsafePackagePathError(ShardedDataPackageError):
    """Raised when an output or manifest path could escape package boundaries."""


@dataclass(frozen=True)
class PackageValidationResult:
    """Verified package totals returned by the offline validator."""

    manifest: dict[str, Any]
    shard_count: int
    record_count: int
    compressed_bytes: int
    uncompressed_bytes: int


@dataclass(frozen=True)
class _PreparedRecord:
    report_date: date
    line: bytes


@dataclass(frozen=True)
class _CanonicalFact:
    country_code: str
    disease_id: str
    report_date: date
    line: bytes


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
    except (TypeError, ValueError) as exc:
        raise PackageBuildError(f"Value is not canonical JSON data: {exc}") from exc
    return encoded.encode("utf-8")


def _json_object(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PackageBuildError(f"{label} metadata must be a mapping")
    encoded = _canonical_json_bytes(value)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise PackageBuildError(f"{label} metadata must encode to a JSON object")
    return decoded


def _metadata_by_key(
    value: Mapping[str, Mapping[str, Any]] | None,
    label: str,
) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PackageBuildError(f"{label} must be a mapping keyed by identifier")
    result: dict[str, dict[str, Any]] = {}
    for key, metadata in value.items():
        if not isinstance(key, str) or not key.strip():
            raise PackageBuildError(f"{label} contains an invalid identifier")
        result[key.strip()] = _json_object(metadata, f"{label}[{key!r}]")
    return result


def _normalise_date(value: object, *, field: str) -> date:
    if isinstance(value, datetime):
        normalized = (
            value.astimezone(timezone.utc) if value.tzinfo is not None else value
        )
        return normalized.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise PackageBuildError(
            f"Record field {field!r} must be an ISO date or datetime"
        )

    raw = value.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass

    normalized_text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalized_text)
    except ValueError as exc:
        raise PackageBuildError(
            f"Record field {field!r} is not an ISO date or datetime: {value!r}"
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date()


def _prepare_records(
    records: Iterable[Mapping[str, Any]],
    *,
    date_field: str,
    max_uncompressed_bytes: int,
) -> list[_PreparedRecord]:
    prepared: list[_PreparedRecord] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise PackageBuildError(f"Record {index} must be a mapping")
        if date_field not in record:
            raise PackageBuildError(
                f"Record {index} does not contain date field {date_field!r}"
            )
        report_date = _normalise_date(record[date_field], field=date_field)
        line = _canonical_json_bytes(record) + b"\n"
        if len(line) > max_uncompressed_bytes:
            raise PackageBuildError(
                f"Record {index} requires {len(line)} uncompressed bytes, exceeding "
                f"the per-shard limit {max_uncompressed_bytes}"
            )
        prepared.append(_PreparedRecord(report_date=report_date, line=line))

    return sorted(
        prepared,
        key=lambda item: (item.report_date.year, item.report_date, item.line),
    )


def _partition_value(record: Mapping[str, Any], field: str, index: int) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PackageBuildError(
            f"Record {index} field {field!r} must be a non-empty string"
        )
    if value != value.strip():
        raise PackageBuildError(
            f"Record {index} field {field!r} must not contain surrounding whitespace"
        )
    return value


def _prepare_canonical_facts(
    records: Iterable[Mapping[str, Any]],
    *,
    date_field: str,
    max_uncompressed_bytes: int,
) -> list[_CanonicalFact]:
    prepared: list[_CanonicalFact] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise PackageBuildError(f"Record {index} must be a mapping")
        if date_field not in record:
            raise PackageBuildError(
                f"Record {index} does not contain date field {date_field!r}"
            )
        country_code = _partition_value(record, "country_code", index)
        disease_id = _partition_value(record, "disease_id", index)
        report_date = _normalise_date(record[date_field], field=date_field)
        line = _canonical_json_bytes(record) + b"\n"
        if len(line) > max_uncompressed_bytes:
            raise PackageBuildError(
                f"Record {index} requires {len(line)} uncompressed bytes, exceeding "
                f"the per-shard limit {max_uncompressed_bytes}"
            )
        prepared.append(
            _CanonicalFact(
                country_code=country_code,
                disease_id=disease_id,
                report_date=report_date,
                line=line,
            )
        )
    return sorted(
        prepared,
        key=lambda item: (
            item.country_code,
            item.disease_id,
            item.report_date.year,
            item.report_date,
            item.line,
        ),
    )


def _safe_output_dir(output_dir: Path) -> Path:
    candidate = Path(output_dir).expanduser()
    if not candidate.name or candidate.name in {".", ".."}:
        raise UnsafePackagePathError(
            f"Output must name a dedicated package directory: {output_dir}"
        )
    if candidate.is_symlink():
        raise UnsafePackagePathError("Package output directory cannot be a symlink")

    parent = candidate.parent.resolve()
    resolved = parent / candidate.name
    if resolved == Path(resolved.anchor):
        raise UnsafePackagePathError("Filesystem root cannot be a package output")
    if resolved.exists():
        marker = resolved / PACKAGE_MARKER_FILENAME
        if not resolved.is_dir() or marker.is_symlink() or not marker.is_file():
            raise UnsafePackagePathError(
                "Refusing to replace an existing directory that was not created "
                "by the sharded data package builder"
            )
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _gzip_bytes(payload: bytes) -> bytes:
    raw_handle = io.BytesIO()
    with raw_handle:
        # Empty filename and mtime=0 remove host path and wall-clock entropy
        # from the gzip header, making identical inputs byte-for-byte stable.
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=raw_handle,
            mtime=0,
        ) as gzip_handle:
            gzip_handle.write(payload)
        return raw_handle.getvalue()


def _write_gzip(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_gzip_bytes(payload))


def _shard_relative_path(year: int, part: int) -> str:
    return f"shards/year={year:04d}/part-{part:05d}.ndjson.gz"


def _path_partition(value: str) -> str:
    return quote(value, safe="-._~")


def _canonical_shard_relative_path(
    sha256: str,
) -> str:
    return f"objects/sha256/{sha256[:2]}/{sha256}.ndjson.gz"


def _write_shards(
    staging_dir: Path,
    prepared: list[_PreparedRecord],
    *,
    max_uncompressed_bytes: int,
) -> list[dict[str, Any]]:
    by_year: dict[int, list[_PreparedRecord]] = defaultdict(list)
    for record in prepared:
        by_year[record.report_date.year].append(record)

    shards: list[dict[str, Any]] = []
    for year in sorted(by_year):
        current: list[_PreparedRecord] = []
        current_bytes = 0
        part = 0

        def flush() -> None:
            nonlocal current, current_bytes, part
            if not current:
                return
            part += 1
            relative_path = _shard_relative_path(year, part)
            shard_path = staging_dir.joinpath(*PurePosixPath(relative_path).parts)
            payload = b"".join(item.line for item in current)
            _write_gzip(shard_path, payload)
            shards.append(
                {
                    "path": relative_path,
                    "year": year,
                    "part": part,
                    "record_count": len(current),
                    "date_start": current[0].report_date.isoformat(),
                    "date_end": current[-1].report_date.isoformat(),
                    "compressed_bytes": shard_path.stat().st_size,
                    "uncompressed_bytes": len(payload),
                    "sha256": _sha256_file(shard_path),
                }
            )
            current = []
            current_bytes = 0

        for record in by_year[year]:
            if current and current_bytes + len(record.line) > max_uncompressed_bytes:
                flush()
            current.append(record)
            current_bytes += len(record.line)
        flush()

    return shards


def _write_canonical_fact_shards(
    staging_dir: Path,
    prepared: list[_CanonicalFact],
    *,
    max_uncompressed_bytes: int,
) -> list[dict[str, Any]]:
    pairs: dict[tuple[str, str], list[_CanonicalFact]] = defaultdict(list)
    for record in prepared:
        pairs[(record.country_code, record.disease_id)].append(record)

    shards: list[dict[str, Any]] = []
    for country_code, disease_id in sorted(pairs):
        pair_records = pairs[(country_code, disease_id)]
        pair_bytes = sum(len(record.line) for record in pair_records)
        overflow_partitions: list[tuple[int | None, list[_CanonicalFact]]]
        if pair_bytes <= max_uncompressed_bytes:
            overflow_partitions = [(None, pair_records)]
        else:
            by_year: dict[int, list[_CanonicalFact]] = defaultdict(list)
            for record in pair_records:
                by_year[record.report_date.year].append(record)
            overflow_partitions = [
                (year, by_year[year]) for year in sorted(by_year)
            ]

        for overflow_year, partition_records in overflow_partitions:
            current: list[_CanonicalFact] = []
            current_bytes = 0
            part = 0

            def flush() -> None:
                nonlocal current, current_bytes, part
                if not current:
                    return
                part += 1
                payload = b"".join(item.line for item in current)
                compressed_payload = _gzip_bytes(payload)
                object_hash = hashlib.sha256(compressed_payload).hexdigest()
                relative_path = _canonical_shard_relative_path(object_hash)
                shard_path = staging_dir.joinpath(
                    *PurePosixPath(relative_path).parts
                )
                shard_path.parent.mkdir(parents=True, exist_ok=True)
                if shard_path.exists():
                    if shard_path.read_bytes() != compressed_payload:
                        raise PackageBuildError(
                            f"SHA-256 object collision at {relative_path}"
                        )
                else:
                    shard_path.write_bytes(compressed_payload)
                shards.append(
                    {
                        "path": relative_path,
                        "country_code": country_code,
                        "disease_id": disease_id,
                        "year": overflow_year,
                        "part": part,
                        "record_count": len(current),
                        "date_start": current[0].report_date.isoformat(),
                        "date_end": current[-1].report_date.isoformat(),
                        "compressed_bytes": len(compressed_payload),
                        "uncompressed_bytes": len(payload),
                        "sha256": object_hash,
                    }
                )
                current = []
                current_bytes = 0

            for record in partition_records:
                if (
                    current
                    and current_bytes + len(record.line)
                    > max_uncompressed_bytes
                ):
                    flush()
                current.append(record)
                current_bytes += len(record.line)
            flush()

    return shards


def _manifest(
    *,
    schema: dict[str, Any],
    release: dict[str, Any],
    dataset: dict[str, Any],
    date_field: str,
    max_uncompressed_bytes: int,
    shards: list[dict[str, Any]],
) -> dict[str, Any]:
    dataset_metadata = dict(dataset)
    configured_date_field = dataset_metadata.get("date_field")
    if configured_date_field is not None and configured_date_field != date_field:
        raise PackageBuildError(
            "dataset.date_field conflicts with the package date_field argument"
        )
    dataset_metadata["date_field"] = date_field
    date_starts = [item["date_start"] for item in shards]
    date_ends = [item["date_end"] for item in shards]
    return {
        "manifest_version": MANIFEST_VERSION,
        "package_mode": "year_partitioned",
        "schema": schema,
        "release": release,
        "dataset": dataset_metadata,
        "format": {
            "media_type": "application/x-ndjson",
            "compression": "gzip",
            "encoding": "utf-8",
            "partitioning": ["year", "part"],
            "ordering": ["year", "date", "canonical_json"],
            "max_uncompressed_bytes": max_uncompressed_bytes,
        },
        "shards": shards,
        "totals": {
            "shard_count": len(shards),
            "record_count": sum(item["record_count"] for item in shards),
            "date_start": min(date_starts) if date_starts else None,
            "date_end": max(date_ends) if date_ends else None,
            "compressed_bytes": sum(item["compressed_bytes"] for item in shards),
            "uncompressed_bytes": sum(item["uncompressed_bytes"] for item in shards),
        },
    }


def _index_relative_path(kind: str, key: str) -> str:
    return f"indexes/{kind}/{_path_partition(key)}.json"


def _write_canonical_indexes(
    staging_dir: Path,
    shards: list[dict[str, Any]],
    *,
    country_metadata: Mapping[str, Mapping[str, Any]],
    disease_metadata: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        "countries": defaultdict(list),
        "diseases": defaultdict(list),
    }
    for shard in shards:
        grouped["countries"][shard["country_code"]].append(shard)
        grouped["diseases"][shard["disease_id"]].append(shard)

    descriptors: dict[str, list[dict[str, Any]]] = {
        "countries": [],
        "diseases": [],
    }
    key_fields = {"countries": "country_code", "diseases": "disease_id"}
    metadata_sets = {
        "countries": country_metadata,
        "diseases": disease_metadata,
    }
    for kind in ("countries", "diseases"):
        all_keys = set(grouped[kind]) | set(metadata_sets[kind])
        for key in sorted(all_keys):
            selected = grouped[kind][key]
            relative_path = _index_relative_path(kind, key)
            date_start = (
                min(item["date_start"] for item in selected) if selected else None
            )
            date_end = (
                max(item["date_end"] for item in selected) if selected else None
            )
            index_document = {
                "index_version": 1,
                "kind": "country" if kind == "countries" else "disease",
                "key": key,
                "record_count": sum(item["record_count"] for item in selected),
                "date_start": date_start,
                "date_end": date_end,
                "metadata": metadata_sets[kind].get(key, {}),
                # Paths point into the single canonical fact store. No facts are
                # copied into the country and disease discovery indexes.
                "shards": [item["path"] for item in selected],
            }
            index_path = staging_dir.joinpath(*PurePosixPath(relative_path).parts)
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_payload = _canonical_json_bytes(index_document) + b"\n"
            index_path.write_bytes(index_payload)
            descriptors[kind].append(
                {
                    key_fields[kind]: key,
                    "path": relative_path,
                    "shard_count": len(selected),
                    "record_count": index_document["record_count"],
                    "date_start": date_start,
                    "date_end": date_end,
                    "bytes": len(index_payload),
                    "sha256": hashlib.sha256(index_payload).hexdigest(),
                }
            )
    return descriptors


def _canonical_facts_manifest(
    *,
    schema: dict[str, Any],
    release: dict[str, Any],
    dataset: dict[str, Any],
    date_field: str,
    max_uncompressed_bytes: int,
    shards: list[dict[str, Any]],
    indexes: dict[str, list[dict[str, Any]]],
    source_catalog: dict[str, Any],
) -> dict[str, Any]:
    dataset_metadata = dict(dataset)
    configured_date_field = dataset_metadata.get("date_field")
    if configured_date_field is not None and configured_date_field != date_field:
        raise PackageBuildError(
            "dataset.date_field conflicts with the package date_field argument"
        )
    dataset_metadata.update(
        {
            "date_field": date_field,
            "country_field": "country_code",
            "disease_field": "disease_id",
        }
    )
    starts = [item["date_start"] for item in shards]
    ends = [item["date_end"] for item in shards]
    content_root = {
        "schema": schema,
        "dataset": dataset_metadata,
        "shards": shards,
        "indexes": indexes,
        "source_catalog": source_catalog,
    }
    content_sha256 = hashlib.sha256(
        _canonical_json_bytes(content_root)
    ).hexdigest()
    release_metadata = dict(release)
    release_id_prefix = release_metadata.pop("release_id_prefix", None)
    if release_id_prefix is not None:
        if (
            not isinstance(release_id_prefix, str)
            or not release_id_prefix.strip()
            or "release_id" in release_metadata
        ):
            raise PackageBuildError(
                "release_id_prefix must be non-empty and cannot be combined "
                "with release_id"
            )
        release_metadata["release_id"] = (
            f"{release_id_prefix.strip()}-{content_sha256[:12]}"
        )
    release_metadata["content_sha256"] = content_sha256
    return {
        "manifest_version": MANIFEST_VERSION,
        "package_mode": "canonical_facts",
        "schema": schema,
        "release": release_metadata,
        "dataset": dataset_metadata,
        "format": {
            "media_type": "application/x-ndjson",
            "compression": "gzip",
            "encoding": "utf-8",
            "partition_strategy": "pair_with_year_overflow",
            "partitioning": [
                "country_code",
                "disease_id",
                "overflow_year",
                "part",
            ],
            "ordering": [
                "country_code",
                "disease_id",
                "overflow_year",
                "date",
                "canonical_json",
            ],
            "max_uncompressed_bytes": max_uncompressed_bytes,
        },
        "shards": shards,
        "indexes": indexes,
        "source_catalog": source_catalog,
        "totals": {
            "shard_count": len(shards),
            "record_count": sum(item["record_count"] for item in shards),
            "date_start": min(starts) if starts else None,
            "date_end": max(ends) if ends else None,
            "compressed_bytes": sum(item["compressed_bytes"] for item in shards),
            "uncompressed_bytes": sum(item["uncompressed_bytes"] for item in shards),
        },
    }


def _write_manifest(staging_dir: Path, manifest: Mapping[str, Any]) -> None:
    payload = _canonical_json_bytes(manifest) + b"\n"
    (staging_dir / MANIFEST_FILENAME).write_bytes(payload)
    (staging_dir / PACKAGE_MARKER_FILENAME).write_text(
        PACKAGE_MARKER_CONTENT,
        encoding="utf-8",
    )


def _atomic_replace_directory(staging_dir: Path, output_dir: Path) -> None:
    if not output_dir.exists():
        os.replace(staging_dir, output_dir)
        return

    backup = output_dir.parent / f".{output_dir.name}.backup-{uuid4().hex}"
    os.replace(output_dir, backup)
    try:
        os.replace(staging_dir, output_dir)
    except BaseException:
        # Restore the last complete package if publication of the validated
        # staging directory fails between the two same-filesystem renames.
        os.replace(backup, output_dir)
        raise
    shutil.rmtree(backup)


def build_sharded_data_package(
    records: Iterable[Mapping[str, Any]],
    output_dir: Path,
    *,
    date_field: str,
    schema: Mapping[str, Any],
    release: Mapping[str, Any],
    dataset: Mapping[str, Any],
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> dict[str, Any]:
    """Build, validate, and atomically install a deterministic local package."""

    if not isinstance(date_field, str) or not date_field.strip():
        raise PackageBuildError("date_field must be a non-empty string")
    if (
        isinstance(max_uncompressed_bytes, bool)
        or not isinstance(max_uncompressed_bytes, int)
        or max_uncompressed_bytes <= 0
    ):
        raise PackageBuildError("max_uncompressed_bytes must be a positive integer")

    destination = _safe_output_dir(output_dir)
    schema_metadata = _json_object(schema, "schema")
    release_metadata = _json_object(release, "release")
    dataset_metadata = _json_object(dataset, "dataset")
    prepared = _prepare_records(
        records,
        date_field=date_field,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=destination.parent,
        )
    )
    try:
        shards = _write_shards(
            staging_dir,
            prepared,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
        manifest = _manifest(
            schema=schema_metadata,
            release=release_metadata,
            dataset=dataset_metadata,
            date_field=date_field,
            max_uncompressed_bytes=max_uncompressed_bytes,
            shards=shards,
        )
        _write_manifest(staging_dir, manifest)
        validate_sharded_data_package(staging_dir)
        _atomic_replace_directory(staging_dir, destination)
        return manifest
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise


def build_canonical_facts_release(
    records: Iterable[Mapping[str, Any]],
    output_dir: Path,
    *,
    date_field: str,
    schema: Mapping[str, Any],
    release: Mapping[str, Any],
    dataset: Mapping[str, Any],
    country_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    disease_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    source_catalog: Mapping[str, Any] | None = None,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> dict[str, Any]:
    """Build one canonical fact store with country and disease shard indexes.

    Every input object must contain ``country_code``, ``disease_id``, and the
    configured date field. Facts are written once to content-addressed gzip
    objects partitioned by ``(country_code, disease_id, year)`` before part
    splitting. Both index families contain paths to those same objects.
    """

    if not isinstance(date_field, str) or not date_field.strip():
        raise PackageBuildError("date_field must be a non-empty string")
    if (
        isinstance(max_uncompressed_bytes, bool)
        or not isinstance(max_uncompressed_bytes, int)
        or max_uncompressed_bytes <= 0
    ):
        raise PackageBuildError("max_uncompressed_bytes must be a positive integer")

    destination = _safe_output_dir(output_dir)
    schema_metadata = _json_object(schema, "schema")
    release_metadata = _json_object(release, "release")
    dataset_metadata = _json_object(dataset, "dataset")
    countries_metadata = _metadata_by_key(country_metadata, "country_metadata")
    diseases_metadata = _metadata_by_key(disease_metadata, "disease_metadata")
    sources_metadata = _json_object(source_catalog or {}, "source_catalog")
    prepared = _prepare_canonical_facts(
        records,
        date_field=date_field,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=destination.parent,
        )
    )
    try:
        shards = _write_canonical_fact_shards(
            staging_dir,
            prepared,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
        indexes = _write_canonical_indexes(
            staging_dir,
            shards,
            country_metadata=countries_metadata,
            disease_metadata=diseases_metadata,
        )
        manifest = _canonical_facts_manifest(
            schema=schema_metadata,
            release=release_metadata,
            dataset=dataset_metadata,
            date_field=date_field,
            max_uncompressed_bytes=max_uncompressed_bytes,
            shards=shards,
            indexes=indexes,
            source_catalog=sources_metadata,
        )
        _write_manifest(staging_dir, manifest)
        validate_sharded_data_package(staging_dir)
        _atomic_replace_directory(staging_dir, destination)
        return manifest
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise


def _safe_manifest_shard_path(
    package_dir: Path,
    relative_path: object,
    *,
    package_mode: str,
    year: int | None,
    part: int,
    sha256: str,
) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise UnsafePackagePathError("Shard path must be a non-empty string")
    if "\\" in relative_path or "\x00" in relative_path:
        raise UnsafePackagePathError(f"Unsafe shard path: {relative_path!r}")
    pure_path = PurePosixPath(relative_path)
    if pure_path.is_absolute() or any(
        piece in {"", ".", ".."} for piece in pure_path.parts
    ):
        raise UnsafePackagePathError(f"Unsafe shard path: {relative_path!r}")

    expected = (
        _canonical_shard_relative_path(sha256)
        if package_mode == "canonical_facts"
        else _shard_relative_path(year, part)
    )
    if relative_path != expected:
        raise UnsafePackagePathError(
            f"Shard path {relative_path!r} does not match expected {expected!r}"
        )

    candidate = package_dir.joinpath(*pure_path.parts)
    root_resolved = package_dir.resolve()
    if candidate.is_symlink():
        raise UnsafePackagePathError(f"Shard cannot be a symlink: {relative_path}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PackageValidationError(f"Shard is missing: {relative_path}") from exc
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafePackagePathError(
            f"Shard escapes package directory: {relative_path}"
        ) from exc
    return resolved


def _manifest_int(mapping: Mapping[str, Any], key: str, *, minimum: int = 0) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PackageValidationError(
            f"Manifest field {key!r} must be an integer >= {minimum}"
        )
    return value


def _validate_shard(
    package_dir: Path,
    shard: Mapping[str, Any],
    *,
    package_mode: str,
    canonical_year_optional: bool,
    date_field: str,
    max_uncompressed_bytes: int,
) -> tuple[int, int, int, str, str]:
    raw_year = shard.get("year")
    year = (
        None
        if canonical_year_optional and raw_year is None
        else _manifest_int(shard, "year", minimum=1)
    )
    part = _manifest_int(shard, "part", minimum=1)
    expected_hash = shard.get("sha256")
    if not isinstance(expected_hash, str) or not _SHA256_PATTERN.fullmatch(
        expected_hash
    ):
        raise PackageValidationError(f"Invalid SHA-256 for {shard.get('path')}")
    shard_path = _safe_manifest_shard_path(
        package_dir,
        shard.get("path"),
        package_mode=package_mode,
        year=year,
        part=part,
        sha256=expected_hash,
    )

    expected_compressed = _manifest_int(shard, "compressed_bytes")
    actual_compressed = shard_path.stat().st_size
    if actual_compressed != expected_compressed:
        raise PackageValidationError(
            f"Compressed size mismatch for {shard['path']}: manifest="
            f"{expected_compressed}, actual={actual_compressed}"
        )
    actual_hash = _sha256_file(shard_path)
    if actual_hash != expected_hash:
        raise PackageValidationError(
            f"SHA-256 mismatch for {shard['path']}: manifest={expected_hash}, "
            f"actual={actual_hash}"
        )

    record_count = 0
    uncompressed_bytes = 0
    dates: list[str] = []
    previous_sort_key: tuple[str, bytes] | None = None
    try:
        with gzip.open(shard_path, "rb") as handle:
            for line_number, line in enumerate(handle, start=1):
                uncompressed_bytes += len(line)
                if not line.endswith(b"\n") or line == b"\n":
                    raise PackageValidationError(
                        f"Invalid NDJSON line {line_number} in {shard['path']}"
                    )
                try:
                    record = json.loads(line[:-1].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise PackageValidationError(
                        f"Invalid JSON line {line_number} in {shard['path']}: {exc}"
                    ) from exc
                if not isinstance(record, dict):
                    raise PackageValidationError(
                        f"NDJSON line {line_number} in {shard['path']} is not an object"
                    )
                try:
                    canonical_line = _canonical_json_bytes(record) + b"\n"
                except PackageBuildError as exc:
                    raise PackageValidationError(
                        f"Non-canonical JSON line {line_number} in "
                        f"{shard['path']}: {exc}"
                    ) from exc
                if line != canonical_line:
                    raise PackageValidationError(
                        f"Non-canonical JSON line {line_number} in {shard['path']}"
                    )
                if date_field not in record:
                    raise PackageValidationError(
                        f"Line {line_number} in {shard['path']} lacks {date_field!r}"
                    )
                try:
                    report_date = _normalise_date(
                        record[date_field],
                        field=date_field,
                    )
                except PackageBuildError as exc:
                    raise PackageValidationError(
                        f"Invalid date on line {line_number} in {shard['path']}: {exc}"
                    ) from exc
                if year is not None and report_date.year != year:
                    raise PackageValidationError(
                        f"Line {line_number} in {shard['path']} belongs to year "
                        f"{report_date.year}, not {year}"
                    )
                if package_mode == "canonical_facts":
                    expected_country = shard.get("country_code")
                    expected_disease = shard.get("disease_id")
                    if record.get("country_code") != expected_country:
                        raise PackageValidationError(
                            f"Line {line_number} in {shard['path']} has country_code "
                            f"{record.get('country_code')!r}, expected {expected_country!r}"
                        )
                    if record.get("disease_id") != expected_disease:
                        raise PackageValidationError(
                            f"Line {line_number} in {shard['path']} has disease_id "
                            f"{record.get('disease_id')!r}, expected {expected_disease!r}"
                        )
                date_text = report_date.isoformat()
                sort_key = (date_text, line)
                if previous_sort_key is not None and sort_key < previous_sort_key:
                    raise PackageValidationError(
                        f"Records are not stably sorted in {shard['path']}"
                    )
                previous_sort_key = sort_key
                dates.append(date_text)
                record_count += 1
    except (EOFError, OSError) as exc:
        raise PackageValidationError(
            f"Cannot decompress shard {shard['path']}: {exc}"
        ) from exc

    expected_count = _manifest_int(shard, "record_count", minimum=1)
    expected_uncompressed = _manifest_int(shard, "uncompressed_bytes", minimum=1)
    if record_count != expected_count:
        raise PackageValidationError(
            f"Record count mismatch for {shard['path']}: manifest={expected_count}, "
            f"actual={record_count}"
        )
    if uncompressed_bytes != expected_uncompressed:
        raise PackageValidationError(
            f"Uncompressed size mismatch for {shard['path']}: manifest="
            f"{expected_uncompressed}, actual={uncompressed_bytes}"
        )
    if uncompressed_bytes > max_uncompressed_bytes:
        raise PackageValidationError(
            f"Shard {shard['path']} exceeds max_uncompressed_bytes"
        )

    date_start = shard.get("date_start")
    date_end = shard.get("date_end")
    if date_start != dates[0] or date_end != dates[-1]:
        raise PackageValidationError(
            f"Date range mismatch for {shard['path']}: manifest={date_start}.."
            f"{date_end}, actual={dates[0]}..{dates[-1]}"
        )
    return (
        record_count,
        actual_compressed,
        uncompressed_bytes,
        dates[0],
        dates[-1],
    )


def _safe_index_path(package_dir: Path, relative_path: object, expected: str) -> Path:
    if not isinstance(relative_path, str) or relative_path != expected:
        raise UnsafePackagePathError(
            f"Index path {relative_path!r} does not match expected {expected!r}"
        )
    pure_path = PurePosixPath(relative_path)
    if pure_path.is_absolute() or "\\" in relative_path or ".." in pure_path.parts:
        raise UnsafePackagePathError(f"Unsafe index path: {relative_path!r}")
    candidate = package_dir.joinpath(*pure_path.parts)
    if candidate.is_symlink():
        raise UnsafePackagePathError(f"Index cannot be a symlink: {relative_path}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(package_dir.resolve())
    except FileNotFoundError as exc:
        raise PackageValidationError(f"Index is missing: {relative_path}") from exc
    except ValueError as exc:
        raise UnsafePackagePathError(
            f"Index escapes package directory: {relative_path}"
        ) from exc
    return resolved


def _validate_canonical_indexes(
    package_dir: Path,
    indexes: object,
    shards: list[dict[str, Any]],
) -> None:
    if not isinstance(indexes, dict) or set(indexes) != {"countries", "diseases"}:
        raise PackageValidationError(
            "Canonical facts manifest requires countries and diseases indexes"
        )
    specifications = {
        "countries": ("country", "country_code"),
        "diseases": ("disease", "disease_id"),
    }
    for collection_name, (kind, key_field) in specifications.items():
        descriptors = indexes.get(collection_name)
        if not isinstance(descriptors, list):
            raise PackageValidationError(
                f"Manifest index collection {collection_name!r} must be a list"
            )
        expected_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for shard in shards:
            key = shard.get(key_field)
            if not isinstance(key, str) or not key:
                raise PackageValidationError(
                    f"Canonical shard lacks non-empty {key_field}"
                )
            expected_by_key[key].append(shard)

        seen_keys: list[str] = []
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                raise PackageValidationError(
                    f"Manifest {collection_name} index descriptor must be an object"
                )
            key = descriptor.get(key_field)
            if not isinstance(key, str) or not key:
                raise PackageValidationError(
                    f"Unexpected {collection_name} index key: {key!r}"
                )
            seen_keys.append(key)
            selected = expected_by_key.get(key, [])
            expected_path = _index_relative_path(collection_name, key)
            index_path = _safe_index_path(
                package_dir,
                descriptor.get("path"),
                expected_path,
            )
            try:
                raw_index = index_path.read_bytes()
                document = json.loads(raw_index.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PackageValidationError(
                    f"Cannot read index {expected_path}: {exc}"
                ) from exc
            if not isinstance(document, dict):
                raise PackageValidationError(
                    f"Index {expected_path} must contain a JSON object"
                )
            descriptor_bytes = _manifest_int(descriptor, "bytes", minimum=1)
            descriptor_hash = descriptor.get("sha256")
            if len(raw_index) != descriptor_bytes:
                raise PackageValidationError(
                    f"Index {expected_path} byte size mismatch"
                )
            if (
                not isinstance(descriptor_hash, str)
                or not _SHA256_PATTERN.fullmatch(descriptor_hash)
                or hashlib.sha256(raw_index).hexdigest() != descriptor_hash
            ):
                raise PackageValidationError(f"Index {expected_path} SHA-256 mismatch")
            try:
                canonical_index = _canonical_json_bytes(document) + b"\n"
            except PackageBuildError as exc:
                raise PackageValidationError(
                    f"Index {expected_path} is not canonical JSON: {exc}"
                ) from exc
            if raw_index != canonical_index:
                raise PackageValidationError(
                    f"Index {expected_path} is not canonical JSON"
                )

            shard_paths = [item["path"] for item in selected]
            record_count = sum(item["record_count"] for item in selected)
            date_start = (
                min(item["date_start"] for item in selected) if selected else None
            )
            date_end = (
                max(item["date_end"] for item in selected) if selected else None
            )
            expected_document_fields = {
                "index_version": 1,
                "kind": kind,
                "key": key,
                "record_count": record_count,
                "date_start": date_start,
                "date_end": date_end,
                "shards": shard_paths,
            }
            for field, expected_value in expected_document_fields.items():
                if document.get(field) != expected_value:
                    raise PackageValidationError(
                        f"Index {expected_path} field {field!r} mismatch"
                    )
            if not isinstance(document.get("metadata"), dict):
                raise PackageValidationError(
                    f"Index {expected_path} metadata must be an object"
                )

            expected_descriptor = {
                key_field: key,
                "path": expected_path,
                "shard_count": len(selected),
                "record_count": record_count,
                "date_start": date_start,
                "date_end": date_end,
                "bytes": descriptor_bytes,
                "sha256": descriptor_hash,
            }
            if descriptor != expected_descriptor:
                raise PackageValidationError(
                    f"Manifest descriptor for index {expected_path} is inconsistent"
                )

        if (
            seen_keys != sorted(set(seen_keys))
            or set(expected_by_key) - set(seen_keys)
        ):
            raise PackageValidationError(
                f"Manifest {collection_name} indexes are missing, duplicated, or unsorted"
            )


def validate_sharded_data_package(package_dir: Path) -> PackageValidationResult:
    """Read every shard and verify paths, hashes, sizes, dates, and totals."""

    root = Path(package_dir)
    if root.is_symlink() or not root.is_dir():
        raise UnsafePackagePathError("Package directory must be a real directory")
    marker = root / PACKAGE_MARKER_FILENAME
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.read_text(encoding="utf-8") != PACKAGE_MARKER_CONTENT
    ):
        raise PackageValidationError("Package marker is missing or invalid")

    manifest_path = root / MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageValidationError(f"Cannot read manifest v2: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PackageValidationError("Manifest root must be a JSON object")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise PackageValidationError(
            f"Unsupported manifest version: {manifest.get('manifest_version')!r}"
        )
    package_mode = manifest.get("package_mode")
    if package_mode not in {"year_partitioned", "canonical_facts"}:
        raise PackageValidationError(f"Unsupported package mode: {package_mode!r}")
    for metadata_key in ("schema", "release", "dataset"):
        if not isinstance(manifest.get(metadata_key), dict):
            raise PackageValidationError(
                f"Manifest {metadata_key!r} must be a JSON object"
            )

    dataset = manifest["dataset"]
    date_field = dataset.get("date_field")
    if not isinstance(date_field, str) or not date_field:
        raise PackageValidationError("Manifest dataset.date_field is required")
    if package_mode == "canonical_facts" and (
        dataset.get("country_field") != "country_code"
        or dataset.get("disease_field") != "disease_id"
    ):
        raise PackageValidationError(
            "Canonical facts dataset fields must be country_code and disease_id"
        )
    format_metadata = manifest.get("format")
    if not isinstance(format_metadata, dict):
        raise PackageValidationError("Manifest format must be a JSON object")
    if (
        format_metadata.get("media_type") != "application/x-ndjson"
        or format_metadata.get("compression") != "gzip"
        or format_metadata.get("encoding") != "utf-8"
    ):
        raise PackageValidationError("Manifest format contract is invalid")
    canonical_year_optional = False
    if package_mode == "canonical_facts":
        partitioning = format_metadata.get("partitioning")
        ordering = format_metadata.get("ordering")
        strategy = format_metadata.get("partition_strategy")
        if (
            partitioning
            == ["country_code", "disease_id", "overflow_year", "part"]
            and ordering
            == [
                "country_code",
                "disease_id",
                "overflow_year",
                "date",
                "canonical_json",
            ]
            and strategy == "pair_with_year_overflow"
        ):
            canonical_year_optional = True
        elif (
            partitioning == ["country_code", "disease_id", "year", "part"]
            and ordering
            == [
                "country_code",
                "disease_id",
                "year",
                "date",
                "canonical_json",
            ]
            and strategy in {None, "pair_year"}
        ):
            # Compatibility with the first local v2 candidate. It was never
            # preferred by clients, but bounded GitHub snapshots may retain it
            # during the migration window.
            canonical_year_optional = False
        else:
            raise PackageValidationError("Manifest format contract is invalid")
    elif (
        format_metadata.get("partitioning") != ["year", "part"]
        or format_metadata.get("ordering")
        != ["year", "date", "canonical_json"]
    ):
        raise PackageValidationError("Manifest format contract is invalid")
    max_uncompressed_bytes = _manifest_int(
        format_metadata,
        "max_uncompressed_bytes",
        minimum=1,
    )

    shards = manifest.get("shards")
    if not isinstance(shards, list):
        raise PackageValidationError("Manifest shards must be a list")
    totals = manifest.get("totals")
    if not isinstance(totals, dict):
        raise PackageValidationError("Manifest totals must be a JSON object")

    seen_paths: set[str] = set()
    previous_identity: tuple[Any, ...] | None = None
    expected_part_by_partition: dict[tuple[Any, ...], int] = defaultdict(lambda: 1)
    actual_records = 0
    actual_compressed = 0
    actual_uncompressed = 0
    starts: list[str] = []
    ends: list[str] = []
    for index, shard in enumerate(shards):
        if not isinstance(shard, dict):
            raise PackageValidationError(f"Shard entry {index} must be an object")
        raw_year = shard.get("year")
        year = (
            None
            if canonical_year_optional and raw_year is None
            else _manifest_int(shard, "year", minimum=1)
        )
        part = _manifest_int(shard, "part", minimum=1)
        if package_mode == "canonical_facts":
            country_code = shard.get("country_code")
            disease_id = shard.get("disease_id")
            if (
                not isinstance(country_code, str)
                or not country_code
                or not isinstance(disease_id, str)
                or not disease_id
            ):
                raise PackageValidationError(
                    "Canonical shard partition values must be non-empty strings"
                )
            partition_identity: tuple[Any, ...] = (
                country_code,
                disease_id,
                0 if year is None else year,
            )
        else:
            partition_identity = (year,)
        identity = (*partition_identity, part)
        if previous_identity is not None and identity <= previous_identity:
            raise PackageValidationError("Manifest shards are not stably ordered")
        previous_identity = identity
        if part != expected_part_by_partition[partition_identity]:
            raise PackageValidationError(
                f"Shard parts for partition {partition_identity!r} are not "
                f"contiguous at part {part}"
            )
        expected_part_by_partition[partition_identity] += 1
        relative_path = shard.get("path")
        if not isinstance(relative_path, str):
            raise PackageValidationError("Shard path must be a string")
        if relative_path in seen_paths:
            raise PackageValidationError(f"Duplicate shard path: {relative_path}")
        seen_paths.add(relative_path)

        count, compressed, uncompressed, date_start, date_end = _validate_shard(
            root,
            shard,
            package_mode=package_mode,
            canonical_year_optional=canonical_year_optional,
            date_field=date_field,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
        actual_records += count
        actual_compressed += compressed
        actual_uncompressed += uncompressed
        starts.append(date_start)
        ends.append(date_end)

    if package_mode == "canonical_facts":
        if not isinstance(manifest.get("source_catalog"), dict):
            raise PackageValidationError(
                "Canonical facts source_catalog must be a JSON object"
            )
        _validate_canonical_indexes(root, manifest.get("indexes"), shards)

    expected_totals = {
        "shard_count": len(shards),
        "record_count": actual_records,
        "date_start": min(starts) if starts else None,
        "date_end": max(ends) if ends else None,
        "compressed_bytes": actual_compressed,
        "uncompressed_bytes": actual_uncompressed,
    }
    for key, actual in expected_totals.items():
        if totals.get(key) != actual:
            raise PackageValidationError(
                f"Manifest total {key!r} mismatch: manifest={totals.get(key)!r}, "
                f"actual={actual!r}"
            )

    return PackageValidationResult(
        manifest=manifest,
        shard_count=len(shards),
        record_count=actual_records,
        compressed_bytes=actual_compressed,
        uncompressed_bytes=actual_uncompressed,
    )


__all__ = [
    "DEFAULT_MAX_UNCOMPRESSED_BYTES",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "PackageBuildError",
    "PackageValidationError",
    "PackageValidationResult",
    "ShardedDataPackageError",
    "UnsafePackagePathError",
    "build_canonical_facts_release",
    "build_sharded_data_package",
    "validate_sharded_data_package",
]
