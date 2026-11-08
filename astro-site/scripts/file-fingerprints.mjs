// Persistent local fingerprints. Metadata is checked on every lookup, including
// ctime/inode so same-size edits with restored mtime cannot reuse old content.
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { constants, copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const digest = (value, algorithm = 'sha256') => createHash(algorithm).update(value).digest('hex');
const normalize = path => resolve(path instanceof URL ? fileURLToPath(path) : path);
const signature = path => {
  const stat = statSync(path, { bigint: true });
  return [stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs].join(':');
};

export class FileFingerprints {
  constructor(cachePath, { read = readFileSync } = {}) {
    this.cachePath = cachePath;
    this.read = read;
    this.files = {};
    this.dirty = false;
    this.readCount = 0;
    try {
      const cache = JSON.parse(readFileSync(cachePath, 'utf8'));
      if (cache.schema === 1 && cache.files && typeof cache.files === 'object') this.files = cache.files;
    } catch { /* first run / damaged cache: read content */ }
  }

  hash(path, { gitAlgorithm } = {}) {
    const key = normalize(path);
    const before = signature(key);
    const entry = this.files[key];
    const gitField = gitAlgorithm === 'sha256' ? 'gitSha256' : 'gitSha1';
    if (entry?.stable && entry.stat === before && /^[a-f0-9]{64}$/.test(entry.sha256)
      && (!gitAlgorithm || entry[gitField])) return entry.sha256;
    const body = this.read(key);
    if (signature(key) !== before) throw new Error(`Build input changed while hashing: ${key}`);
    const sha256 = digest(body);
    const gitHash = gitAlgorithm
      ? createHash(gitAlgorithm).update(`blob ${body.length}\0`).update(body).digest('hex')
      : undefined;
    const timestamps = before.split(':').slice(-2).map(BigInt);
    const cutoff = BigInt(Date.now() - 1000) * 1000000n;
    this.files[key] = { stat: before, sha256, ...(gitHash ? { [gitField]: gitHash } : {}), stable: timestamps.every(value => value < cutoff) };
    this.dirty = true;
    this.readCount += 1;
    return sha256;
  }

  gitBlobHash(path, algorithm = 'sha1') {
    const key = normalize(path);
    const field = algorithm === 'sha256' ? 'gitSha256' : 'gitSha1';
    this.hash(key, { gitAlgorithm: algorithm });
    return this.files[key][field];
  }

  copy(source, target) {
    const before = signature(source);
    const sha256 = this.hash(source);
    // Keep unchanged cache entries and their trusted metadata intact. Reflinks
    // isolate later HTML rewrites; hard links would corrupt the page cache.
    if (existsSync(target) && this.hash(target) === sha256) {
      if (before !== signature(source)) throw new Error(`Build input changed while copying: ${source}`);
      return false;
    }
    copyFileSync(source, target, constants.COPYFILE_FICLONE);
    if (before !== signature(source)) throw new Error(`Build input changed while copying: ${source}`);
    // The new target's timestamps may still be in the filesystem's current
    // clock tick. Revalidate it once after that window before trusting metadata.
    this.files[normalize(target)] = { stat: signature(target), sha256, stable: false };
    this.dirty = true;
    return true;
  }

  save() {
    if (!this.dirty) return;
    mkdirSync(dirname(this.cachePath), { recursive: true });
    const temporary = `${this.cachePath}.${process.pid}.tmp`;
    writeFileSync(temporary, JSON.stringify({ schema: 1, files: this.files }));
    renameSync(temporary, this.cachePath);
    this.dirty = false;
  }
}

// Clean tracked sources already have a content ID in Git. Include staged edits
// from the index; hash unstaged/untracked/ignored inputs using the local cache.
// File discovery stays with the caller so ignored generated assets aren't lost.
export function gitSourceIdentities(root) {
  try {
    const git = args => execFileSync('git', ['--no-optional-locks', '-C', root, ...args], {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 16 * 1024 * 1024,
    });
    const changed = new Set(git(['diff', '--no-ext-diff', '--name-only', '-z']).split('\0'));
    const entries = new Map();
    entries.objectFormat = git(['rev-parse', '--show-object-format']).trim();
    for (const row of git(['ls-files', '--stage', '-z']).split('\0')) {
      const match = /^(100644|100755) ([a-f0-9]+) 0\t([\s\S]+)$/.exec(row);
      if (match && !changed.has(match[3])) entries.set(resolve(root, match[3]), `${match[1]}:${match[2]}`);
    }
    return entries;
  } catch { return new Map(); }
}

export function sourceIdentity(path, identities, fingerprints) {
  const key = normalize(path);
  // Git hashes symlink contents, while the compiler follows them.
  if (identities.has(key) && !lstatSync(key).isSymbolicLink()) return `git:${identities.get(key)}`;
  if (identities.objectFormat && !lstatSync(key).isSymbolicLink()) {
    const mode = (statSync(key).mode & 0o100) ? '100755' : '100644';
    return `git:${mode}:${fingerprints.gitBlobHash(key, identities.objectFormat)}`;
  }
  return `file:${fingerprints.hash(key)}`;
}

const project = resolve(import.meta.dirname, '../..');
export const fileFingerprints = new FileFingerprints(process.env.GIDS_BUILD_FINGERPRINT_CACHE || resolve(project, 'data/cache/build-file-fingerprints.json'));
export const fileHash = path => fileFingerprints.hash(path);
process.once('exit', () => fileFingerprints.save());
