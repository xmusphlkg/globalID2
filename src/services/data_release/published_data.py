"""Independent, content-verified source data retained for template releases.

These copies are prepared before rendering and become authoritative only when
the successful Pages marker references them. Never hardlink mutable inputs.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .artifact_index import ArtifactIndex

LABELS = ("src-data", "public-site-data")
EDITORIAL = {"changelog.ts", "acknowledgements.ts"}


def clone_file(source, destination):
    """CoW on supporting filesystems, otherwise independent copied bytes."""
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Unsafe source data file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
        with source.open("rb") as incoming, destination.open("wb") as outgoing:
            fcntl.ioctl(outgoing.fileno(), 0x40049409, incoming.fileno())  # FICLONE
    except OSError:
        shutil.copyfile(source, destination)
    shutil.copystat(source, destination)
    return str(destination)


def files(directory: Path) -> dict[str, Path]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"Missing or unsafe data directory: {directory}")
    result = {}
    for parent, directories, names in os.walk(directory):
        for name in directories + names:
            path = Path(parent) / name
            if path.is_symlink():
                raise ValueError(f"Symlink in retained data: {path}")
        for name in names:
            path = Path(parent) / name
            if not path.is_file():
                raise ValueError(f"Unsupported data input: {path}")
            result[path.relative_to(directory).as_posix()] = path
    return result


def _index(root: Path, directory: Path) -> ArtifactIndex:
    return ArtifactIndex(directory, cache_dir=root / "data/cache/published-data-index")


def capture(root: Path, paths: list[Path]) -> dict:
    from .stage_receipts import _generated_data_files, _inventory, _same_inventory
    if len(paths) != 2:
        raise ValueError("Published data requires the build and public data roots")
    before = _generated_data_files(root, _inventory(paths))
    for name in EDITORIAL:
        before.pop(str(paths[0] / name), None)
    parent = root / "exports/published-site-data"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=parent) as temporary:
        candidate = Path(temporary) / "data"
        for label, source in zip(LABELS, paths):
            (candidate / label).mkdir(parents=True)
            for relative, path in files(source).items():
                if str(path) in before:
                    clone_file(path, candidate / label / relative)
        after = _generated_data_files(root, _inventory(paths))
        for name in EDITORIAL:
            after.pop(str(paths[0] / name), None)
        if not _same_inventory(before, after):
            raise RuntimeError("Release data changed while freezing build inputs")
        index = _index(root, candidate)
        entries = files(candidate)
        with ThreadPoolExecutor(max_workers=4) as pool:
            descriptions = list(pool.map(index.describe, entries.values()))
        manifest = {"schema": 1, "files": {
            name: {key: value[key] for key in ("sha256", "bytes")}
            for name, value in zip(entries, descriptions)}}
        body = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(body).hexdigest()
        target = parent / digest
        descriptor = {"schema": 1, "manifest_sha256": digest}
        if target.exists():
            verify(root, descriptor)
        else:
            (candidate / "manifest.json").write_bytes(body)
            candidate.rename(target)
        # Populate the ordinary stat-aware digest cache at the retained path.
        # The digest manifest remains authority; cache misses read the bytes.
        retained_index = _index(root, target)
        for name, value in zip(entries, descriptions):
            path = target / name
            from .artifact_index import _signature
            retained_index.entries[retained_index.key(path)] = {
                **value, "stat": _signature(path), "stable": False}
        retained_index.save()
    return descriptor


def verify(root: Path, descriptor: dict) -> list[Path]:
    digest = descriptor.get("manifest_sha256", "")
    if descriptor.get("schema") != 1 or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid published data descriptor")
    directory = root / "exports/published-site-data" / digest
    actual = files(directory)
    body = actual.pop("manifest.json").read_bytes()
    if hashlib.sha256(body).hexdigest() != digest:
        raise ValueError("Published data manifest digest differs")
    manifest = json.loads(body)
    expected = manifest["files"]
    if manifest.get("schema") != 1 or not expected or actual.keys() != expected.keys():
        raise ValueError("Published data inventory differs")
    if any(name.split("/", 1)[0] not in LABELS for name in expected):
        raise ValueError("Unexpected published data root")
    index = _index(root, directory)
    def check(item):
        name, path = item
        value = index.describe(path)
        if any(value[key] != expected[name][key] for key in ("sha256", "bytes")):
            raise ValueError(f"Published data changed: {name}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(check, actual.items()))
    index.save()
    return [directory / label for label in LABELS]


def verify_copy(root: Path, sources: list[Path], copies: list[Path]) -> None:
    """Check independent workspace bytes before research baseline composition."""
    expected, actual = {}, {}
    for label, source, target in zip(LABELS, sources, copies):
        for output, directory in ((expected, source), (actual, target)):
            for name, path in files(directory).items():
                if label == LABELS[0] and name in EDITORIAL:
                    continue
                output[f"{label}/{name}"] = path
    if not expected or expected.keys() != actual.keys():
        raise ValueError("Template workspace data inventory differs from publication")
    source_index = _index(root, sources[0].parent)
    copy_index = _index(root, copies[0].parents[1])
    def check(name):
        if source_index.describe(expected[name]) != copy_index.describe(actual[name]):
            raise ValueError(f"Template workspace data differs from publication: {name}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(check, expected))
    source_index.save()
    copy_index.save()
