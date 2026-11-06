import { resolve } from 'node:path';
import { listWeeks, listMonths, weeksOfMonth, readSituationMap } from '../src/utils/international.ts';
import { weeklyArchive } from '../src/lib/situation-archive.ts';

export function addSituationPages({ dataRoot, add, hashFile }) {
  const dir = resolve(dataRoot, 'situation/international');
  for (const week of listWeeks(dir)) {
    add(`situation-week:${week.period_key}`, `situation/weekly/${week.period_key}`, [weeklyArchive(week.period_key, dir)]);
  }
  const months = listMonths(dir).reverse();
  for (const [index, month] of months.entries()) {
    add(`situation-month:${month}`, `situation/monthly/${month}`,
      [weeksOfMonth(month, dir), months[index - 1], months[index + 1]]);
  }
  for (const outbreak of readSituationMap(dir)?.outbreaks ?? []) {
    const id = outbreak.outbreak_id;
    if (!/^[A-Za-z0-9_-]+$/.test(id)) throw new Error(`Unsafe outbreak id: ${id}`);
    add(`situation-outbreak:${id}`, '', [hashFile(resolve(dir, 'outbreaks', `${id}.json`))],
      { locales: [''], paths: [`situation/outbreaks/${id}.json`] });
  }
}
