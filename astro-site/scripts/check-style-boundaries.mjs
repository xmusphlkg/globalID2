import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

export const GLOBAL_STYLES_LINE_BUDGET = 2000;

const walk = (directory) => readdirSync(directory).flatMap((name) => {
  const path = resolve(directory, name);
  return statSync(path).isDirectory() ? walk(path) : [path];
});

export function auditStyleBoundaries({
  projectRoot = resolve('.'),
  sourceRoot = resolve(projectRoot, 'src'),
  stylesRoot = resolve(sourceRoot, 'styles'),
  lineBudget = GLOBAL_STYLES_LINE_BUDGET,
} = {}) {
  const failures = [];
  const globalCssPath = resolve(stylesRoot, 'global.css');
  const styleFiles = existsSync(stylesRoot)
    ? walk(stylesRoot).filter((path) => path.endsWith('.css')).sort()
    : [];
  const styleFileSet = new Set(styleFiles);
  const sourceFiles = walk(sourceRoot).filter((path) => /\.(astro|css|ts|tsx)$/.test(path));

  let totalStyleLines = 0;
  for (const stylePath of styleFiles) {
    const css = readFileSync(stylePath, 'utf8');
    const label = relative(projectRoot, stylePath).split(sep).join('/');
    const lines = css.split(/\r?\n/).length;
    totalStyleLines += lines;
    if (/--compat-/.test(css)) failures.push(`${label} reintroduced compatibility color variables`);
    if (/slate-\d+/.test(css)) failures.push(`${label} reintroduced a Tailwind slate presentation color`);
  }

  if (totalStyleLines > lineBudget) {
    failures.push(`src/styles/*.css has ${totalStyleLines} combined lines; budget is ${lineBudget}`);
  }

  // Verify every local @import in global.css resolves to an existing file in src/styles/.
  if (existsSync(globalCssPath)) {
    const globalSource = readFileSync(globalCssPath, 'utf8');
    for (const match of globalSource.matchAll(/@import\s+['"](\.[^'"]+)['"]/g)) {
      const target = resolve(dirname(globalCssPath), match[1]);
      if (!existsSync(target)) {
        failures.push(`src/styles/global.css imports missing partial ${match[1]}`);
      }
    }
  }

  for (const path of sourceFiles) {
    const source = readFileSync(path, 'utf8');
    const label = relative(projectRoot, path).split(sep).join('/');
    const isSharedStyle = styleFileSet.has(path);
    if (/slate-\d+/.test(source) && !isSharedStyle) {
      failures.push(`${label} uses a legacy slate presentation class`);
    }
    if (/Source Serif 4|Source Sans 3|Newsreader|JetBrains Mono/.test(source)) {
      failures.push(`${label} references a removed typeface`);
    }
    if (/font-family:\s*var\(--font-serif\s*,/.test(source)) {
      failures.push(`${label} uses the undefined legacy --font-serif variable; use --font-interface`);
    }
    if (/['"]IBM Plex Sans['"],\s*(?:ui-serif,\s*Georgia,\s*)?serif/.test(source)) {
      failures.push(`${label} gives IBM Plex Sans a serif fallback`);
    }
    const importantLines = source.split(/\r?\n/).filter((line) => line.includes('!important'));
    for (const line of importantLines) {
      const isReducedMotionGuard = isSharedStyle && line.includes('animation-duration: .01ms');
      if (!isReducedMotionGuard) failures.push(`${label} contains an unapproved !important declaration`);
    }
  }

  return {
    passed: failures.length === 0,
    failures,
    styleLines: totalStyleLines,
    styleFiles: styleFiles.length,
    sourceFiles: sourceFiles.length,
  };
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const result = auditStyleBoundaries();
  if (!result.passed) {
    console.error('[style-boundaries] FAIL');
    result.failures.forEach((failure) => console.error(`- ${failure}`));
    process.exit(1);
  }
  console.log(`[style-boundaries] PASS styles=${result.styleLines} lines (${result.styleFiles} files), files=${result.sourceFiles}`);
}
