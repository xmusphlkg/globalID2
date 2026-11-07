"""In-place stage receipts: retries validate outputs without copying worktrees.

Receipts are private hints, not a substitute for publication gates. A changed
input, missing/modified output, or a different successful release invalidates
reuse. Small stat inventories avoid rereading multi-gigabyte downloads.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from .artifact_checkpoint import _fingerprint, rendering_configuration, source_fingerprint


def _stat(path: Path) -> list[int]:
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _inventory(paths: list[Path]) -> dict:
    result = {}
    cutoff = time.time_ns() - 1_000_000_000

    def record(path: Path) -> None:
        if path.is_symlink():
            raise ValueError(f"Symlink in stage output: {path}")
        before = _stat(path)
        # Capture a digest only inside the racy-stat window. Stable files
        # retain their existing identity without a body read.
        sha = _digest(path) if max(before[-2:]) >= cutoff else None
        if before != _stat(path):
            raise ValueError(f"Stage output changed during checkpoint: {path}")
        result[str(path)] = {"stat": before, "sha256": sha}

    for root in paths:
        if root.is_symlink():
            raise ValueError(f"Symlink in stage output: {root}")
        if root.is_file():
            record(root)
            continue
        if not root.is_dir():
            raise ValueError(f"Missing stage output: {root}")
        for parent, dirs, names in os.walk(root):
            dirs[:] = sorted(name for name in dirs if name != ".git")
            if any((Path(parent) / name).is_symlink() for name in dirs):
                raise ValueError(f"Symlink directory in stage output: {parent}")
            for name in sorted(names):
                if name == ".git":
                    continue
                record(Path(parent) / name)
    return result


def _generation_source(root: Path) -> str:
    digest = hashlib.sha256()
    for directory in ("src", "scripts", "configs"):
        for path in sorted((root / directory).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path == root / "configs/literature/weekly_ai_reviews.json":
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(bytes.fromhex(_digest(path)))
    requirements = root / "requirements.txt"
    if requirements.is_file():
        digest.update(bytes.fromhex(_digest(requirements)))
    return digest.hexdigest()


class StageReceipts:
    def __init__(self, root: Path, task_uuid: str, selection: dict, destinations: dict):
        if Path(task_uuid).name != task_uuid or task_uuid in {"", ".", ".."}:
            raise ValueError("Invalid task UUID")
        self.root = root
        self.task_uuid = task_uuid
        self.path = root / "exports/release-stages" / f"{task_uuid}.json"
        self.selection = selection
        self.destinations = destinations
        self.records = {}
        try:
            data = json.loads(self.path.read_text())
            if data.get("schema") == 1:
                self.records = data.get("stages", {})
        except (OSError, ValueError):
            pass

    def signature(self, stage: str, identity: dict) -> str:
        return _fingerprint({
            "selection": self.selection, "destinations": self.destinations,
            "source": _generation_source(self.root) if stage == "generated" else source_fingerprint(self.root),
            "rendering": rendering_configuration(self.root),
            "release": None if stage == "generated" else identity.get("release_id"),
            "published_baseline": self._baseline(identity),
        })

    def _baseline(self, identity: dict):
        marker = self.root / "exports/successful-site-release.json"
        path = marker if marker.exists() else self.root / "exports/verified-pages-snapshot/release.json"
        if not path.is_file():
            return None
        try:
            current = json.loads(path.read_text())
            if not isinstance(current, dict) or not current.get("release_id"):
                raise ValueError("Missing release identity")
            if path == marker and current.get("schema") != 1:
                raise ValueError("Unsupported successful release marker")
        except (OSError, ValueError) as error:
            raise RuntimeError(f"Cannot validate successful release baseline: {path}") from error
        ready = self.records.get("ready", {})
        if (current.get("release_id") == identity.get("release_id")
                and (path != marker or current.get("task_uuid") == self.task_uuid)
                and ready.get("identity", {}).get("release_id") == identity.get("release_id")
                and current.get("build_digest") == ready.get("identity", {}).get("build_digest")):
            # Deployment may succeed before the final database acknowledgement.
            # Only this task's own release can advance its baseline on retry.
            return ready.get("baseline")
        return _digest(path)

    def mark_published(self, identity: dict, *, data_paths: list[Path] | None = None) -> None:
        """Record Pages success before optional snapshot retention or DB ack.

        The small marker is independent of the large retained Pages tree. A
        later successful deployment therefore invalidates old candidates even
        when keeping that tree failed. Failure to persist this marker propagates
        so the task cannot report completion without a durable baseline.
        """
        if not identity.get("release_id"):
            raise ValueError("A successful release requires a release_id")
        path = self.root / "exports/successful-site-release.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**identity, "schema": 1, "task_uuid": self.task_uuid}
        data_error = None
        if data_paths is not None:
            roots = [path.resolve() for path in data_paths]
            try:
                files = _inventory(roots)
                if not files:
                    raise ValueError("Empty published data snapshot")
                payload["data_snapshot"] = {"roots": [str(path) for path in roots], "files": files}
            except (OSError, ValueError) as error:
                # The Pages deployment already happened. Persist its baseline
                # even if its local data cannot authorize a code-only release.
                data_error = error
        descriptor, temporary = tempfile.mkstemp(prefix=".successful-site-release-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        if data_error is not None:
            raise RuntimeError("Published baseline saved, but its data snapshot could not be recorded") from data_error

    def is_current_publication(self, identity: dict) -> bool:
        """A prior deploy checkpoint is reusable only while it is still current."""
        if not identity.get("release_id") or not identity.get("build_digest"):
            return False
        marker = self.root / "exports/successful-site-release.json"
        path = marker if marker.exists() else self.root / "exports/verified-pages-snapshot/release.json"
        try:
            current = json.loads(path.read_text())
            return bool(
                current.get("release_id") == identity["release_id"]
                and current.get("build_digest") == identity["build_digest"]
                and (path != marker or (
                    current.get("schema") == 1 and current.get("task_uuid") == self.task_uuid
                ))
            )
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def assert_published_data(self, paths: list[Path]) -> None:
        """Require the exact data snapshot of a successful Pages deployment.

        A retained HTML tree cannot establish this: a failed later generation
        may have replaced local JSON while leaving the deployed site untouched.
        """
        marker = self.root / "exports/successful-site-release.json"
        try:
            data = json.loads(marker.read_text())
            if data.get("schema") != 1 or not data.get("release_id"):
                raise ValueError("Missing successful release identity")
            snapshot = data["data_snapshot"]
            roots = [path.resolve() for path in paths]
            if sorted(snapshot["roots"]) != sorted(str(path) for path in roots):
                raise ValueError("Published data roots differ")
            current = _inventory(roots)
            expected = snapshot["files"]
            if not current or current.keys() != expected.keys():
                raise ValueError("Published data inventory differs")
            for name, entry in expected.items():
                if (current[name]["stat"] != entry["stat"]
                        or (entry.get("sha256") and _digest(Path(name)) != entry["sha256"])):
                    raise ValueError(f"Published data changed: {name}")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise RuntimeError(
                "Code-only release requires the unchanged data snapshot of a recorded "
                "successful publication; complete a normal data release first."
            ) from error

    def matches(self, stage: str, signature: str) -> dict | None:
        record = self.records.get(stage)
        if not isinstance(record, dict) or record.get("signature") != signature:
            return None
        try:
            # Enumerate names/stat only. This also rejects extra output files,
            # rather than silently deploying bytes absent from the receipt.
            current = _inventory([Path(p) for p in record["roots"]])
            expected = record["files"]
            if current.keys() != expected.keys():
                return None
            for name, entry in expected.items():
                if current[name]["stat"] != entry["stat"]:
                    return None
                if entry.get("sha256") and _digest(Path(name)) != entry["sha256"]:
                    return None
            return record
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, stage: str, signature: str, paths: list[Path], *, identity: dict) -> bool:
        if self.signature(stage, identity) != signature:
            return False
        try:
            files = _inventory(paths)
        except (OSError, ValueError):
            return False
        if not files:
            return False
        self.records[stage] = {
            "signature": signature, "roots": [str(p) for p in paths],
            "files": files, "identity": identity, "baseline": self._baseline(identity),
        }
        if stage == "generated":
            self.records.pop("ready", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps({"schema": 1, "stages": self.records}, separators=(",", ":")))
        temporary.replace(self.path)
        return True

    def discard(self):
        self.path.unlink(missing_ok=True)
