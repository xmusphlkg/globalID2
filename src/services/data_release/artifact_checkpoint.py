"""Retain a validated release candidate across a failed external publication.

Only retries of the same task, source tree and destination configuration may
reuse a candidate. Its frozen projection acknowledges exactly the facts that
were rendered, even when new collections arrive between attempts.
"""

from __future__ import annotations

import hashlib
import json
import errno
import os
import shutil
from pathlib import Path

from .release_binding import PAPERS_FIELDS, papers_binding_matches


def link_immutable_or_copy(source, destination):
    """Share immutable partition bytes when possible, preserving portability."""
    try:
        os.link(source, destination)
        return destination
    except OSError as exc:
        if exc.errno not in {errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.EMLINK}:
            raise
        return shutil.copy2(source, destination)


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    paths = []
    for name in ("src", "scripts", "configs", "shared", "astro-site/src", "astro-site/scripts", "astro-site/public",
                 "cloudflare/surveillance-data/src", "cloudflare/surveillance-data/scripts", "cloudflare/papers-json/src"):
        directory = root / name
        paths.extend(
            path for path in directory.rglob("*") if path.is_file()
            and "__pycache__" not in path.parts
            and not (path.is_relative_to(root / "astro-site/src/data") and path.suffix == ".json")
            and not path.is_relative_to(root / "astro-site/public/site-data")
            # This is runtime research data, already frozen into the candidate,
            # and may advance between retries just like new database facts.
            and path != root / "configs/literature/weekly_ai_reviews.json"
            and path != root / "astro-site/src/data/changelog.ts"
        )
    for name in ("requirements.txt", "astro-site/package.json", "astro-site/package-lock.json", "astro-site/astro.config.mjs",
                 "cloudflare/surveillance-data/package.json", "cloudflare/surveillance-data/package-lock.json",
                 "cloudflare/surveillance-data/wrangler.jsonc"):
        path = root / name
        if path.is_file():
            paths.append(path)
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def rendering_configuration(root: Path) -> str:
    settings_path = root / "data/system-settings.json"
    site = json.loads(settings_path.read_text()).get("site", {}) if settings_path.is_file() else {}
    environment = {
        key: value for key, value in os.environ.items()
        if key.startswith("PUBLIC_") or key in {
            "GLOBALID_SITE_URL", "SITE_URL", "SITUATION_SHADOW_PREVIEW",
            "SUBSCRIPTIONS__PUBLIC_BASE_URL", "NODE_ENV",
            "GIDS_PAGES_DATA_WORKER", "GIDS_RUNTIME_DATA_READY", "GIDS_RUNTIME_SITUATION_READY",
            "GIDS_RESEARCH_PUBLICATION_ENABLED",
            "GIDS_EXTERNAL_SURVEILLANCE_DATA",
        }
    }
    return _fingerprint({"site": site, "environment": environment})


class ArtifactCheckpoint:
    def __init__(self, root: Path, task_uuid: str, paths: list[Path], input_data: dict):
        if Path(task_uuid).name != task_uuid or task_uuid in {"", ".", ".."}:
            raise ValueError("Invalid release task UUID")
        self.root = root
        self.directory = root / "exports" / "release-candidates" / task_uuid
        self.paths = paths
        self.selection = _fingerprint(input_data)
        self.ready = False

    def signature(self, destinations: dict) -> dict:
        baseline = self.paths[2] / "release.json"
        return {
            "source": source_fingerprint(self.root),
            "rendering": rendering_configuration(self.root),
            "destinations": _fingerprint(destinations),
            "selection": self.selection,
            # A later successful release makes an older frozen candidate
            # obsolete. Retrying that task must regenerate current facts.
            "base_release": hashlib.sha256(baseline.read_bytes()).hexdigest() if baseline.is_file() else None,
        }

    def discard(self) -> None:
        if self.directory.exists():
            shutil.rmtree(self.directory)
        self.ready = False

    def restore(self, signature: dict, identity: dict, digest_site) -> dict | None:
        try:
            candidate = json.loads((self.directory / "ready.json").read_text())
            if (candidate.get("schema") != 1 or candidate.get("signature") != signature
                    or candidate["identity"].get("release_id") != identity.get("release_id")
                    or not candidate.get("retained")):
                self.discard()
                return None
            present = candidate["present"]
            if (not isinstance(present, list) or any(type(index) is not int for index in present)
                    or not {0, 2}.issubset(present)
                    or not set(present).issubset(range(len(self.paths)))):
                raise ValueError("Invalid release candidate artifact map")
            # A candidate must include every artifact tree that existed at the
            # local gate, plus the prepared projection needed for a frozen commit.
            for index in candidate["present"]:
                if not (self.directory / str(index)).is_dir():
                    raise ValueError("Incomplete release candidate")
            projection = self.directory / "0" / "_release-projection" / "manifest.json"
            if not projection.is_file():
                raise ValueError("Release candidate lacks its frozen projection")
            expected_digest = candidate["identity"].get("build_digest")
            if expected_digest and digest_site(self.directory / "2") != expected_digest:
                raise ValueError("Release candidate build checksum mismatch")
            # release.json is deliberately excluded from the build checksum.
            # A bound candidate therefore needs an independent identity check.
            if any(key in candidate["identity"] for key in PAPERS_FIELDS):
                manifest = json.loads((self.directory / "2" / "release.json").read_text())
                if (manifest.get("release_id") != candidate["identity"].get("release_id")
                        or manifest.get("build_digest") != expected_digest
                        or not papers_binding_matches(manifest, candidate["identity"])):
                    raise ValueError("Release candidate Papers binding mismatch")
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            self.discard()
            return None
        for index, path in enumerate(self.paths):
            if path.exists():
                shutil.rmtree(path)
            if index in candidate["present"]:
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(self.directory / str(index)), str(path))
        candidate["retained"] = False
        self._write(candidate)
        self.ready = True
        return candidate["identity"]

    def save_ready(self, signature: dict, identity: dict) -> None:
        # Legacy generators cannot safely resume: their final DB commit reads
        # live observations instead of the exact rendered curves.
        if not (self.paths[0] / "_release-projection" / "manifest.json").is_file():
            return
        if (source_fingerprint(self.root) != signature["source"]
                or rendering_configuration(self.root) != signature["rendering"]):
            return
        self._write({
            "schema": 1, "signature": signature, "identity": identity,
            "present": [index for index, path in enumerate(self.paths) if path.is_dir()],
            "retained": False,
        })
        self.ready = True

    def retain(self) -> bool:
        if not self.ready:
            return False
        candidate = json.loads((self.directory / "ready.json").read_text())
        for index in candidate["present"]:
            shutil.move(str(self.paths[index]), str(self.directory / str(index)))
        candidate["retained"] = True
        self._write(candidate)
        return True

    def _write(self, payload: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / "ready.tmp"
        temporary.write_text(json.dumps(payload, sort_keys=True))
        temporary.replace(self.directory / "ready.json")
