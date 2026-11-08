import { useSurveillanceSummary } from './useSurveillanceSummary';
import { cadence, countryLabel, number, coverage, sourceEntries, pick as translate, type Language, type Summary } from '../../lib/surveillance-presentation';

export default function SurveillanceCountryContext({ dataUrl, language }: { dataUrl: string; language: Language }) {
  const { data, version, error, retry } = useSurveillanceSummary(dataUrl);
  const pick = (en: string, zh: string, fr: string) => translate(language, en, zh, fr);
  if (!data) return <p role="status">{error ? <>{pick('Current data is unavailable.', '当前数据暂不可用。', 'Les données actuelles sont indisponibles.')} <button className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button></> : pick('Loading current data…', '正在加载最新数据…', 'Chargement des données actuelles…')}</p>;
  const revision = <p className="figure-caption mt-3" data-surveillance-version={version}>{pick('Data updated', '数据更新于', 'Données mises à jour')} {String(data.generated_at ?? '—').slice(0, 10)}</p>;
    const name = countryLabel(data, language);
    const sources = sourceEntries(data.source_info).map(source => source.label).join(' · ');
    const frequencies = [...new Set((data.diseases ?? []).map((row: Summary) => row.period_granularity).filter(Boolean))].map(value => cadence(value, language)).join(' · ');
    const fallbacks: Record<string, string> = {
      surveillance_system: pick(`Official surveillance sources for ${name}: ${sources}.`, `${name}的官方监测来源：${sources}。`, `Sources officielles de surveillance pour ${name} : ${sources}.`),
      interpretation: pick(`Coverage: ${coverage(data)}; ${number(data.disease_count, language)} tracked diseases.`, `覆盖 ${coverage(data)}，纳入 ${number(data.disease_count, language)} 种疾病。`, `Couverture : ${coverage(data)} ; ${number(data.disease_count, language)} maladies suivies.`),
      reporting_cadence: pick(`Reporting periods: ${frequencies}. Counts retain the original period and are not frequency-normalized.`, `报告频率：${frequencies}。病例数保留原始报告期间，不进行频率归一化。`, `Périodes : ${frequencies}. Les comptes conservent la période d’origine, sans normalisation entre fréquences.`),
      limitations: pick('Reported counts depend on case definitions, reporting delays, source cadence and available denominators.', '已报告数值受病例定义、报告延迟、来源频率和可用人口分母影响。', 'Les comptes dépendent des définitions de cas, des retards, de la fréquence des sources et des dénominateurs disponibles.'),
    };
    return <div className="space-y-4">
      {data[`brief_${language}`] && <p className="text-sm leading-6 text-[rgb(var(--text-muted))]">{data[`brief_${language}`]}</p>}
      <p className="figure-caption">{pick('Sources', '来源', 'Sources')}: {data[`source_summary_${language}`] || sources}
        {data.country_brief_updated_at && <> · {pick('Profile updated', '简介更新于', 'Profil mis à jour')} {String(data.country_brief_updated_at).slice(0, 10)}</>}</p>
      <details><summary className="cursor-pointer text-sm font-semibold">{pick('Review interpretation, cadence, and limitations', '查看解读、报告频率与数据限制', 'Voir l’interprétation, la fréquence et les limites')}</summary>
      <div className="grid gap-4 sm:grid-cols-2 mt-4">{[
        ['surveillance_system', pick('Surveillance system', '监测系统', 'Système de surveillance')],
        ['interpretation', pick('Current interpretation', '当前解读', 'Interprétation actuelle')],
        ['reporting_cadence', pick('Reporting cadence', '报告频率', 'Fréquence de déclaration')],
        ['limitations', pick('Data limitations', '数据限制', 'Limites des données')],
      ].map(([key, title]) => <div className="border border-[rgb(var(--border))] p-4" key={key}><div className="academic-meta-label">{title}</div><p className="mt-2 text-sm leading-6 text-[rgb(var(--text-muted))]">{key === 'reporting_cadence' ? fallbacks[key] : data[`${key}_${language}`] || fallbacks[key]}</p></div>)}</div></details>{revision}
    </div>;
}
