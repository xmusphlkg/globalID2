import { useMemo } from 'react';
import GIDSLoader from '../GIDSLoader';
import EpidemicCurve from '../charts/EpidemicCurve';
import { useSurveillanceSummary } from './useSurveillanceSummary';
import { cadence, coverage, diseaseLabel, number, pick, sourceEntries, type Language } from '../../lib/surveillance-presentation';

interface Props {
  language?: Language;
}

function routeValue(value: string | null | undefined): string {
  return String(value ?? '').trim().toLowerCase().replace(/[^a-z0-9-]/g, '');
}

export default function CountryDiseaseRuntime({ language = 'en' }: Props) {
  const isBrowser = typeof window !== 'undefined';
  const query = isBrowser ? new URLSearchParams(window.location.search) : null;
  const country = routeValue(query?.get('country'));
  const disease = routeValue(query?.get('disease'));
  const summaryUrl = country ? `/site-data/countries/${country}-summary.json` : undefined;
  const { data, error, retry } = useSurveillanceSummary(summaryUrl);
  const row = useMemo(() => {
    const diseases = Array.isArray(data?.diseases) ? data.diseases : [];
    return diseases.find((entry: any) => String(entry.slug ?? '').toLowerCase() === disease)
      ?? diseases.find((entry: any) => String(entry.disease_id ?? entry.id ?? '').toLowerCase() === disease);
  }, [data, disease]);
  const diseaseId = String(row?.disease_id ?? row?.id ?? disease).toUpperCase();
  const countryName = language === 'zh'
    ? data?.country_name_zh ?? data?.name_zh ?? country.toUpperCase()
    : language === 'fr'
      ? data?.country_name_fr ?? data?.name_fr ?? data?.country_name_en ?? country.toUpperCase()
      : data?.country_name_en ?? data?.name_en ?? country.toUpperCase();
  const diseaseName = row ? diseaseLabel(row, language) : disease.replaceAll('-', ' ');
  const sources = sourceEntries(data?.source_info).slice(0, 3);
  const prefix = language === 'zh' ? '/zh' : language === 'fr' ? '/fr' : '';
  const validRoute = Boolean(country && disease);

  if (!isBrowser) {
    return <section className="mx-auto max-w-5xl px-4 py-16" aria-busy="true">
      <h1 className="academic-title">{pick(language, 'Country disease surveillance', '国家疾病监测', 'Surveillance par pays et maladie')}</h1>
      <GIDSLoader language={language} />
    </section>;
  }

  if (!validRoute) {
    return <section className="mx-auto max-w-3xl px-4 py-16 text-center" role="alert">
      <h1 className="academic-title">{pick(language, 'Country disease profile unavailable', '国家疾病页面不可用', 'Profil pays-maladie indisponible')}</h1>
      <p className="figure-caption mt-4">{pick(language, 'The requested country and disease were not provided.', '未提供有效的国家和疾病参数。', 'Le pays et la maladie demandés ne sont pas valides.')}</p>
    </section>;
  }

  if (!data && !error) {
    return <section className="mx-auto max-w-5xl px-4 py-16" aria-busy="true"><GIDSLoader language={language} /></section>;
  }

  if (error || !data) {
    return <section className="mx-auto max-w-3xl px-4 py-16 text-center" role="alert">
      <h1 className="academic-title">{pick(language, 'Surveillance data unavailable', '监测数据暂不可用', 'Données de surveillance indisponibles')}</h1>
      <p className="figure-caption mt-4">{pick(language, 'The published country dataset could not be loaded.', '当前发布的国家数据暂时无法加载。', 'Le jeu de données publié du pays ne peut pas être chargé.')}</p>
      <button type="button" className="site-action-secondary mt-6 px-4 py-2" onClick={retry}>{pick(language, 'Retry', '重试', 'Réessayer')}</button>
    </section>;
  }

  return <article className="mx-auto max-w-5xl px-4 py-10 sm:px-6 lg:px-8">
    <header className="academic-hero">
      <p className="academic-kicker">{pick(language, 'Area disease surveillance profile', '区域疾病监测画像', 'Profil de surveillance par zone et maladie')}</p>
      <h1 className="academic-title mt-4">{pick(language, `${diseaseName} surveillance in ${countryName}`, `${countryName}${diseaseName}监测`, `Surveillance de ${diseaseName} en ${countryName}`)}</h1>
      <p className="academic-lead">{pick(language, 'Official-source observations, coverage details, and trend context for this area and disease.', '汇总该区域和疾病的官方来源观测、覆盖范围与趋势信息。', 'Observations officielles, couverture et contexte des tendances pour cette zone et cette maladie.')}</p>
      <div className="academic-meta-grid mt-6">
        {[
          [pick(language, 'Coverage', '覆盖区间', 'Période couverte'), coverage(row ?? data)],
          [pick(language, 'Reporting granularity', '报告粒度', 'Granularité des rapports'), cadence(row?.period_granularity, language)],
          [pick(language, 'Latest reported value', '最新报告值', 'Dernière valeur publiée'), number(row?.latest_cases, language)],
          [pick(language, 'Data updated', '数据更新于', 'Données mises à jour'), String(data.generated_at ?? '—').slice(0, 10)],
        ].map(([label, value]) => <div className="academic-meta-item" key={String(label)}><div className="academic-meta-label">{label}</div><div className="academic-meta-value text-sm">{value}</div></div>)}
      </div>
    </header>

    <section className="figure-panel mt-6" id="trends">
      <h2 className="figure-title">{pick(language, 'Surveillance trend', '监测趋势', 'Tendance de surveillance')}</h2>
      <p className="figure-caption mt-2">{pick(language, 'The chart opens with this disease selected for this area.', '图表已预选此区域和疾病。', 'Le graphique est préconfiguré pour cette zone et cette maladie.')}</p>
      <div className="mt-5">
        <EpidemicCurve
          dataUrl={`/site-data/countries/${country}.json`}
          sourceSeriesUrl={`/site-data/countries/${country}-source-series.json`}
          entityIds={diseaseId ? [diseaseId] : undefined}
          initialSelectionIds={diseaseId ? [`${encodeURIComponent(diseaseId)}::published`] : undefined}
          initialLanguage={language}
        />
      </div>
    </section>

    <section className="figure-panel mt-6" id="sources">
      <h2 className="figure-title">{pick(language, 'Official sources', '官方来源', 'Sources officielles')}</h2>
      {sources.length > 0 ? <ul className="mt-4 space-y-3">{sources.map((source: any, index: number) => <li key={`${source.url ?? source.label ?? 'source'}-${index}`}><a className="font-semibold text-brand-500 hover:underline" href={source.url} target="_blank" rel="noreferrer">{source.label ?? source.url}</a></li>)}</ul> : <p className="figure-caption mt-3">{pick(language, 'Source details are available in the published dataset.', '来源详情见已发布数据集。', 'Les détails des sources figurent dans le jeu de données publié.')}</p>}
      <div className="mt-5 flex flex-wrap gap-4 text-sm font-semibold">
        <a className="text-brand-500 hover:underline" href={`${prefix}/countries/${country}/`}>{pick(language, `Open the ${countryName} dataset →`, `打开${countryName}数据集 →`, `Ouvrir le jeu de données de ${countryName} →`)}</a>
        <a className="text-brand-500 hover:underline" href={`${prefix}/diseases/${disease}/`}>{pick(language, `View global ${diseaseName} profile →`, `查看全球${diseaseName}画像 →`, `Voir le profil mondial de ${diseaseName} →`)}</a>
      </div>
    </section>
  </article>;
}
