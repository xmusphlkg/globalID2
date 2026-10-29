/** Deterministic sentences built from export numbers. No model rewrites these. */

import type { AlertAI, Locale, WeeklyChange } from '../../utils/situationTypes.ts';

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "This week: 2 new outbreaks, 1 outbreak with updates, ..." in the reader's language. */
export function weeklyDiff(
  change: Pick<WeeklyChange, 'new_outbreaks' | 'updated_outbreaks' | 'new_places' | 'alerts'>,
  locale: Locale,
): string {
  const fresh = change.new_outbreaks.length;
  const updated = change.updated_outbreaks.length;
  if (locale === 'zh') {
    return `本周新增 ${fresh} 起疫情，${updated} 起疫情有更新，首次出现 ${change.new_places} 个地点，共 ${change.alerts} 条警报。`;
  }
  if (locale === 'fr') {
    return `Cette semaine : ${plural(fresh, 'nouvelle flambée', 'nouvelles flambées')}, ${plural(updated, 'flambée mise à jour', 'flambées mises à jour')}, ${plural(change.new_places, 'lieu signalé pour la première fois', 'lieux signalés pour la première fois')}, ${plural(change.alerts, 'alerte', 'alertes')}.`;
  }
  return `This week: ${plural(fresh, 'new outbreak', 'new outbreaks')}, ${plural(updated, 'outbreak with updates', 'outbreaks with updates')}, ${plural(change.new_places, 'place reported for the first time', 'places reported for the first time')}, ${plural(change.alerts, 'alert', 'alerts')}.`;
}

/** How an alert was produced: which review it passed and what the models read. */
export function provenance(ai: AlertAI | null, locale: Locale): string {
  if (!ai) return '';
  const review = {
    cross_model: { en: 'Two-model peer review', zh: '两个模型互审', fr: 'Relecture croisée par deux modèles' },
    single_model: { en: 'Single-model review', zh: '单模型评审', fr: 'Relecture par un seul modèle' },
  };
  const input = {
    native_file: { en: 'read from the original file', zh: '读取原始文件', fr: 'lu depuis le fichier original' },
    text: { en: 'read as extracted text', zh: '读取提取文本', fr: 'lu comme texte extrait' },
  };
  return [
    ai.review_mode ? review[ai.review_mode][locale] : null,
    ai.input_mode ? input[ai.input_mode][locale] : null,
  ]
    .filter(Boolean)
    .join(' · ');
}
