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

test('render overlaps source checks, writers stay ordered and check workers are bounded', async () => {
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
    'optimize map', 'performance audit', 'finalize page caches']);
  for (const value of ['0', '4', '1.5', 'NaN']) assert.throws(() => checkConcurrency(value));
  const controller = new AbortController(); controller.abort();
  await assert.rejects(runStages([{ name: 'never', args: ['-e', 'process.exit(0)'] }],
    { signal: controller.signal, log() {} }), /interrupted/);
});
