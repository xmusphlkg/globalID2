from __future__ import annotations

import json
from pathlib import Path
import subprocess

from scripts import publish_raw_git_archive as archive
from scripts.verify_raw_git_archive import verify_raw_archive


def _git_log(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def test_local_archive_is_incremental_and_excludes_auth(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    repository = tmp_path / "archive"
    (source / "us").mkdir(parents=True)
    (source / "us" / "cases.json").write_text("{\"cases\": 7}\n", encoding="utf-8")
    (source / "us" / "_auth").mkdir()
    (source / "us" / "_auth" / "token.json").write_text("secret", encoding="utf-8")

    first = archive.publish_raw_archive(
        source,
        repository,
        chunk_bytes=8,
        commit_batch_bytes=16,
    )

    assert first.changed is True
    assert first.source_file_count == 1
    assert first.new_object_count == 1
    assert first.commit_count >= 2
    assert first.manifest_path is not None
    manifest = json.loads((repository / first.manifest_path).read_text(encoding="utf-8"))
    assert [item["path"] for item in manifest["files"]] == ["us/cases.json"]
    chunks = manifest["files"][0]["object"]["chunks"]
    assert len(chunks) > 1
    assert all(int(item["size"]) <= 8 for item in chunks)
    assert not any("_auth" in path.as_posix() for path in repository.rglob("*"))

    restored = tmp_path / "restored"
    verified = verify_raw_archive(
        repository,
        source_dir=source,
        restore_dir=restored,
    )
    assert verified.file_count == 1
    assert (restored / "us" / "cases.json").read_bytes() == (
        source / "us" / "cases.json"
    ).read_bytes()

    initial_log = _git_log(repository)
    second = archive.publish_raw_archive(
        source,
        repository,
        chunk_bytes=8,
        commit_batch_bytes=16,
    )

    assert second.changed is False
    assert second.new_object_count == 0
    assert second.commit_count == 0
    assert _git_log(repository) == initial_log


def test_changed_file_adds_one_object_and_preserves_old_manifest(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    repository = tmp_path / "archive"
    source.mkdir()
    payload = source / "payload.bin"
    payload.write_bytes(b"old payload")
    first = archive.publish_raw_archive(
        source,
        repository,
        chunk_bytes=1024,
        commit_batch_bytes=1024,
    )
    assert first.manifest_path is not None
    old_manifest = repository / first.manifest_path

    payload.write_bytes(b"new payload")
    second = archive.publish_raw_archive(
        source,
        repository,
        chunk_bytes=1024,
        commit_batch_bytes=1024,
    )

    assert second.changed is True
    assert second.new_object_count == 1
    assert old_manifest.is_file()
    assert second.manifest_path != first.manifest_path
    latest = json.loads((repository / "latest.json").read_text(encoding="utf-8"))
    assert latest["manifest"] == second.manifest_path


def test_interrupted_untracked_object_is_reused_and_committed(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    repository = tmp_path / "archive"
    source.mkdir()
    (source / "first.bin").write_bytes(b"first")
    archive.publish_raw_archive(
        source,
        repository,
        chunk_bytes=1024,
        commit_batch_bytes=1024,
    )

    duplicate_payload = b"compressed before an interrupted commit"
    (source / "second.bin").write_bytes(duplicate_payload)
    (source / "second-copy.bin").write_bytes(duplicate_payload)
    digest = archive._sha256_file(source / "second.bin")
    archive._compress_object(
        source / "second.bin",
        repository,
        digest,
        chunk_bytes=1024,
        zstd_level=1,
    )

    result = archive.publish_raw_archive(
        source,
        repository,
        chunk_bytes=1024,
        commit_batch_bytes=1024,
    )

    assert result.new_object_count == 1
    metadata = archive._object_metadata_path(repository, digest).relative_to(repository)
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", metadata.as_posix()],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    assert tracked.returncode == 0


def test_unborn_persistent_clone_resumes_without_recompression(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    repository = tmp_path / "archive"
    source.mkdir()
    source_file = source / "payload.bin"
    source_file.write_bytes(b"already compressed before the first commit")
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "checkout", "--orphan", archive.TARGET_BRANCH], cwd=repository, check=True)
    digest = archive._sha256_file(source_file)
    archive._compress_object(
        source_file,
        repository,
        digest,
        chunk_bytes=1024,
        zstd_level=1,
    )

    result = archive.publish_raw_archive(
        source,
        repository,
        chunk_bytes=1024,
        commit_batch_bytes=1024,
    )

    assert result.changed is True
    assert result.new_object_count == 1
    assert _git_log(repository)
