#!/usr/bin/env python3
"""Upload completed migration cells while generation continues, then publish v5."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.publish_download_repo import (
    configure_github_ssh_transport, ensure_commit_identity, run_git, run_git_with_retry,
)


def completed_assets(candidate: Path) -> dict[str, dict]:
    assets = {}
    for checkpoint in sorted((candidate / '.migration').glob('*.json')):
        if checkpoint.stem in {'source', 'verified'}:
            continue
        entry = json.loads(checkpoint.read_text())
        for part in entry['parts']:
            for meta in part['files'].values():
                assets[meta['relative_path']] = meta
    return assets


def stream(source: Path, candidate: Path, *, batch_bytes=512 * 1024 * 1024, publish=False):
    source, candidate = source.resolve(), candidate.resolve()
    identity = json.loads((candidate / '.migration/source.json').read_text())
    staging = f"globalid-upload/cells-{identity['source_sha256'][:24]}"
    scratch = candidate.parent / f'.{candidate.name}.upload'
    scratch.mkdir(exist_ok=True)
    with (scratch / 'upload.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        repo = run_git(['remote', 'get-url', 'origin'], source)
        configure_github_ssh_transport(repo)
        ensure_commit_identity(source)
        branch = run_git(['symbolic-ref', '--short', 'HEAD'], source)
        refs = run_git_with_retry(['ls-remote', '--heads', 'origin', branch, staging], source)
        remote = {line.split()[1]: line.split()[0] for line in refs.splitlines()}
        parent = remote.get(f'refs/heads/{staging}') or remote.get(f'refs/heads/{branch}')
        if not parent:
            raise RuntimeError('Streaming migration requires an existing published baseline')
        run_git_with_retry(['fetch', 'origin', parent], source)
        env = {**os.environ, 'GIT_DIR': run_git(['rev-parse', '--absolute-git-dir'], source),
               'GIT_WORK_TREE': str(scratch), 'GIT_INDEX_FILE': str(scratch / 'index')}

        def index_git(args, payload=None):
            return subprocess.run(['git', *args], cwd=scratch, env=env, input=payload,
                text=True, capture_output=True, check=True).stdout.strip()

        index_git(['read-tree', parent])
        uploaded = set(run_git(['ls-tree', '-r', '--name-only', parent, '--', 'cells'], source).splitlines())
        batches = 0
        while True:
            if json.loads((candidate / '.migration/source.json').read_text()) != identity:
                raise RuntimeError('Migration identity changed during streaming upload')
            available = completed_assets(candidate)
            pending = [(path, meta) for path, meta in available.items() if path not in uploaded]
            batch, size = [], 0
            for path, meta in pending:
                if batch and size + meta['bytes'] > batch_bytes:
                    break
                if meta['bytes'] > batch_bytes:
                    raise ValueError(f'Asset exceeds upload batch size: {path}')
                batch.append((path, meta))
                size += meta['bytes']
            if batch:
                for relative, meta in batch:
                    # Completed country files are immutable. Hard links survive
                    # the migration's final directory rename without a data copy.
                    path = scratch / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        try:
                            os.link(candidate / relative, path)
                        except FileNotFoundError:
                            os.link(source / relative, path)
                    with path.open('rb') as handle:
                        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
                    if path.stat().st_size != meta['bytes'] or digest != meta['sha256']:
                        raise ValueError(f'Completed cell checksum mismatch: {relative}')
                index_git(['add', '--pathspec-from-file=-', '--pathspec-file-nul'],
                    ''.join(path + '\0' for path, _ in batch))
                tree = index_git(['write-tree'])
                commit = run_git(['commit-tree', tree, '-p', parent, '-m', 'Stage generated download cells'], source)
                batches += 1
                print(f'Uploading batch {batches}: {len(batch)} files, {size / 1024**2:.1f} MiB', flush=True)
                run_git_with_retry(['-c', 'pack.window=0', 'push', 'origin',
                    f'{commit}:refs/heads/{staging}'], source)
                parent = commit
                uploaded.update(path for path, _ in batch)
                print(f'Uploaded {len(uploaded)} files to {staging}', flush=True)
                continue
            verified = candidate / '.migration/verified.json'
            live = json.loads((source / 'manifest.json').read_text())
            if verified.exists() and live.get('schema_version') == 5:
                receipt = json.loads(verified.read_text())
                with (source / 'manifest.json').open('rb') as handle:
                    digest = hashlib.file_digest(handle, 'sha256').hexdigest()
                if digest != receipt['manifest_sha256']:
                    raise RuntimeError('Activated manifest differs from the verified migration')
                break
            time.sleep(2)
        print(f'All {len(uploaded)} generated files uploaded. Preload branch: {staging}', flush=True)
        if publish:
            subprocess.run([sys.executable, str(ROOT / 'scripts/publish_download_repo.py'),
                '--source-dir', str(source), '--repo-url', repo, '--branch', branch,
                '--push', '--verify-remote', '--commit-message', 'chore(data): migrate downloads to country disease window cells'],
                cwd=ROOT, env={**os.environ, 'GIDS_DOWNLOAD_PRELOAD_BRANCH': staging}, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'exports/site-downloads')
    parser.add_argument('--candidate', type=Path, default=ROOT / 'exports/site-downloads-cells')
    parser.add_argument('--batch-mib', type=int, default=512)
    parser.add_argument('--publish', action='store_true', help='Publish the verified final manifest and remove the preload branch')
    args = parser.parse_args()
    if args.batch_mib < 100 or args.batch_mib > 1024:
        parser.error('--batch-mib must be between 100 and 1024')
    stream(args.source, args.candidate, batch_bytes=args.batch_mib * 1024**2, publish=args.publish)


if __name__ == '__main__':
    main()
