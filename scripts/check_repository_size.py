#!/usr/bin/env python3
"""Reject newly tracked backups, database dumps, and oversized Git blobs.

The allowlist records existing debt by path, object id, and size.  It therefore
cannot be used to replace an old backup with a new object at the same path.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Iterable


DEFAULT_MAX_BLOB_BYTES = 5 * 1024 * 1024
FORBIDDEN_PREFIXES = ("backups/", "data/backups/")
FORBIDDEN_SUFFIXES = (".dump", ".db", ".sqlite", ".sqlite3")
BACKUP_SQL_PATTERN = re.compile(r"(?:^|/)[^/]*backup[^/]*\.sql$", re.IGNORECASE)


@dataclass(frozen=True)
class TrackedBlob:
    path: str
    oid: str
    size: int


@dataclass(frozen=True)
class Violation:
    blob: TrackedBlob
    reasons: tuple[str, ...]


def _run_git(repo: Path, arguments: list[str], *, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        input=input_data,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"git {' '.join(arguments)} failed")
    return result.stdout


def tracked_blobs(repo: Path) -> list[TrackedBlob]:
    """Return regular files and symlinks from the current Git index."""
    records = _run_git(repo, ["ls-files", "--stage", "-z"]).split(b"\0")
    indexed: list[tuple[str, str]] = []
    for record in records:
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, raw_oid, stage = metadata.split()
        if stage != b"0" or mode == b"160000":
            continue
        indexed.append(
            (
                raw_path.decode("utf-8", errors="surrogateescape"),
                raw_oid.decode("ascii"),
            )
        )

    if not indexed:
        return []
    unique_oids = list(dict.fromkeys(oid for _, oid in indexed))
    query = "".join(f"{oid}\n" for oid in unique_oids).encode("ascii")
    response = _run_git(
        repo,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input_data=query,
    )
    sizes: dict[str, int] = {}
    for line in response.decode("ascii").splitlines():
        oid, object_type, raw_size = line.split()
        if object_type != "blob":
            continue
        sizes[oid] = int(raw_size)
    return [TrackedBlob(path, oid, sizes[oid]) for path, oid in indexed]


def revision_blobs(repo: Path, revision_range: str) -> list[TrackedBlob]:
    """Return blobs introduced by commits in a revision range, including deleted files."""
    raw_objects = _run_git(repo, ["rev-list", "--objects", revision_range])
    candidates: list[tuple[str, str]] = []
    for raw_line in raw_objects.splitlines():
        raw_oid, separator, raw_path = raw_line.partition(b" ")
        if not separator:
            continue
        candidates.append(
            (
                raw_path.decode("utf-8", errors="surrogateescape"),
                raw_oid.decode("ascii"),
            )
        )
    if not candidates:
        return []
    query = "".join(f"{oid}\n" for _, oid in candidates).encode("ascii")
    response = _run_git(
        repo,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input_data=query,
    )
    metadata = [line.split() for line in response.decode("ascii").splitlines()]
    return [
        TrackedBlob(path, oid, int(parts[2]))
        for (path, oid), parts in zip(candidates, metadata, strict=True)
        if parts[1] == "blob"
    ]


def load_allowlist(path: Path | None) -> set[tuple[str, str, int]]:
    if path is None:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("allowlist"), list):
        raise ValueError("repository-size baseline must have version 1 and an allowlist")
    entries: set[tuple[str, str, int]] = set()
    for item in payload["allowlist"]:
        entry = (str(item["path"]), str(item["oid"]), int(item["size"]))
        if entry in entries:
            raise ValueError(f"duplicate repository-size baseline entry: {entry[0]}")
        entries.add(entry)
    return entries


def violation_reasons(blob: TrackedBlob, max_blob_bytes: int) -> tuple[str, ...]:
    normalized = PurePosixPath(blob.path).as_posix().lstrip("./")
    lower_path = normalized.lower()
    reasons: list[str] = []
    if lower_path.startswith(FORBIDDEN_PREFIXES):
        reasons.append("backup directory")
    if lower_path.endswith(FORBIDDEN_SUFFIXES) or BACKUP_SQL_PATTERN.search(normalized):
        reasons.append("database/backup artifact")
    if blob.size > max_blob_bytes:
        reasons.append(f"blob exceeds {max_blob_bytes} bytes")
    return tuple(reasons)


def find_violations(
    blobs: Iterable[TrackedBlob],
    *,
    allowlist: set[tuple[str, str, int]],
    max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES,
) -> list[Violation]:
    violations: list[Violation] = []
    for blob in blobs:
        reasons = violation_reasons(blob, max_blob_bytes)
        if reasons and (blob.path, blob.oid, blob.size) not in allowlist:
            violations.append(Violation(blob, reasons))
    return violations


def _default_repo() -> Path:
    output = _run_git(Path.cwd(), ["rev-parse", "--show-toplevel"])
    return Path(output.decode("utf-8", errors="surrogateescape").strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, help="Git repository to inspect")
    parser.add_argument("--baseline", type=Path, help="Exact legacy-object allowlist")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BLOB_BYTES)
    parser.add_argument(
        "--revision-range",
        help="also inspect every blob introduced in this Git revision range",
    )
    args = parser.parse_args(argv)

    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    repo = (args.repo or _default_repo()).resolve()
    baseline = args.baseline
    if baseline is None:
        candidate = repo / "configs/repository_size_baseline.json"
        baseline = candidate if candidate.exists() else None
    try:
        indexed = tracked_blobs(repo)
        allowlist = load_allowlist(baseline)
        violations = find_violations(
            indexed, allowlist=allowlist, max_blob_bytes=args.max_bytes
        )
        introduced: list[TrackedBlob] = []
        if args.revision_range:
            introduced = revision_blobs(repo, args.revision_range)
            # A baseline change in the same PR must not exempt a newly introduced
            # object. Existing debt is only relevant to the current index scan.
            violations.extend(
                find_violations(
                    introduced, allowlist=set(), max_blob_bytes=args.max_bytes
                )
            )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Repository size audit failed: {exc}", file=sys.stderr)
        return 2

    if violations:
        print("New forbidden or oversized Git objects:", file=sys.stderr)
        for violation in violations:
            blob = violation.blob
            print(
                f"- {blob.path} ({blob.size} bytes, {blob.oid}): "
                f"{', '.join(violation.reasons)}",
                file=sys.stderr,
            )
        print(
            "Move generated data/backups to external storage; do not extend the legacy baseline.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Repository size OK: {len(indexed)} indexed files and "
        f"{len(introduced)} introduced objects checked; "
        f"limit={args.max_bytes} bytes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
