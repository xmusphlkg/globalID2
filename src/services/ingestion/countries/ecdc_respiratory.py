"""Capture a live ECDC git tree and immutable blobs, then organize offline."""
import asyncio
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from src.data.crawlers.ecdc_respiratory import DEFAULT_SCOPE, DATASETS, TREE_URL, REPOSITORY, ECDCRespiratoryCrawler, parse_csv
from src.services.ingestion.artifacts import read_artifact

CACHE_DIRECTORY = Path(__file__).resolve().parents[4] / "data/raw/ecdc_respiratory_cache"
TREE_TTL_SECONDS = 15 * 60


def _atomic_write(path, payload):
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _cached_file(directory, name):
    """Read only self-contained cached bytes whose receipt still verifies."""
    try:
        receipt = json.loads((directory / (name + ".json")).read_bytes())
        payload = (directory / (name + ".bin")).read_bytes()
        captured = datetime.fromisoformat(receipt["retrieved_at"].replace("Z", "+00:00"))
        if captured.tzinfo is None or receipt["sha256"] != hashlib.sha256(payload).hexdigest() or receipt["byte_size"] != len(payload):
            return None
        return payload, receipt, captured
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_cached_file(directory, name, payload, retrieved_at):
    receipt = {"sha256": hashlib.sha256(payload).hexdigest(), "byte_size": len(payload), "retrieved_at": retrieved_at}
    _atomic_write(directory / (name + ".bin"), payload)
    _atomic_write(directory / (name + ".json"), json.dumps(receipt).encode())
    return receipt


def cached_capture(crawler, *, cache_directory=CACHE_DIRECTORY, now=None):
    """Share a fresh official tree and verified immutable blobs across processes.

    Every caller receives all source bytes to persist in its own artifact set.
    A cached source keeps its original retrieval timestamp. Expired discovery
    must refresh successfully; immutable blobs may be reused under a live tree.
    """
    directory = Path(cache_directory); directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".capture.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            raise ValueError("ECDC cache clock must be timezone-aware")
        cached = _cached_file(directory, "tree")
        fresh = False
        if cached:
            payload, receipt, captured = cached
            try:
                fresh = 0 <= (moment - captured).total_seconds() < TREE_TTL_SECONDS
                if fresh:
                    tree_paths(json.loads(payload))
            except (ValueError, KeyError, TypeError):
                fresh = False
        if not fresh:
            # Never fall back to an expired tree when this request fails.
            payload = crawler.get(TREE_URL).content
            tree_paths(json.loads(payload))
            receipt = _save_cached_file(directory, "tree", payload, moment.isoformat())
        tree = json.loads(payload)
        result = [(payload, TREE_URL, "ecdc_respiratory_tree", {"retrieved_at": receipt["retrieved_at"]})]
        for path, item in tree_paths(tree).items():
            sha = item["sha"]
            if not re.fullmatch(r"[a-f0-9]{40}", sha) or not isinstance(item.get("size"), int) or item["size"] < 0:
                raise ValueError("ECDC git tree contains an invalid blob identity/size")
            url = f"https://api.github.com/repos/{REPOSITORY}/git/blobs/{sha}"
            cached = _cached_file(directory, sha)
            valid = False
            if cached:
                raw, blob_receipt, _ = cached
                digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                valid = digest == sha and len(raw) == item["size"]
            if not valid:
                raw = blob_payload(json.loads(crawler.get(url).content), sha)
                if len(raw) != item["size"]:
                    raise ValueError("ECDC blob size contradicts current official tree")
                blob_receipt = _save_cached_file(directory, sha, raw, moment.isoformat())
            result.append((raw, url, "ecdc_respiratory_blob", {"path": path, "git_blob_sha1": sha,
                "git_tree_sha1": tree["sha"], "retrieved_at": blob_receipt["retrieved_at"]}))
        return result


def blob_payload(document, expected_sha):
    if document.get("encoding") != "base64" or document.get("sha") != expected_sha:
        raise ValueError("ECDC git blob response identity/encoding changed")
    payload = base64.b64decode(document["content"])
    git_hash = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
    if git_hash != expected_sha or len(payload) != document["size"]:
        raise ValueError("ECDC git blob hash/size mismatch")
    return payload


def tree_paths(document):
    if document.get("truncated") is not False or not document.get("sha"):
        raise ValueError("ECDC repository tree is incomplete")
    paths = {item["path"]: item for item in document["tree"] if item.get("type") == "blob"}
    required = set(DATASETS) | {"README.MD"}
    if not required <= paths.keys():
        raise ValueError("ECDC repository lost a reviewed dataset or README")
    return {p: paths[p] for p in sorted(required)}


async def collect(writer, *, country_code=None, source=DEFAULT_SCOPE, **options):
    if source != DEFAULT_SCOPE:
        raise ValueError("Unexpected ECDC respiratory source scope")
    crawler = ECDCRespiratoryCrawler()
    try:
        capture = await asyncio.to_thread(cached_capture, crawler)
        for payload, url, kind, metadata in capture:
            await writer.write(payload, source_url=url, kind=kind, metadata=metadata)
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    trees = [a for a in artifacts if a.kind == "ecdc_respiratory_tree"]
    if len(trees) != 1 or trees[0].source_url != TREE_URL:
        raise ValueError("Exactly one captured official ECDC tree is required")
    tree = json.loads(read_artifact(trees[0])); expected = tree_paths(tree)
    blobs = [a for a in artifacts if a.kind == "ecdc_respiratory_blob"]
    bypath = {a.metadata_.get("path"): a for a in blobs}
    if len(blobs) != len(bypath) or set(bypath) != set(expected):
        raise ValueError("ECDC artifact set is incomplete or duplicated")
    for path, artifact in bypath.items():
        payload = read_artifact(artifact)
        sha = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
        url = f"https://api.github.com/repos/{REPOSITORY}/git/blobs/{expected[path]['sha']}"
        if (sha != expected[path]["sha"] or artifact.metadata_.get("git_tree_sha1") != tree["sha"]
                or artifact.source_url != url or artifact.metadata_.get("git_blob_sha1") != sha
                or len(payload) != expected[path]["size"]):
            raise ValueError("ECDC blob does not belong to frozen discovery tree")
    as_of = datetime.fromisoformat(trees[0].metadata_["retrieved_at"].replace("Z", "+00:00")).date()
    rows, reports = [], []
    for path in DATASETS:
        artifact = bypath[path]
        parsed, audit = parse_csv(read_artifact(artifact), country_code=updater.country_code, dataset=path, as_of=as_of,
            provenance={"DownloadURL": artifact.source_url, "RawSHA256": artifact.sha256, "RetrievedAt": artifact.metadata_["retrieved_at"], "SourceDictionarySHA256": bypath["README.MD"].sha256, "SourceGitBlobSHA1": expected[path]["sha"], "SourceGitTreeSHA1": tree["sha"]})
        rows.extend(parsed); reports.append(audit)
    if not rows:
        raise ValueError("No closed-week ECDC respiratory data for requested country")
    audit = {"status": "parsed", "country_code": updater.country_code, "reports": reports, "observations": len(rows), "coverage_complete": True, "coverage_scope": "captured_current_laboratory_csv_files_only", "git_tree_sha1": tree["sha"], "source_readme": read_artifact(bypath["README.MD"]).decode(), "withdrawal_authority": False}
    return updater.prepare_rows(rows, source=source, source_audit=audit, **options)
