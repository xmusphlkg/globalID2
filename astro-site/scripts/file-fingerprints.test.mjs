import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { chmodSync, mkdtempSync, readFileSync, symlinkSync, rmSync, statSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { FileFingerprints, gitSourceIdentities, sourceIdentity } from './file-fingerprints.mjs';

const leaveTimestampWindow = () => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1010);

function fixture(run) {
  const root = mkdtempSync(join(tmpdir(), 'gids-fingerprints-'));
  try { run(root); } finally { rmSync(root, { recursive: true, force: true }); }
}

test('persistent cache skips unchanged bodies and catches same-size edits', () => fixture(root => {
  const file = join(root, 'page.html');
  const cache = join(root, 'cache.json');
  writeFileSync(file, 'first');
  leaveTimestampWindow();
  const first = new FileFingerprints(cache);
  const original = first.hash(file);
  first.save();
  let reads = 0;
  const next = new FileFingerprints(cache, { read: path => { reads++; return readFileSync(path); } });
  assert.equal(next.hash(file), original);
  assert.equal(reads, 0);
  const before = statSync(file);
  writeFileSync(file, 'other');
  utimesSync(file, before.atime, before.mtime);
  assert.notEqual(next.hash(file), original);
  assert.equal(reads, 1);
}));

test('copies revalidate once after the timestamp window then reuse output', () => fixture(root => {
  const source = join(root, 'source');
  const target = join(root, 'target');
  writeFileSync(source, 'page');
  const index = new FileFingerprints(join(root, 'cache'));
  const digest = index.hash(source);
  index.copy(source, target);
  leaveTimestampWindow();
  assert.equal(index.hash(target), digest);
  index.read = () => { throw new Error('unexpected body read'); };
  assert.equal(index.hash(target), digest);
}));

test('Git identities include staged edits and fall back for unstaged/untracked files', () => fixture(root => {
  const git = args => execFileSync('git', ['-C', root, ...args], { stdio: 'pipe' });
  git(['init']);
  const file = join(root, 'template.astro');
  writeFileSync(file, 'first');
  git(['add', '.']);
  const cache = new FileFingerprints(join(root, 'cache'));
  const identity = sourceIdentity(file, gitSourceIdentities(root), cache);
  assert.match(identity, /^git:/);
  assert.equal(cache.readCount, 0);
  writeFileSync(file, 'dirty');
  assert.notEqual(sourceIdentity(file, gitSourceIdentities(root), cache), identity);
  assert.equal(cache.readCount, 1);
  const dirty = sourceIdentity(file, gitSourceIdentities(root), cache);
  git(['add', 'template.astro']);
  assert.equal(sourceIdentity(file, gitSourceIdentities(root), cache), dirty);
  git(['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture']);
  assert.equal(sourceIdentity(file, gitSourceIdentities(root), cache), dirty);
  writeFileSync(join(root, 'new.astro'), 'new');
  assert.match(sourceIdentity(join(root, 'new.astro'), gitSourceIdentities(root), cache), /^git:/);
  rmSync(file);
  assert.equal(gitSourceIdentities(root).has(file), false);
}));

test('damaged cache and missing Git use content hashing', () => fixture(root => {
  const cache = join(root, 'cache');
  writeFileSync(cache, 'broken json');
  const file = join(root, 'template');
  writeFileSync(file, 'page');
  const index = new FileFingerprints(cache);
  assert.match(sourceIdentity(file, gitSourceIdentities(root), index), /^file:/);
  assert.equal(index.readCount, 1);
}));

for (const algorithm of ['sha1', 'sha256']) {
  test(`Git ${algorithm} identities stay stable across dirty/staged/committed states and track modes`, () => fixture(root => {
    const git = args => execFileSync('git', ['-C', root, ...args], { stdio: 'pipe' });
    git(['init', `--object-format=${algorithm}`]);
    const file = join(root, 'template.astro');
    writeFileSync(file, 'template');
    const cache = new FileFingerprints(join(root, 'cache'));
    const untracked = sourceIdentity(file, gitSourceIdentities(root), cache);
    git(['add', 'template.astro']);
    assert.equal(sourceIdentity(file, gitSourceIdentities(root), cache), untracked);
    chmodSync(file, 0o755);
    const executable = sourceIdentity(file, gitSourceIdentities(root), cache);
    assert.notEqual(executable, untracked);
    git(['add', 'template.astro']);
    assert.equal(sourceIdentity(file, gitSourceIdentities(root), cache), executable);
    symlinkSync('template.astro', join(root, 'link.astro'));
    git(['add', 'link.astro']);
    assert.match(sourceIdentity(join(root, 'link.astro'), gitSourceIdentities(root), cache), /^file:/);
    const linked = sourceIdentity(join(root, 'link.astro'), gitSourceIdentities(root), cache);
    writeFileSync(file, 'changed');
    assert.notEqual(sourceIdentity(join(root, 'link.astro'), gitSourceIdentities(root), cache), linked);
  }));
}
