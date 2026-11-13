import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import test from 'node:test';
import { buildTheme, writeThemeCandidate } from './build-theme.mjs';

const defaults = JSON.parse(readFileSync(new URL('../../shared/site-theme-v1.json', import.meta.url), 'utf8'));
const hash = data => createHash('sha256').update(data).digest('hex');

test('theme candidate is deterministic and its compatibility entry resolves to the verified immutable CSS', () => {
  const candidate = buildTheme(defaults);
  assert.deepEqual(candidate, buildTheme(JSON.parse(JSON.stringify(defaults))));
  const href = candidate.entry.match(/url\("([^"]+)"\)/)[1];
  const resolved = new URL(href, 'https://static.example.org/public/theme/compat/v1/theme.css');
  assert.equal(resolved.pathname, `/public/${candidate.manifest.objects[0].key}`);
  for (const [index, body] of [candidate.css, candidate.entry].entries()) {
    assert.equal(hash(body), candidate.manifest.objects[index].sha256);
    assert.equal(Buffer.byteLength(body), candidate.manifest.objects[index].bytes);
  }
  assert.equal(candidate.manifest.public_verified, false);
});

test('a color update changes only the prepared theme artifacts and retains old immutable assets', () => {
  const output = mkdtempSync(resolve(tmpdir(), 'gids-theme-'));
  try {
    const before = writeThemeCandidate(defaults, output);
    const next = structuredClone(defaults);
    next.root['--color-teal'] = '17 120 112';
    const after = writeThemeCandidate(next, output);
    assert.notEqual(before.content_sha256, after.content_sha256);
    assert.equal(readdirSync(resolve(output, 'theme/versions/sha256')).length, 2);
    assert.equal(hash(readFileSync(resolve(output, before.objects[0].key))), before.content_sha256);
    assert.equal(after.content_render_count, 0);
    assert.equal(after.html_assembly_count, 0);
    assert.equal(after.pages_deployment_required, false);
    assert.deepEqual(readdirSync(output).sort(), ['theme', 'theme-candidate.json']);
  } finally { rmSync(output, { recursive: true, force: true }); }
});

test('the compatibility compiler rejects structural changes, CSS injection and schema drift', () => {
  for (const mutate of [
    value => { value.schema_version = 2; },
    value => { value.root['--unknown'] = '1rem'; },
    value => { value.root['--bg'] = '0 0 0; } body { display: none'; },
    value => { value.root['--bg'] = '256 0 0'; },
    value => { value.mobile['--page-gutter'] = 'url(https://example.org)'; },
    value => { delete value.light['--bg']; },
    value => { value.fonts = []; },
  ]) {
    const input = structuredClone(defaults);
    mutate(input);
    assert.throws(() => buildTheme(input));
  }
});

test('every independently changeable theme variable has a bundled fallback', () => {
  const css = readFileSync(new URL('../src/styles/global.css', import.meta.url), 'utf8');
  for (const [selector, values] of [[':root', defaults.root], ["html[data-theme='light']", defaults.light]]) {
    const block = css.slice(css.indexOf(`${selector} {`)).split('}')[0];
    for (const key of Object.keys(values)) assert.ok(block.includes(`${key}:`), key);
  }
});
