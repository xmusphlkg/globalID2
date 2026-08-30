import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(scriptDirectory, '..');

export function buildSituationRedirectRules(dataRoot = resolve(siteRoot, 'src', 'data', 'situation', 'v3')) {
  const definitions = [
    { directory: 'weekly', pattern: /^\d{4}-W\d{2}\.json$/ },
    { directory: 'monthly', pattern: /^\d{4}-\d{2}\.json$/ },
  ];
  const rules = [];
  for (const definition of definitions) {
    const directory = resolve(dataRoot, definition.directory);
    if (!existsSync(directory)) continue;
    for (const filename of readdirSync(directory).filter(name => definition.pattern.test(name)).sort()) {
      const period = filename.replace(/\.json$/, '');
      const destination = `/situation/${definition.directory}/${period}/`;
      rules.push(`/situation/${period}/  ${destination}  301`);
      rules.push(`/situation/${period}  ${destination}  301`);
    }
  }
  return rules;
}

export function renderRedirects(baseRedirects, generatedRules) {
  const base = baseRedirects.trimEnd();
  const generated = [...new Set(generatedRules)];
  return `${base}\n\n# Generated legacy Situation Room redirects\n${generated.join('\n')}\n`;
}

export function writeBuildRedirects({
  publicFile = resolve(siteRoot, 'public', '_redirects'),
  outputFile = resolve(siteRoot, 'dist', '_redirects'),
  dataRoot,
} = {}) {
  const baseRedirects = readFileSync(publicFile, 'utf8');
  const rules = buildSituationRedirectRules(dataRoot);
  writeFileSync(outputFile, renderRedirects(baseRedirects, rules));
  return { outputFile, generatedRules: rules.length };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = writeBuildRedirects();
  process.stdout.write(`[redirects] wrote ${result.generatedRules} generated rules to ${result.outputFile}\n`);
}
