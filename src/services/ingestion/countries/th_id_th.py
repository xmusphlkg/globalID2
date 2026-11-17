"""Incremental GitHub blob capture, immutable offline archive replay."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import io
from itertools import groupby
import zipfile
import json
from pathlib import Path
import re

from src.data.crawlers.th_id_th import (
    API_URL, DEFAULT_SCOPE, EXCLUDED_STEMS, IDTHContractError, REPOSITORY,
    ThailandIDTHCrawler, git_blob_sha, parse_weekly_csv, raw_url, selected_files, source_file,
)
from src.data.processors.th_id_th import ThailandIDTHHistoryUpdater
from src.services.ingestion.artifacts import read_artifact

SNAPSHOT_ROOT = Path(__file__).resolve().parents[4] / "data/external/ID_TH"
CACHE_ROOT = Path(__file__).resolve().parents[4] / "data/cache/th_id_th/git_blobs"


def cached_blob(item, *, archive_root=None, cache_root=None):
    """Only content matching Git's blob identity may enter a new capture."""
    sha = item["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise IDTHContractError("Invalid upstream Git blob identity")
    for path in (Path(cache_root or CACHE_ROOT) / sha, Path(archive_root or SNAPSHOT_ROOT) / item["path"]):
        if path.is_file():
            payload = path.read_bytes()
            if len(payload) == item["size"] and git_blob_sha(payload) == sha:
                return payload
    return None


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=2020, end_year=None, archive_root=None, **options):
    ThailandIDTHHistoryUpdater.normalize_source(source)
    crawler = ThailandIDTHCrawler()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    try:
        response = await asyncio.to_thread(crawler.get, API_URL + "/commits/main")
        commit_payload = response.content
        head = json.loads(commit_payload)
        commit, tree_sha = head["sha"], head["commit"]["tree"]["sha"]
        if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (commit, tree_sha)):
            raise IDTHContractError("Invalid upstream Git commit/tree identity")
        await writer.write(commit_payload, source_url=API_URL + "/commits/main", kind="archive_json", metadata={"endpoint": "repository_commit", "retrieved_at": retrieved_at})
        tree_url = API_URL + f"/git/trees/{tree_sha}?recursive=1"
        response = await asyncio.to_thread(crawler.get, tree_url)
        tree_payload = response.content
        tree = json.loads(tree_payload)
        if tree["sha"] != tree_sha:
            raise IDTHContractError("Captured tree differs from pinned commit tree")
        await writer.write(tree_payload, source_url=tree_url, kind="archive_json", metadata={"endpoint": "repository_tree", "commit": commit})
        selected = [item for item in selected_files(tree) if int(start_year) <= source_file(item["path"])[0] <= int(end_year or datetime.now(timezone.utc).year)]
        downloaded, reused, excluded = 0, 0, []
        if not selected:
            raise IDTHContractError("Requested archive period has no source files")
        for directory, items in groupby(selected, key=lambda item: str(Path(item["path"]).parent)):
            bundled, members = io.BytesIO(), []
            with zipfile.ZipFile(bundled, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in items:
                    code = source_file(item["path"])[2]
                    if code in EXCLUDED_STEMS:
                        excluded.append({"path": item["path"], "reason": EXCLUDED_STEMS[code], "git_blob_sha": item["sha"]})
                        continue
                    payload = await asyncio.to_thread(cached_blob, item, archive_root=archive_root)
                    if payload is None:
                        response = await asyncio.to_thread(crawler.get, raw_url(commit, item["path"]))
                        payload = response.content
                        if len(payload) != item["size"] or git_blob_sha(payload) != item["sha"]:
                            raise IDTHContractError(f"Downloaded archive blob integrity mismatch: {item['path']}")
                        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
                        await asyncio.to_thread((CACHE_ROOT / item["sha"]).write_bytes, payload)
                        downloaded += 1
                    else:
                        reused += 1
                    member = {"path": item["path"], "git_blob_sha": item["sha"], "sha256": hashlib.sha256(payload).hexdigest(), "byte_size": len(payload)}
                    members.append(member)
                    # Stable ZIP metadata makes repeated captures deduplicate.
                    info = zipfile.ZipInfo(item["path"], date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, payload)
            if members:
                await writer.write(bundled.getvalue(), source_url=REPOSITORY + f"/tree/{commit}/{directory}", kind="idth_weekly_bundle", metadata={"endpoint": "weekly_national_bundle", "directory": directory, "commit": commit, "members": members, "retrieved_at": retrieved_at})
        await writer.write(json.dumps({"commit": commit, "tree_sha": tree_sha, "start_year": int(start_year), "end_year": int(end_year or datetime.now(timezone.utc).year), "downloaded_blobs": downloaded, "reused_blobs": reused, "excluded": excluded, "calendar_status": "unverified_historical_calendar", "basis": "unverified_archive_basis"}, ensure_ascii=False).encode(), source_url=REPOSITORY, kind="archive_json", metadata={"endpoint": "collection_plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    def one(endpoint):
        values = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == endpoint]
        if len(values) != 1:
            raise IDTHContractError(f"Exactly one captured {endpoint} artifact required")
        return json.loads(read_artifact(values[0]))
    head, tree, plan = one("repository_commit"), one("repository_tree"), one("collection_plan")
    if head["sha"] != plan["commit"] or head["commit"]["tree"]["sha"] != tree["sha"] or tree["sha"] != plan["tree_sha"]:
        raise IDTHContractError("Captured repository commit/tree provenance mismatch")
    expected = {item["path"]: item for item in selected_files(tree) if plan["start_year"] <= source_file(item["path"])[0] <= plan["end_year"] and source_file(item["path"])[2] not in EXCLUDED_STEMS}
    seen, rows = set(), []
    def parse_member(payload, *, path, commit, blob_sha, retrieved_at):
        if path in seen or path not in expected or commit != plan["commit"] or blob_sha != expected[path]["sha"]:
            raise IDTHContractError("Unexpected, duplicate, or incorrectly pinned weekly CSV")
        seen.add(path)
        rows.extend(parse_weekly_csv(payload, path=path, commit=commit, blob_sha=blob_sha, retrieved_at=retrieved_at))

    for artifact in artifacts:
        meta = artifact.metadata_
        if meta.get("endpoint") == "weekly_national_csv":
            # Replay already captured v1 artifacts; new captures use bundles.
            parse_member(read_artifact(artifact), path=meta["path"], commit=meta["commit"], blob_sha=meta["git_blob_sha"], retrieved_at=meta["retrieved_at"])
        elif meta.get("endpoint") == "weekly_national_bundle":
            try:
                with zipfile.ZipFile(io.BytesIO(read_artifact(artifact))) as archive:
                    if len(archive.namelist()) != len(set(archive.namelist())) or set(archive.namelist()) != {member["path"] for member in meta["members"]}:
                        raise IDTHContractError("Archive bundle member manifest mismatch")
                    for member in meta["members"]:
                        path = member["path"]
                        if path not in expected or archive.getinfo(path).file_size != int(member["byte_size"]) or int(member["byte_size"]) != expected[path]["size"]:
                            raise IDTHContractError("Archive bundle member size/path mismatch")
                        payload = archive.read(path)
                        if hashlib.sha256(payload).hexdigest() != member["sha256"]:
                            raise IDTHContractError("Archive bundle member SHA256 mismatch")
                        parse_member(payload, path=path, commit=meta["commit"], blob_sha=member["git_blob_sha"], retrieved_at=meta["retrieved_at"])
            except (zipfile.BadZipFile, KeyError) as exc:
                raise IDTHContractError("Archive bundle is corrupt or incomplete") from exc
    if seen != set(expected):
        raise IDTHContractError("Captured archive is incomplete; source files are missing")
    result = updater.prepare_rows(rows, source=source, **options)
    result.source_audit.update({**plan, "national_week_observations": len(rows), "source_disease_categories": len({row["SourceDiseaseCode"] for row in rows}), "storage_date_role": "source_year_anchor_only", "dates_inferred": False})
    result.script_logs.append(f"[archive] commit={plan['commit']} reused={plan['reused_blobs']} downloaded={plan['downloaded_blobs']}; {len(plan['excluded'])} non-disease worksheets excluded")
    return result
