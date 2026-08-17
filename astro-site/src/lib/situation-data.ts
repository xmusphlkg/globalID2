import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { isSituationReportV3, type SituationReportV3 } from '../utils/situation';

export type SituationPeriodKind = 'weekly' | 'monthly';

export function situationReportDirectory(kind: SituationPeriodKind): string {
  return resolve(`./src/data/situation/v3/${kind}`);
}

export function loadSituationReport(kind: SituationPeriodKind, period: string): SituationReportV3 | null {
  const file = resolve(situationReportDirectory(kind), `${period}.json`);
  if (!existsSync(file)) return null;
  const value: unknown = JSON.parse(readFileSync(file, 'utf-8'));
  return isSituationReportV3(value) ? value : null;
}

export function listSituationReports(kind: SituationPeriodKind): SituationReportV3[] {
  const directory = situationReportDirectory(kind);
  const pattern = kind === 'weekly' ? /^\d{4}-W\d{2}\.json$/ : /^\d{4}-\d{2}\.json$/;
  if (!existsSync(directory)) return [];
  return readdirSync(directory)
    .filter(name => pattern.test(name))
    .map(name => loadSituationReport(kind, name.replace(/\.json$/, '')))
    .filter((report): report is SituationReportV3 => report !== null)
    .sort((left, right) => left.report.period_key.localeCompare(right.report.period_key));
}
