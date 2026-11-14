import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { FileFingerprints } from './file-fingerprints.mjs';
import { createSourceDependencyFingerprinter } from './source-dependencies.mjs';

function fixture(run) {
  const root = mkdtempSync(join(tmpdir(), 'gids-source-graph-'));
  const write = (path, body) => {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), body);
  };
  const hash = entries => createSourceDependencyFingerprinter({
    siteRoot: root, projectRoot: root, fingerprints: new FileFingerprints(join(root, 'cache')),
  })(entries);
  try { run({ root, write, hash }); } finally { rmSync(root, { recursive: true, force: true }); }
}

test('route import closure follows layouts, localized wrappers, lazy islands, CSS, and aliases', () => fixture(({ write, hash }) => {
  write('tsconfig.json', JSON.stringify({ compilerOptions: { paths: { '@/*': ['src/*'] } } }));
  write('src/pages/article.astro', `---\nimport Layout from '@/layouts/Base.astro';\n---\n<Layout><script>import('../islands/chart.ts');</script></Layout>`);
  write('src/pages/fr/article.astro', `---\nimport Article from '../article.astro';\n---\n<Article />`);
  write('src/layouts/Base.astro', `---\nimport '../styles/global.css';\n---\n<slot />`);
  write('src/styles/global.css', '.title { color: red; }');
  write('src/islands/chart.ts', `export const lazy = () => import('./chart-helper');`);
  write('src/islands/chart-helper.ts', 'export const value = 1;');
  write('src/pages/report.astro', 'report before');
  write('src/pages/situation.astro', 'situation before');
  const entries = ['src/pages/article.astro', 'src/pages/fr/article.astro'];
  const initial = hash(entries);
  write('src/pages/report.astro', 'report after');
  write('src/pages/situation.astro', 'situation after');
  assert.equal(hash(entries), initial);
  write('src/islands/chart-helper.ts', 'export const value = 2;');
  const clientChanged = hash(entries);
  assert.notEqual(clientChanged, initial);
  write('src/styles/global.css', '.title { color: blue; }');
  assert.notEqual(hash(entries), clientChanged);
  const cssChanged = hash(entries);
  write('src/layouts/Base.astro', `---\nimport '../styles/global.css';\n---\n<main><slot /></main>`);
  assert.notEqual(hash(entries), cssChanged);
}));

test('unknown dynamic imports conservatively include all sources; newly resolved imports invalidate', () => fixture(({ write, hash }) => {
  write('src/pages/article.ts', 'export const load = name => import(name);');
  write('src/pages/report.ts', 'export const value = 1;');
  const initial = hash(['src/pages/article.ts']);
  write('src/pages/report.ts', 'export const value = 2;');
  assert.notEqual(hash(['src/pages/article.ts']), initial);
  const beforeData = hash(['src/pages/article.ts']);
  write('src/data/dynamic.json', '{"value":1}');
  assert.notEqual(hash(['src/pages/article.ts']), beforeData);
  write('src/pages/article.ts', `import '../new';`);
  const missing = hash(['src/pages/article.ts']);
  write('src/new.ts', 'export const value = 3;');
  assert.notEqual(hash(['src/pages/article.ts']), missing);
}));

test('generated JSON is tracked by data snapshots and source JSON remains in the closure', () => fixture(({ write, hash }) => {
  write('src/pages/article.ts', `import data from '../data/article.json'; import config from '../settings.json';`);
  write('src/data/article.json', '{"value":1}');
  write('src/settings.json', '{"value":1}');
  const initial = hash(['src/pages/article.ts']);
  write('src/data/article.json', '{"value":2}');
  assert.equal(hash(['src/pages/article.ts']), initial);
  write('src/settings.json', '{"value":2}');
  assert.notEqual(hash(['src/pages/article.ts']), initial);
}));

test('actual research route closure excludes report and Situation templates without a conservative fallback', () => fixture(({ root }) => {
  let plan;
  const graph = createSourceDependencyFingerprinter({
    siteRoot: join(import.meta.dirname, '..'),
    fingerprints: new FileFingerprints(join(root, 'cache')),
    onPlan: value => { plan = value; },
  });
  graph(['src/pages/research/articles/[slug].astro', 'src/pages/fr/research/articles/[slug].astro',
    'src/pages/zh/research/articles/[slug].astro', 'src/pages/research/cards/[slug].png.ts']);
  assert.equal(plan.conservative, false, plan.reasons.join('\n'));
  assert.ok(plan.files.some(path => path.endsWith('/layouts/BaseLayout.astro')));
  assert.ok(plan.files.some(path => path.endsWith('/components/Header.astro')));
  assert.ok(plan.files.some(path => path.endsWith('/components/Footer.astro')));
  // Publishing a changelog entry must not invalidate every research article.
  assert.ok(!plan.files.some(path => path.endsWith('/data/changelog.ts')));
  assert.ok(plan.files.some(path => path.endsWith('/styles/global.css')));
  assert.ok(plan.files.some(path => path.endsWith('/public/scripts/analytics.js')));
  assert.ok(!plan.files.some(path => path.includes('/pages/countries/') || path.includes('/pages/situation/')));
}));

test('version and update pages compile without embedding the authored release catalogue', () => fixture(({ root }) => {
  let plan;
  createSourceDependencyFingerprinter({
    siteRoot: join(import.meta.dirname, '..'), fingerprints: new FileFingerprints(join(root, 'cache')),
    onPlan: value => { plan = value; },
  })(['src/pages/changelog.astro', 'src/pages/data-updates.astro']);
  assert.equal(plan.conservative, false, plan.reasons.join('\n'));
  assert.ok(plan.files.some(path => path.endsWith('/public/useChangelog.ts')));
  assert.ok(!plan.files.some(path => path.endsWith('/data/changelog.ts')));
}));

for (const [name, css, asset] of [
  ['unquoted relative font', '@font-face { src: url(./font.woff2); }', 'src/styles/font.woff2'],
  ['unquoted bare font', '@font-face { src: URL(font.woff2); }', 'src/styles/font.woff2'],
  ['public absolute font', '@font-face { src: url(/fonts/font.woff2?v=2#variant); }', 'public/fonts/font.woff2'],
  ['quoted public image', '.icon { background: url("/images/icon.svg#mark"); }', 'public/images/icon.svg'],
  ['escaped CSS filename', String.raw`@font-face { src: url(./font\(one\).woff2); }`, 'src/styles/font(one).woff2'],
  ['encoded CSS filename', '@font-face { src: url("./font%20one.woff2"); }', 'src/styles/font one.woff2'],
  ['hex-escaped filename', String.raw`@font-face { src: url(./\66 ont.woff2); }`, 'src/styles/font.woff2'],
]) {
  test(`CSS dependency tracks ${name} content`, () => fixture(({ write, hash }) => {
    write('src/styles/page.css', css);
    write(asset, 'first asset');
    const initial = hash(['src/styles/page.css']);
    write(asset, 'changed asset');
    assert.notEqual(hash(['src/styles/page.css']), initial);
  }));
}

test('CSS import url and quoted bare imports follow nested stylesheets and their assets', () => fixture(({ write, hash }) => {
  write('src/pages/article.astro', '<style>@import url(../styles/theme.css) layer(theme);</style>');
  write('src/styles/theme.css', '@import "fonts.css"; .title { color: red }');
  write('src/styles/fonts.css', '@font-face { src: url(/font.woff2); }');
  write('public/font.woff2', 'first font');
  const initial = hash(['src/pages/article.astro']);
  write('public/font.woff2', 'second font');
  assert.notEqual(hash(['src/pages/article.astro']), initial);
  const fontChanged = hash(['src/pages/article.astro']);
  write('src/styles/theme.css', '@import "fonts.css"; .title { color: blue }');
  assert.notEqual(hash(['src/pages/article.astro']), fontChanged);
}));

test('CSS skips external, data, fragment, commented and quoted non-resource URLs', () => fixture(({ write, hash }) => {
  write('src/styles/page.css', `
    @import url("https://example.invalid/style.css");
    .a { background: url(//example.invalid/image.png); mask: url(#filter); }
    .b { background: url(data:image/png;base64,aGVsbG8=); }
    .c::before { content: "url(./not-an-asset.png)"; }
    /* @import './not-a-stylesheet.css'; background: url(./missing.png); */
  `);
  write('src/unrelated.ts', 'before');
  const initial = hash(['src/styles/page.css']);
  write('src/unrelated.ts', 'after');
  assert.equal(hash(['src/styles/page.css']), initial);
}));

test('unresolved dynamic CSS conservatively tracks source and public assets', () => fixture(({ write, hash }) => {
  write('src/styles/page.css', '.a { background: url(var(--image)); }');
  write('public/image.svg', 'before');
  const initial = hash(['src/styles/page.css']);
  write('public/image.svg', 'after');
  assert.notEqual(hash(['src/styles/page.css']), initial);
}));
