#!/usr/bin/env python3
"""Build an auditable three-page overlay on a verified Pages snapshot.

This command never deploys. The parent release ID and source commit describe
the unchanged site baseline; overlay_id and source_tree_digest identify the
replacement pages. The build digest identifies the complete resulting site.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.data_release.pipeline import _built_site_digest
from src.services.data_release.shared_assets import verified_snapshot

PAGES = ("subscribe/index.html", "zh/subscribe/index.html", "fr/subscribe/index.html")


def source_digest(root: Path) -> str:
    """Fingerprint all Astro source and build configuration, including untracked files."""
    source = root / "astro-site"
    files = [p for p in (source / "src").rglob("*") if p.is_file()]
    files.extend(source / name for name in ("astro.config.mjs", "package.json", "package-lock.json"))
    digest = hashlib.sha256()
    for file in sorted(files):
        digest.update(file.relative_to(source).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def prepare(base: Path, build: Path, target: Path, live: dict, root: Path) -> dict:
    base, build, target = base.resolve(), build.resolve(), target.resolve()
    if target.exists() or target.is_relative_to(base) or base.is_relative_to(target):
        raise ValueError("overlay_target_must_be_new_and_separate")
    if any(path.is_symlink() for path in base.rglob("*")):
        raise ValueError("baseline_symlinks_not_allowed")
    if not verified_snapshot(base, live):
        raise ValueError("baseline_not_verified_against_live")
    baseline = json.loads((base / "release.json").read_text())
    for page in PAGES:
        html = (build / page).read_text(encoding="utf-8")
        if "pilot_mode" not in html or "consent" not in html:
            raise ValueError(f"page_missing_subscription_contract:{page}")
        for asset in re.findall(r'(?:src|href)="(/_astro/[^"?#]+)', html):
            if not (build / asset.lstrip("/")).is_file() and not (base / asset.lstrip("/")).is_file():
                raise ValueError(f"missing_page_asset:{asset}")

    shutil.copytree(base, target)
    changed: dict[str, str] = {}
    for page in PAGES:
        shutil.copy2(build / page, target / page)
        changed[page] = hashlib.sha256((target / page).read_bytes()).hexdigest()
    for asset in sorted((build / "_astro").rglob("*")):
        if not asset.is_file():
            continue
        relative = asset.relative_to(build)
        destination = target / relative
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(asset.read_bytes()).digest():
                raise ValueError(f"asset_url_collision:{relative}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, destination)
        changed[relative.as_posix()] = hashlib.sha256(asset.read_bytes()).hexdigest()

    new_digest = _built_site_digest(target)
    when = datetime.now(timezone.utc).isoformat()
    overlay_id = f"subscription-{new_digest[:16]}"
    identity = dict(baseline)
    identity["build_digest"] = new_digest
    identity["overlay"] = {
        "overlay_id": overlay_id,
        "base_release_id": baseline["release_id"],
        "base_build_digest": baseline["build_digest"],
        "created_at": when,
        "kind": "subscription_pages",
        "source_checkout_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "source_checkout_dirty": True,
        "source_tree_sha256": source_digest(root),
        "files_sha256": changed,
    }
    (target / "release.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2) + "\n")
    if not verified_snapshot(target, identity) or not verified_snapshot(base, live):
        raise ValueError("overlay_or_baseline_checksum_mismatch")
    return identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "build", "target", "live-manifest"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.base, args.build, args.target, json.loads(args.live_manifest.read_text()), ROOT)
    print(json.dumps({"release_id": result["release_id"], "build_digest": result["build_digest"],
                      "overlay": result["overlay"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
