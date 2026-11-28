import { existsSync, readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { posix, relative, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

const projectRoot = resolve(import.meta.dirname, '..');

// Explicit allowlist of *.test.* paths (relative to astro-site/, POSIX slashes)
// excluded from offline deterministic unit test runs (e.g. tests requiring live
// network, Playwright browsers, or a pre-built dist/). Browser e2e specs live
// under e2e/*.spec.ts and are run separately via `npm run test:e2e`.
export const EXCLUDED_UNIT_TESTS = new Set([]);

const TEST_FILE_PATTERN = /\.test\.(?:ts|mts|cts|js|mjs|cjs)$/;
const IGNORED_DIRS = new Set(['node_modules', 'dist', '.astro']);

export function discoverUnitTests({
  root = projectRoot,
  roots = ['scripts', 'src'],
  exclude = EXCLUDED_UNIT_TESTS,
} = {}) {
  const discovered = [];
  const walk = (directory) => {
    if (!existsSync(directory)) return;
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const fullPath = resolve(directory, entry.name);
      if (entry.isDirectory()) {
        if (!IGNORED_DIRS.has(entry.name)) walk(fullPath);
        continue;
      }
      if (!entry.isFile() || !TEST_FILE_PATTERN.test(entry.name)) continue;
      const rel = relative(root, fullPath).split(sep).join(posix.sep);
      if (!exclude.has(rel)) discovered.push(rel);
    }
  };
  for (const subdir of roots) walk(resolve(root, subdir));
  return discovered.sort();
}

function ensureFlagsSynced(root) {
  const sampleFlag = resolve(root, 'public', 'flags', 'us.svg');
  if (existsSync(sampleFlag)) return;
  const syncScript = resolve(root, 'scripts', 'sync-flags.mjs');
  if (!existsSync(syncScript)) return;
  const synced = spawnSync(process.execPath, [syncScript], { cwd: root, stdio: 'inherit' });
  if (synced.status !== 0) process.exit(synced.status ?? 1);
}

function runCli() {
  const cliArgs = process.argv.slice(2);
  const listOnly = cliArgs.includes('--list');
  const forwardedArgs = cliArgs.filter((arg) => arg !== '--list');
  const testFiles = discoverUnitTests({ root: projectRoot });

  if (listOnly) {
    for (const file of testFiles) process.stdout.write(`${file}\n`);
    process.stdout.write(`# ${testFiles.length} unit test files\n`);
    return;
  }

  if (testFiles.length === 0) {
    process.stderr.write('[test:all] No unit test files discovered under scripts/ or src/.\n');
    process.exit(1);
  }

  ensureFlagsSynced(projectRoot);
  process.stdout.write(`[test:all] running ${testFiles.length} discovered unit test files\n`);

  const result = spawnSync(
    process.execPath,
    ['--experimental-strip-types', '--test', ...forwardedArgs, ...testFiles],
    {
      cwd: projectRoot,
      env: { ASTRO_TELEMETRY_DISABLED: '1', ...process.env },
      stdio: 'inherit',
    },
  );
  if (result.error) {
    process.stderr.write(`[test:all] failed to spawn test runner: ${result.error.message}\n`);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  runCli();
}
