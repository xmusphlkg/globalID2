import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// A deliberately narrow v1 contract. Structural selectors, fonts, arbitrary
// CSS and external assets require a template release, not a theme candidate.
const colors = new Set([
  '--color-brand', '--color-teal', '--color-accent', '--color-evidence',
  '--bg', '--bg-soft', '--surface', '--surface-raised', '--text', '--text-strong',
  '--text-muted', '--text-faint', '--border', '--border-soft', '--ring-offset',
  '--header-bg', '--footer-bg',
]);
const lengths = new Set([
  '--page-reading-width', '--page-tool-width', '--page-directory-width',
  '--page-gutter', '--page-block-space', '--page-title-size',
]);
const length = '(?:0|(?:[0-9]+(?:\\.[0-9]+)?|\\.[0-9]+)(?:rem|px|vw))';
const lengthPattern = new RegExp(`^(?:${length}|clamp\\(${length},\\s*${length},\\s*${length}\\))$`);
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

function declarations(tokens, allowed, required) {
  if (!tokens || typeof tokens !== 'object' || Array.isArray(tokens)) throw new Error('Theme tokens must be an object');
  if (required.some(key => !(key in tokens))) throw new Error('Theme is missing required v1 tokens');
  return Object.keys(tokens).sort().map(key => {
    const value = tokens[key];
    if (!allowed.has(key) || typeof value !== 'string') throw new Error(`Unsupported theme token: ${key}`);
    if (colors.has(key)) {
      if (!/^\d{1,3} \d{1,3} \d{1,3}$/.test(value) || value.split(' ').some(n => Number(n) > 255)) {
        throw new Error(`Invalid RGB channels: ${key}`);
      }
    } else if (!lengthPattern.test(value)) throw new Error(`Invalid theme length: ${key}`);
    return `  ${key}: ${value};`;
  }).join('\n');
}

export function compileTheme(source) {
  if (source?.schema_version !== 1 || source.compatibility !== 'v1'
    || Object.keys(source).some(key => !['schema_version', 'compatibility', 'root', 'light', 'mobile'].includes(key))) {
    throw new Error('Unsupported theme contract');
  }
  const root = declarations(source.root, new Set([...colors, ...lengths]), [...colors, ...lengths]);
  const lightColors = [...colors].filter(key => !key.startsWith('--color-'));
  const light = declarations(source.light, new Set(lightColors), lightColors);
  const mobile = declarations(source.mobile, new Set(['--page-gutter']), ['--page-gutter']);
  // Unlayered variables override the bundled @layer base defaults. Defaults
  // remain in global.css so a failed theme request leaves a readable page.
  return `/* GIDS theme compatibility v1 */\n:root {\n${root}\n}\nhtml[data-theme='light'] {\n${light}\n}\n@media (max-width: 640px) {\n  :root {\n${mobile}\n  }\n}\n`;
}

export function buildTheme(source) {
  const css = compileTheme(source);
  const sha256 = digest(css);
  const key = `theme/versions/sha256/${sha256}.css`;
  // Relative to /theme/compat/v1/theme.css, including CDN installations that
  // use a path prefix. No redirects or Worker URLs are needed.
  const entry = `@import url("../../versions/sha256/${sha256}.css");\n`;
  const entryKey = 'theme/compat/v1/theme.css';
  return {
    css, entry,
    manifest: {
      schema_version: 1, compatibility: 'v1',
      content_sha256: sha256,
      objects: [
        { key, sha256, bytes: Buffer.byteLength(css), content_type: 'text/css; charset=utf-8', cache_control: 'public, max-age=31536000, immutable' },
        { key: entryKey, sha256: digest(entry), bytes: Buffer.byteLength(entry), content_type: 'text/css; charset=utf-8', cache_control: 'public, max-age=0, s-maxage=30, must-revalidate' },
      ],
      coverage: ['home', 'country'],
      content_render_count: 0, html_assembly_count: 0, template_compile_count: 0,
      theme_compile_count: 1, pages_deployment_required: false,
      // This is a local candidate, not a receipt of a public commit.
      status: 'prepared', public_verified: false,
    },
  };
}

export function writeThemeCandidate(source, output) {
  const candidate = buildTheme(source);
  for (const [index, body] of [candidate.css, candidate.entry].entries()) {
    const path = resolve(output, candidate.manifest.objects[index].key);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, body);
  }
  writeFileSync(resolve(output, 'theme-candidate.json'), `${JSON.stringify(candidate.manifest, null, 2)}\n`);
  return candidate.manifest;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.length > 2) throw new Error('Usage: node scripts/build-theme.mjs [source.json] [output-directory]');
  const source = resolve(args[0] ?? new URL('../../shared/site-theme-v1.json', import.meta.url).pathname);
  const output = resolve(args[1] ?? new URL('../../exports/theme-candidate', import.meta.url).pathname);
  const manifest = writeThemeCandidate(JSON.parse(readFileSync(source, 'utf8')), output);
  console.log(JSON.stringify({ output, ...manifest }, null, 2));
}
