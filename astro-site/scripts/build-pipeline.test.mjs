import assert from 'node:assert/strict';
import { mkdtempSync, existsSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { assertRuntimeDataReady, buildPipeline, checkConcurrency, runStages } from './build-pipeline.mjs';

test('managed template releases require initialized runtime data before any stage runs', async () => {
  const managed = { GLOBALID_SKIP_SITE_DATA_GENERATION: '1', PUBLIC_GIDS_RELEASE_ID: 'production' };
  let ran = false;
  await assert.rejects(buildPipeline({ env: managed, run: async () => { ran = true; } }), /Initialize and verify the data Worker/);
  assert.equal(ran, false);
  await assert.rejects(buildPipeline({ env: { ...managed, GIDS_RUNTIME_DATA_READY: '1' }, run: async () => { ran = true; } }), /Situation JSON publication/);
  assert.equal(ran, false);
  assert.doesNotThrow(() => assertRuntimeDataReady({ ...managed, GIDS_RUNTIME_DATA_READY: '1', GIDS_RUNTIME_SITUATION_READY: '1' }));
  assert.doesNotThrow(() => assertRuntimeDataReady({ PUBLIC_GIDS_RELEASE_ID: 'offline-fixture' }));
  assert.doesNotThrow(() => assertRuntimeDataReady({ GLOBALID_SKIP_SITE_DATA_GENERATION: '1' }));
});

test('Pages data Worker packaging survives every enabled template build', async () => {
  const groups = [];
  await buildPipeline({ env: { GIDS_PAGES_DATA_WORKER: '1' }, run: async stages => { groups.push(stages.map(stage => stage.name)); } });
  assert.deepEqual(groups.at(-1), ['package Pages data Worker']);
  assert.deepEqual(groups.at(-2), ['finalize page caches']);
});

test('independent template publication rebuilds collections before all output gates', async () => {
  const names = [];
  await buildPipeline({ env: { GIDS_RESEARCH_PUBLICATION_ENABLED: '1' }, run: async stages => {
    names.push(...stages.map(stage => stage.name));
  } });
  assert.ok(names.indexOf('render independent research collections') > names.indexOf('render and restore'));
  assert.ok(names.indexOf('render independent research collections') < names.indexOf('split research evidence'));
  const failed = [];
  await assert.rejects(buildPipeline({ env: { GIDS_RESEARCH_PUBLICATION_ENABLED: '1' }, run: async stages => {
    failed.push(...stages.map(stage => stage.name));
    if (stages.some(stage => stage.name === 'render independent research collections')) throw new Error('collection missing');
  } }), /collection missing/);
  assert.equal(failed.includes('finalize page caches'), false);
});

test('missing styles after collection assembly prevent cache finalization', async () => {
  const ran = [];
  await assert.rejects(buildPipeline({ env: { GIDS_RESEARCH_PUBLICATION_ENABLED: '1' }, run: async stages => {
    ran.push(...stages.map(stage => stage.name));
    if (stages.some(stage => stage.name === 'stylesheet audit')) throw new Error('Missing base layout CSS');
  } }), /Missing base layout CSS/);
  assert.ok(ran.indexOf('stylesheet audit') > ran.indexOf('render independent research collections'));
  assert.equal(ran.includes('finalize page caches'), false);
});

test('independent validators execute concurrently in separate processes', async () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-parallel-checks-'));
  try {
    // Each process waits for its peer: a serial implementation cannot pass.
    const code = `const fs=require('node:fs');const [own,peer]=process.argv.slice(1);
      fs.writeFileSync(own,String(process.pid));
      const deadline=Date.now()+5000;const timer=setInterval(()=>{
        if(fs.existsSync(peer)){clearInterval(timer);process.exit(0)}
        if(Date.now()>deadline)process.exit(1);
      },10);`;
    const a = join(root, 'a'); const b = join(root, 'b');
    await runStages([{ name: 'a', args: ['-e', code, a, b] }, { name: 'b', args: ['-e', code, b, a] }],
      { concurrency: 2, log() {} });
    assert.notEqual(readFileSync(a, 'utf8'), readFileSync(b, 'utf8'));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('failed checks cancel peers, skip queued work, and never promote caches', async () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-failed-checks-'));
  try {
    const marker = join(root, 'late');
    await assert.rejects(runStages([
      { name: 'fail', args: ['-e', 'setTimeout(()=>process.exit(2),100)'] },
      { name: 'peer', args: ['-e', `setTimeout(()=>require('node:fs').writeFileSync(process.argv[1],'bad'),10000)`, marker] },
      { name: 'queued', args: ['-e', `require('node:fs').writeFileSync(process.argv[1],'bad')`, marker] },
    ], { concurrency: 2, log() {} }), /fail failed/);
    assert.equal(existsSync(marker), false);
    const ran = [];
    await assert.rejects(buildPipeline({ env: {}, run: async stages => {
      ran.push(...stages.map(stage => stage.name));
      if (stages.some(stage => stage.name === 'SEO audit')) throw new Error('SEO failed');
    } }), /SEO failed/);
    assert.equal(ran.includes('finalize page caches'), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('render overlaps source checks, disjoint writers parallelize, and check workers are bounded', async () => {
  const calls = [];
  await buildPipeline({ env: { GIDS_BUILD_CHECK_WORKERS: '1' }, run: async (stages, options) => {
    calls.push(stages.map(stage => stage.name));
    if (stages.length > 1) {
      const rendering = stages.some(stage => stage.name === 'render and restore');
      assert.equal(options.concurrency, rendering ? 2 : 1);
      assert.ok(stages.filter(stage => stage.name !== 'render and restore').every(stage => stage.args.includes('--max-old-space-size=1536')));
    }
  } });
  assert.deepEqual(calls.map(stages => stages[0]), ['sync flags', 'render and restore',
    'split research evidence', 'performance audit', 'finalize page caches']);
  assert.deepEqual(calls[2], ['split research evidence', 'optimize map']);

  const defaultWorkers = checkConcurrency(undefined);
  assert.ok(Number.isInteger(defaultWorkers) && defaultWorkers >= 1 && defaultWorkers <= 3);
  for (let i = 1; i <= 6; i++) assert.equal(checkConcurrency(String(i)), i);
  for (const value of ['0', '7', '-1', '1.5', 'NaN', '']) assert.throws(() => checkConcurrency(value), /1–6/);

  const controller = new AbortController(); controller.abort();
  await assert.rejects(runStages([{ name: 'never', args: ['-e', 'process.exit(0)'] }],
    { signal: controller.signal, log() {} }), /interrupted/);
});

test('disjoint post-processing writers run concurrently and explicit worker counts up to 6 propagate', async () => {
  const recorded = [];
  await buildPipeline({ env: { GIDS_BUILD_CHECK_WORKERS: '6' }, run: async (stages, options) => {
    recorded.push({ names: stages.map(stage => stage.name), concurrency: options.concurrency ?? 1 });
  } });
  const renderGroup = recorded.find(group => group.names.includes('render and restore'));
  const postProcessGroup = recorded.find(group => group.names.includes('split research evidence'));
  const auditGroup = recorded.find(group => group.names.includes('performance audit'));
  assert.equal(renderGroup.concurrency, 7);
  assert.deepEqual(postProcessGroup, { names: ['split research evidence', 'optimize map'], concurrency: 2 });
  assert.equal(auditGroup.concurrency, 6);
});

test('GIDS_BUILD_TIMING_REPORT writes structured JSON timings only when configured', async () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-build-timing-'));
  try {
    const defaultReportPath = join(root, 'timing.json');
    await buildPipeline({ cwd: root, env: { GIDS_BUILD_CHECK_WORKERS: '2' }, run: async stages =>
      stages.map((stage, idx) => ({ name: stage.name, seconds: 0.25 * (idx + 1) })) });
    assert.equal(existsSync(defaultReportPath), false);

    const relativeReport = join('reports', 'build-timing.json');
    await buildPipeline({
      cwd: root,
      env: { GIDS_BUILD_CHECK_WORKERS: '4', GIDS_BUILD_TIMING_REPORT: relativeReport },
      run: async stages => stages.map((stage, idx) => ({ name: stage.name, seconds: 0.5 + idx * 0.1 })),
    });
    const reportPath = join(root, relativeReport);
    assert.equal(existsSync(reportPath), true);
    const report = JSON.parse(readFileSync(reportPath, 'utf8'));
    assert.equal(report.status, 'ok');
    assert.equal(report.workers, 4);
    assert.equal(typeof report.total_seconds, 'number');
    assert.ok(report.total_seconds >= 0);
    assert.ok(Array.isArray(report.stages));
    assert.ok(report.stages.some(stage => stage.name === 'split research evidence' && stage.group_workers === 2));
    assert.ok(report.stages.some(stage => stage.name === 'optimize map' && stage.group_workers === 2));
    assert.ok(report.stages.some(stage => stage.name === 'SEO audit' && stage.group_workers === 4));

    const failureReportPath = join(root, 'failed-timing.json');
    await assert.rejects(buildPipeline({
      cwd: root,
      env: { GIDS_BUILD_CHECK_WORKERS: '2', GIDS_BUILD_TIMING_REPORT: failureReportPath },
      run: async stages => {
        if (stages.some(stage => stage.name === 'SEO audit')) throw new Error('SEO audit failed');
        return stages.map(stage => ({ name: stage.name, seconds: 0.12 }));
      },
    }), /SEO audit failed/);
    const failedReport = JSON.parse(readFileSync(failureReportPath, 'utf8'));
    assert.equal(failedReport.status, 'failed');
    assert.equal(failedReport.error, 'SEO audit failed');
    assert.equal(failedReport.workers, 2);
    assert.equal(failedReport.stages.some(stage => stage.name === 'finalize page caches'), false);
    assert.equal(failedReport.stages.some(stage => stage.name === 'sync flags'), true);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
