import assert from 'node:assert/strict';
import {mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {gzipSync} from 'node:zlib';
import test from 'node:test';
import {openGzipSizeCache} from './gzip-size-cache.mjs';

test('reuses exact bytes across runs and recalculates changed content', t => {
  const dir=mkdtempSync(join(tmpdir(),'gzip-cache-'));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const file=join(dir,'sizes.json');
  const bytes=Buffer.from('unchanged content');
  const first=openGzipSizeCache(file);
  assert.equal(first.measure(bytes),gzipSync(bytes,{level:9}).length);
  first.save();
  let calls=0;
  const second=openGzipSizeCache(file,{compress: b=>{calls++;return gzipSync(b,{level:9}).length;}});
  assert.equal(second.measure(bytes),first.measure(bytes));
  assert.equal(calls,0);
  const changed=Buffer.from('a changed document');
  assert.equal(second.measure(changed),gzipSync(changed,{level:9}).length);
  assert.equal(calls,1);
});

test('invalid cache or a different compression algorithm falls back to computation', t=>{
  const dir=mkdtempSync(join(tmpdir(),'gzip-cache-'));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const file=join(dir,'sizes.json'), bytes=Buffer.from('content');
  for(const body of ['broken',JSON.stringify({schema:1,algorithm:'other',entries:{}})]){
    writeFileSync(file,body);
    const cache=openGzipSizeCache(file);
    assert.equal(cache.measure(bytes),gzipSync(bytes,{level:9}).length);
    assert.equal(cache.stats.misses,1);
  }
});
