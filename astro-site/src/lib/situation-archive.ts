import { INTERNATIONAL_DIR, listWeeks, readSituationMap, readWeek, weekAlerts } from '../utils/international.ts';

/** Inputs actually displayed in a historical week, excluding export timestamps
 * and unrelated outbreaks. The renderer and cache planner share this view. */
export function weeklyArchive(period: string, dir = INTERNATIONAL_DIR) {
  const week = readWeek(period, dir);
  if (!week) return null;
  const periods = listWeeks(dir).map(row => row.period_key).reverse();
  const position = periods.indexOf(period);
  const outbreakById = new Map((readSituationMap(dir)?.outbreaks ?? []).map(row => [row.outbreak_id, row]));
  const section = (ids: string[]) => ids.flatMap(id => {
    const outbreak = outbreakById.get(id);
    const alerts = weekAlerts(id, period, dir);
    if (!outbreak || !alerts.length) return [];
    const { outbreak_id, disease_id, disease_slug, disease_label, disease_name_en, disease_name_zh, first_published } = outbreak;
    return [{ outbreak: { outbreak_id, disease_id, disease_slug, disease_label, disease_name_en, disease_name_zh, first_published }, alerts }];
  });
  const fresh = section(week.new_outbreaks);
  const updated = section(week.updated_outbreaks);
  const dates = [...fresh, ...updated].flatMap(row => row.alerts.map(alert => alert.published_date)).sort();
  return { week, previous: periods[position - 1], next: periods[position + 1], fresh, updated, published: dates[0] ?? null };
}
