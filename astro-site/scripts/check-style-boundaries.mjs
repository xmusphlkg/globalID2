import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

const sourceRoot = resolve('src');
const globalCssPath = resolve('src/styles/global.css');
const globalCss = readFileSync(globalCssPath, 'utf8');
const failures = [];

const walk = directory => readdirSync(directory).flatMap(name => {
  const path = resolve(directory, name);
  return statSync(path).isDirectory() ? walk(path) : [path];
});

const sourceFiles = walk(sourceRoot).filter(path => /\.(astro|css|ts|tsx)$/.test(path));
const globalLines = globalCss.split(/\r?\n/).length;

if (globalLines > 2000) failures.push(`global.css has ${globalLines} lines; budget is 2000`);
if (/--compat-/.test(globalCss)) failures.push('global.css reintroduced compatibility color variables');
if (/slate-\d+/.test(globalCss)) failures.push('global.css reintroduced a Tailwind slate presentation color');

for (const path of sourceFiles) {
  const source = readFileSync(path, 'utf8');
  const label = path.replace(`${resolve('.')}\/`, '');
  if (/slate-\d+/.test(source) && path !== globalCssPath) {
    failures.push(`${label} uses a legacy slate presentation class`);
  }
  if (/Source Serif 4|Source Sans 3|Newsreader|JetBrains Mono/.test(source)) {
    failures.push(`${label} references a removed typeface`);
  }
  if (/['"]IBM Plex Sans['"],\s*(?:ui-serif,\s*Georgia,\s*)?serif/.test(source)) {
    failures.push(`${label} gives IBM Plex Sans a serif fallback`);
  }
  const importantLines = source.split(/\r?\n/).filter(line => line.includes('!important'));
  for (const line of importantLines) {
    const isReducedMotionGuard = path === globalCssPath && line.includes('animation-duration: .01ms');
    if (!isReducedMotionGuard) failures.push(`${label} contains an unapproved !important declaration`);
  }
}

if (failures.length) {
  console.error('[style-boundaries] FAIL');
  failures.forEach(failure => console.error(`- ${failure}`));
  process.exit(1);
}

console.log(`[style-boundaries] PASS global.css=${globalLines} lines, files=${sourceFiles.length}`);
