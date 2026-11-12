// Independent validators use separate Node processes so CPU work can run on
// multiple cores. Writers and cache promotion remain behind validation gates.
import { spawn } from 'node:child_process';
import { availableParallelism } from 'node:os';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export function checkConcurrency(value = process.env.GIDS_BUILD_CHECK_WORKERS) {
  const count = Number(value ?? Math.min(3, availableParallelism()));
  if (!Number.isInteger(count) || count < 1 || count > 3) throw new Error('GIDS_BUILD_CHECK_WORKERS must be 1–3');
  return count;
}

export function assertRuntimeDataReady(env) {
  if (env.GLOBALID_SKIP_SITE_DATA_GENERATION === '1' && env.PUBLIC_GIDS_RELEASE_ID && env.GIDS_RUNTIME_DATA_READY !== '1') {
    throw new Error('Runtime surveillance data is not initialized. Initialize and verify the data Worker and JSON publication, enable surveillance JSON publication, then restart the worker before deploying templates (GIDS_RUNTIME_DATA_READY=1 is required).');
  }
  if (env.GLOBALID_SKIP_SITE_DATA_GENERATION === '1' && env.PUBLIC_GIDS_RELEASE_ID && env.GIDS_RUNTIME_SITUATION_READY !== '1') {
    throw new Error('Runtime Situation data is not initialized. Initialize and verify the Situation JSON publication and same-origin routes before deploying templates (GIDS_RUNTIME_SITUATION_READY=1 is required).');
  }
}

export async function runStages(stages, { concurrency = 1, cwd, env = process.env, log = console.log, signal } = {}) {
  if (!Number.isInteger(concurrency) || concurrency < 1) throw new Error('Invalid stage concurrency');
  const started = performance.now();
  const children = new Set();
  let next = 0;
  let failure;
  const timings = [];
  const terminate = (child, name = 'SIGTERM') => {
    try {
      if (process.platform === 'win32') child.kill(name);
      else if (child.pid) process.kill(-child.pid, name);
    } catch (error) { if (error.code !== 'ESRCH') throw error; }
  };
  let killTimer;
  const stop = error => {
    if (failure) return;
    failure = error;
    for (const child of children) terminate(child);
    killTimer = setTimeout(() => { for (const child of children) terminate(child, 'SIGKILL'); }, 2000);
    killTimer.unref();
  };
  const abort = () => stop(new Error('Build interrupted'));
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const worker = async () => {
    while (!failure && next < stages.length) {
      const stage = stages[next++];
      const begin = performance.now();
      log(`[build-stage] start ${stage.name}`);
      try {
        await new Promise((done, reject) => {
          const child = spawn(process.execPath, stage.args, {
            cwd, env: { ...env, ...stage.env }, stdio: 'inherit', detached: process.platform !== 'win32',
          });
          children.add(child);
          child.once('error', reject);
          child.once('close', (code, childSignal) => {
            children.delete(child);
            if (code === 0) done();
            else reject(new Error(`${stage.name} failed (${childSignal ?? code})`));
          });
        });
        const seconds = (performance.now() - begin) / 1000;
        timings.push({ name: stage.name, seconds });
        log(`[build-timing] ${stage.name} ${seconds.toFixed(1)}s`);
      } catch (error) { stop(error); }
    }
  };
  try {
    await Promise.all(Array.from({ length: Math.min(concurrency, stages.length) }, worker));
    if (failure) throw failure;
    log(`[build-timing] group wall=${((performance.now() - started) / 1000).toFixed(1)}s workers=${concurrency}`);
    return timings;
  } finally {
    clearTimeout(killTimer);
    signal?.removeEventListener('abort', abort);
  }
}

export async function buildPipeline({ cwd = resolve(import.meta.dirname, '..'), env = process.env, signal, run = runStages } = {}) {
  assertRuntimeDataReady(env);
  const concurrency = checkConcurrency(env.GIDS_BUILD_CHECK_WORKERS);
  const stage = (name, file, ...args) => ({ name, args: ['--experimental-strip-types', `scripts/${file}.mjs`, ...args] });
  const options = { cwd, env, signal };
  const checks = { ...options, concurrency };
  const check = (name, file, ...args) => {
    const value = stage(name, file, ...args);
    // CLI heap limits take precedence over the renderer's NODE_OPTIONS. Three
    // validators must not each inherit the full 6 GiB rendering allowance.
    value.args.unshift('--max-old-space-size=1536');
    return value;
  };
  // Sync flags writes public inputs used by the compiler and source checks.
  await run([stage('sync flags', 'sync-flags')], options);
  // Source checks read frozen inputs and can overlap the renderer on other cores.
  // runStages cancels the entire group if either checks or rendering fail.
  await run([
    stage('render and restore', 'build-site'),
    check('source styles', 'check-style-boundaries'),
    check('source translations', 'check-i18n'),
    check('map ISO mapping', 'build-world-feature-iso', '--check'),
  ], { ...options, concurrency: concurrency + 1 });
  await run([stage('split research evidence', 'split-research-evidence')], options);
  await run([stage('optimize map', 'optimize-world-map')], options);
  await run([
    check('performance audit', 'check-performance-budgets'),
    check('SEO audit', 'seo-audit-workers'),
    check('output translations', 'check-i18n', '--dist'),
  ], checks);
  // Never promote a new page cache after any validator fails.
  await run([stage('finalize page caches', 'finalize-research-build')], options);
  if (env.GIDS_PAGES_DATA_WORKER === '1') {
    await run([{ name: 'package Pages data Worker', args: ['../cloudflare/surveillance-data/scripts/build-pages.mjs', 'dist'] }], options);
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const controller = new AbortController();
  const interrupt = () => controller.abort();
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  try { await buildPipeline({ signal: controller.signal }); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
  finally {
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
  }
}
