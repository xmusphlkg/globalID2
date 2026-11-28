// Independent validators use separate Node processes so CPU work can run on
// multiple cores. Writers and cache promotion remain behind validation gates.
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { availableParallelism } from 'node:os';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const DEFAULT_MAX_BUILD_CHECK_WORKERS = 3;
export const MAX_BUILD_CHECK_WORKERS = 6;

export function checkConcurrency(value = process.env.GIDS_BUILD_CHECK_WORKERS) {
  const count = Number(value ?? Math.min(DEFAULT_MAX_BUILD_CHECK_WORKERS, availableParallelism()));
  if (!Number.isInteger(count) || count < 1 || count > MAX_BUILD_CHECK_WORKERS) {
    throw new Error(`GIDS_BUILD_CHECK_WORKERS must be 1–${MAX_BUILD_CHECK_WORKERS}`);
  }
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

function roundSeconds(value) {
  return Number(Number(value).toFixed(3));
}

export async function buildPipeline({ cwd = resolve(import.meta.dirname, '..'), env = process.env, signal, run = runStages } = {}) {
  assertRuntimeDataReady(env);
  const concurrency = checkConcurrency(env.GIDS_BUILD_CHECK_WORKERS);
  const timingReportSetting = typeof env.GIDS_BUILD_TIMING_REPORT === 'string'
    ? env.GIDS_BUILD_TIMING_REPORT.trim()
    : '';
  const timingReportPath = timingReportSetting ? resolve(cwd, timingReportSetting) : '';
  const pipelineStarted = performance.now();
  const stageTimings = [];
  let groupIndex = 0;
  let pipelineError;

  const runGroup = async (stages, groupOptions) => {
    const group = ++groupIndex;
    const groupWorkers = Math.min(groupOptions.concurrency ?? 1, stages.length);
    const groupStarted = performance.now();
    const result = await run(stages, groupOptions);
    const groupWallSeconds = roundSeconds((performance.now() - groupStarted) / 1000);
    if (Array.isArray(result)) {
      for (const entry of result) {
        stageTimings.push({
          name: entry.name,
          seconds: roundSeconds(entry.seconds),
          group,
          group_wall_seconds: groupWallSeconds,
          group_workers: groupWorkers,
        });
      }
    } else {
      for (const item of stages) {
        stageTimings.push({
          name: item.name,
          seconds: groupWallSeconds,
          group,
          group_wall_seconds: groupWallSeconds,
          group_workers: groupWorkers,
        });
      }
    }
    return result;
  };

  const stage = (name, file, ...args) => ({ name, args: ['--experimental-strip-types', `scripts/${file}.mjs`, ...args] });
  const options = { cwd, env, signal };
  const checks = { ...options, concurrency };
  const check = (name, file, ...args) => {
    const value = stage(name, file, ...args);
    // CLI heap limits take precedence over the renderer's NODE_OPTIONS. Parallel
    // workers must not each inherit the full 6 GiB rendering allowance.
    value.args.unshift('--max-old-space-size=1536');
    return value;
  };

  try {
    // Sync flags writes public inputs used by the compiler and source checks.
    await runGroup([stage('sync flags', 'sync-flags')], options);
    // Source checks read frozen inputs and can overlap the renderer on other cores.
    // runStages cancels the entire group if either checks or rendering fail.
    await runGroup([
      stage('render and restore', 'build-site'),
      check('source styles', 'check-style-boundaries'),
      check('source translations', 'check-i18n'),
      check('map ISO mapping', 'build-world-feature-iso', '--check'),
    ], { ...options, concurrency: concurrency + 1 });
    if (env.GIDS_RESEARCH_PUBLICATION_ENABLED === '1') {
      await runGroup([stage('render independent research collections', 'build-site-research-collections')], options);
    }
    // Disjoint dist/ post-processing writers:
    // - split-research-evidence reads/writes dist/research/evidence-manifest.json & dist/research/evidence-shards/*
    // - optimize-world-map reads public/data/world.json & writes dist/data/world.json
    await runGroup([
      check('split research evidence', 'split-research-evidence'),
      check('optimize map', 'optimize-world-map'),
    ], { ...options, concurrency: Math.min(2, concurrency) });
    await runGroup([
      check('performance audit', 'check-performance-budgets'),
      check('page assets', 'check-build-assets'),
      check('stylesheet audit', 'build-output'),
      check('SEO audit', 'seo-audit-workers'),
      check('output translations', 'check-i18n', '--dist'),
    ], checks);
    // Never promote a new page cache after any validator fails.
    await runGroup([stage('finalize page caches', 'finalize-research-build')], options);
    if (env.GIDS_PAGES_DATA_WORKER === '1') {
      await runGroup([{ name: 'package Pages data Worker', args: ['../cloudflare/surveillance-data/scripts/build-pages.mjs', 'dist'] }], options);
    }
  } catch (error) {
    pipelineError = error;
    throw error;
  } finally {
    if (timingReportPath) {
      const report = {
        total_seconds: roundSeconds((performance.now() - pipelineStarted) / 1000),
        workers: concurrency,
        status: pipelineError ? 'failed' : 'ok',
        ...(pipelineError ? { error: pipelineError.message } : {}),
        stages: stageTimings,
      };
      mkdirSync(dirname(timingReportPath), { recursive: true });
      writeFileSync(timingReportPath, `${JSON.stringify(report, null, 2)}\n`);
    }
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
