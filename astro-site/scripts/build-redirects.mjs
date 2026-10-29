import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(scriptDirectory, '..');

/** Month (YYYY-MM) in which an ISO week (YYYY-Www) starts, on its Monday. */
export function isoWeekMonth(period) {
  const match = /^(\d{4})-W(\d{2})$/.exec(period);
  if (!match) return null;
  const year = Number(match[1]);
  const week = Number(match[2]);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (week - 1) * 7);
  return monday.toISOString().slice(0, 7);
}

/**
 * Legacy ``/situation/<period>/`` URLs redirect to the weekly or monthly page.
 * Periods come from the published digest index, so every target exists.
 */
export function buildSituationRedirectRules(dataRoot = resolve(siteRoot, 'src', 'data', 'situation', 'international')) {
  const indexFile = resolve(dataRoot, 'digests', 'index.json');
  if (!existsSync(indexFile)) return [];
  let periods = [];
  try {
    periods = (JSON.parse(readFileSync(indexFile, 'utf8')).periods ?? [])
      .map(row => String(row?.period_key ?? ''))
      .filter(key => /^\d{4}-W\d{2}$/.test(key));
  } catch {
    return [];
  }
  const weeks = [...new Set(periods)].sort();
  const months = [...new Set(weeks.map(isoWeekMonth).filter(Boolean))].sort();
  const rules = [];
  for (const [directory, keys] of [['weekly', weeks], ['monthly', months]]) {
    for (const period of keys) {
      const destination = `/situation/${directory}/${period}/`;
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
