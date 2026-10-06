import { useEffect, useState } from 'react';
import DiseaseCountryCurve from '../charts/DiseaseCountryCurve';

export const RESEARCH_ASK_SURVEILLANCE_EVENT = 'gids:research-ask-surveillance';

export interface ResearchAskSurveillanceDetail {
  diseaseId: string;
  diseaseSlug?: string | null;
  diseaseNameEn?: string | null;
  diseaseNameZh?: string | null;
  countryCodes?: string[];
  countryNameEn?: string | null;
  countryNameZh?: string | null;
}

declare global {
  interface Window {
    __gidsResearchAskSurveillance?: ResearchAskSurveillanceDetail | null;
  }
}

function validDetail(value: unknown): value is ResearchAskSurveillanceDetail {
  if (!value || typeof value !== 'object') return false;
  const detail = value as Partial<ResearchAskSurveillanceDetail>;
  return typeof detail.diseaseId === 'string' && detail.diseaseId.trim().length > 0;
}

export default function ResearchAskSurveillance({ initialLanguage = 'en' }: { initialLanguage?: 'en' | 'zh' | 'fr' }) {
  // Keep the first client render identical to the static server output. The
  // query result is applied immediately after hydration from the shared
  // window snapshot below.
  const [selection, setSelection] = useState<ResearchAskSurveillanceDetail | null>(null);

  useEffect(() => {
    const handleSelection = (event: Event) => {
      const detail = (event as CustomEvent<ResearchAskSurveillanceDetail | null>).detail;
      setSelection(validDetail(detail) ? detail : null);
    };

    const initial = window.__gidsResearchAskSurveillance;
    if (validDetail(initial)) setSelection(initial);
    window.addEventListener(RESEARCH_ASK_SURVEILLANCE_EVENT, handleSelection);
    return () => window.removeEventListener(RESEARCH_ASK_SURVEILLANCE_EVENT, handleSelection);
  }, []);

  if (!selection) return null;

  const lang = initialLanguage;
  const prefix = lang === 'zh' ? '/zh' : lang === 'fr' ? '/fr' : '';
  const diseaseId = selection.diseaseId.trim().toLowerCase();
  const diseaseLabel = lang === 'zh'
    ? selection.diseaseNameZh || selection.diseaseNameEn || selection.diseaseSlug || selection.diseaseId
    : selection.diseaseNameEn || selection.diseaseSlug || selection.diseaseId;
  const countryCodes = [...new Set((selection.countryCodes ?? [])
    .map((code) => String(code).trim().toUpperCase())
    .filter(Boolean))];
  const geographyLabel = countryCodes.length === 1
      ? (lang === 'zh'
        ? selection.countryNameZh || selection.countryNameEn || countryCodes[0]
        : selection.countryNameEn || countryCodes[0])
      : countryCodes.length > 1
      ? (lang === 'zh' ? `${countryCodes.length} 个相关国家/地区` : lang === 'fr' ? `${countryCodes.length} régions concernées` : `${countryCodes.length} related locations`)
      : (lang === 'zh' ? '报告国家/地区' : lang === 'fr' ? 'régions déclarantes' : 'reporting locations');
  const diseaseHref = selection.diseaseSlug
    ? `${prefix}/diseases/${selection.diseaseSlug}/`
    : `${prefix}/diseases/`;
  const countryHref = countryCodes.length === 1
    ? `${prefix}/countries/${countryCodes[0].toLowerCase()}/`
    : `${prefix}/countries/`;

  return (
    <div className="ask-surveillance-widget" data-research-surveillance="true">
      <div className="ask-surveillance-widget-header">
        <div className="ask-surveillance-scope">
          <span className="ask-surveillance-scope-dot" aria-hidden="true" />
          <span>{lang === 'zh' ? '已匹配监测序列' : lang === 'fr' ? 'Série de surveillance correspondante' : 'Matched surveillance series'}</span>
        </div>
        <div className="ask-surveillance-entity">
          <strong>{diseaseLabel}</strong>
          <span aria-hidden="true">×</span>
          <strong>{geographyLabel}</strong>
        </div>
        <p>
          {lang === 'zh'
            ? '以下为 GIDS 已发布的报告病例时间序列，用于补充文献证据的现实监测背景。'
            : lang === 'fr' ? 'Cette série de cas déclarés par GIDS apporte un contexte de surveillance actuel aux données scientifiques ci-dessus.' : 'This GIDS reported-case time series adds current surveillance context to the literature evidence above.'}
        </p>
        <div className="ask-surveillance-links">
          <a href={diseaseHref}>
            {lang === 'zh' ? '打开疾病数据页' : lang === 'fr' ? 'Ouvrir les données de la maladie' : 'Open disease data'} <span aria-hidden="true">↗</span>
          </a>
          <a href={countryHref}>
            {lang === 'zh' ? '查看地区资料' : lang === 'fr' ? 'Voir le profil régional' : 'View location profile'} <span aria-hidden="true">↗</span>
          </a>
        </div>
      </div>
      <DiseaseCountryCurve
        dataUrl={`/site-data/diseases/${diseaseId}.json`}
        entityIds={countryCodes.length > 0 ? countryCodes : undefined}
        topN={countryCodes.length > 0 ? countryCodes.length : 6}
        height={420}
        initialLanguage={lang}
      />
      <p className="ask-surveillance-note">
        {lang === 'zh'
          ? '解读提示：这是报告病例曲线，不等同于文献中的因果结论，也不构成风险预测或临床建议。来源、报告频率与暂定数据说明见图表注释。'
          : lang === 'fr' ? 'Interprétation : il s’agit d’une courbe de cas déclarés, et non d’une conclusion causale ni d’une prévision de risque. Les sources et notes sur les données provisoires figurent sous le graphique.' : 'Interpretation note: this is a reported-case curve, not a causal conclusion from the literature or a risk forecast. Source, cadence, and provisional-data notes are available below the chart.'}
      </p>
    </div>
  );
}
