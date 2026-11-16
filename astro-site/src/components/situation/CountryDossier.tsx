import type { Locale, MapPoint, SeriesGlance, SiteIndex } from '../../utils/situationTypes.ts';
import { recencyOf, placeLabel, RECENCY_COLORS } from './model.ts';
import { COPY, STATUS_LABELS, countryName, diseaseName, formatCount, formatDay, formatSignedPercent, hostLabel } from './labels.ts';
import { seriesKey } from './site.ts';

const HOST_SERIES_NOTE: Record<Locale, string> = {
  en: 'Human surveillance charts apply only to explicitly human events and are hidden for this host scope.',
  zh: '人类监测图表仅适用于明确的人类事件；当前宿主范围不展示这些图表。',
  fr: 'Les graphiques de surveillance humaine concernent uniquement les événements explicitement humains et sont masqués pour cette sélection d’hôtes.',
};

export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 160;
  const height = 36;
  const path = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / span) * (height - 4) - 2;
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg className="sit-spark" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={path} />
    </svg>
  );
}

export function SeriesRow({ glance, locale }: { glance: SeriesGlance; locale: Locale }) {
  const copy = COPY[locale];
  return (
    <div className="sit-series">
      <div className="sit-series-main">
        <strong>{formatCount(glance.value, locale)}</strong>
        <span>
          {copy.nationalSeries}
          {glance.date ? ` · ${formatDay(glance.date, locale)}` : ''}
          {glance.changePct != null && ` · ${formatSignedPercent(glance.changePct, locale)} ${copy.vsPrevious}`}
        </span>
      </div>
      {glance.spark && <Sparkline values={glance.spark} />}
    </div>
  );
}

export function FusionCard({ locale, point, site }: { locale: Locale; point: MapPoint; site: SiteIndex }) {
  const copy = COPY[locale];
  const disease = point.disease_id ? site.diseases[point.disease_id.toUpperCase()] : undefined;
  const country = site.countries[point.country_code.toUpperCase()];
  const availableGlance = point.disease_id ? site.series[seriesKey(point.country_code, point.disease_id)] : undefined;
  const glance = point.host_type === 'human' ? availableGlance : undefined;
  const seriesWithheldForHost = point.host_type !== 'human' && Boolean(availableGlance);
  const diseaseLabel = diseaseName(point, locale);
  return (
    <div className="sit-fusion">
      <h3>{copy.surveillanceHeading}</h3>
      <p>{hostLabel(point, locale)}</p>
      {glance ? (
        <SeriesRow glance={glance} locale={locale} />
      ) : seriesWithheldForHost ? (
        <p>{HOST_SERIES_NOTE[locale]}</p>
      ) : country && country.seriesCount > 0 && point.disease_id ? (
        <p>{copy.diseaseNotInSeries(diseaseLabel, country.seriesCount)}</p>
      ) : (
        <p>{copy.noNationalSeries}</p>
      )}
      <div className="sit-chip-row">
        {glance?.curveHref && <a className="sit-chip" href={glance.curveHref}>{copy.localCurve}</a>}
        {country?.href && <a className="sit-chip" href={country.href}>{copy.openCountry}</a>}
        {disease?.href && <a className="sit-chip" href={disease.href}>{copy.openDisease}</a>}
        {disease?.researchHref && disease.researchCount > 0 && <a className="sit-chip" href={disease.researchHref}>{copy.openResearch(disease.researchCount)}</a>}
        {country?.researchHref && country.researchCount > 0 && <a className="sit-chip" href={country.researchHref}>{copy.openResearch(country.researchCount)}</a>}
      </div>
    </div>
  );
}

export function CountryDossier({
  locale,
  country,
  diseaseId,
  series,
  seriesWithheldForHost = false,
  disease,
  alerts,
  asOf,
  canWiden,
  onWiden,
  onSelect,
}: {
  locale: Locale;
  country: SiteIndex['countries'][string];
  diseaseId: string;
  series?: SeriesGlance;
  seriesWithheldForHost?: boolean;
  disease?: SiteIndex['diseases'][string];
  alerts: MapPoint[];
  asOf: string;
  canWiden: boolean;
  onWiden: () => void;
  onSelect: (id: string) => void;
}) {
  const copy = COPY[locale];
  return (
    <>
      {diseaseId && series && (
        <div className="sit-fusion">
          <h3>{disease?.name_zh && locale === 'zh' ? disease.name_zh : disease?.name_en ?? diseaseId}</h3>
          <SeriesRow glance={series} locale={locale} />
        </div>
      )}
      {diseaseId && seriesWithheldForHost && <p className="sit-muted">{HOST_SERIES_NOTE[locale]}</p>}
      {diseaseId && !series && !seriesWithheldForHost && country.seriesCount > 0 && disease && (
        <p className="sit-muted">{copy.diseaseNotInSeries(locale === 'zh' && disease.name_zh ? disease.name_zh : disease.name_en, country.seriesCount)}</p>
      )}
      <div className="sit-chip-row">
        {series?.curveHref && <a className="sit-chip" href={series.curveHref}>{copy.localCurve}</a>}
        {country.href && <a className="sit-chip" href={country.href}>{copy.openCountry}</a>}
        {disease?.href && <a className="sit-chip" href={disease.href}>{copy.openDisease}</a>}
        {disease?.researchHref && disease.researchCount > 0 && <a className="sit-chip" href={disease.researchHref}>{copy.openResearch(disease.researchCount)}</a>}
        {country.researchHref && country.researchCount > 0 && <a className="sit-chip" href={country.researchHref}>{copy.openResearch(country.researchCount)}</a>}
      </div>
      <section>
        <h3>{copy.alertsHere}</h3>
        {alerts.length === 0 ? (
          <>
            <p className="sit-muted">{copy.noAlertsHere}</p>
            {canWiden && <button type="button" className="sit-text-btn" onClick={onWiden}>{copy.widenWindow}</button>}
          </>
        ) : (
          <ul className="sit-places-list">
            {alerts.slice(0, 12).map(point => {
              const recency = recencyOf(point.published_date, asOf);
              return (
                <li key={point.alert_id}>
                  <button type="button" className="sit-item" onClick={() => onSelect(point.alert_id)}>
                    <span className="sit-dot" style={{ background: RECENCY_COLORS[recency] }} aria-hidden="true" />
                    <span className="sit-item-main">
                      <strong>{diseaseName(point, locale)}</strong>
                      <span>{placeLabel(point, countryName(point, locale))}</span>
                      <span>{hostLabel(point, locale)}</span>
                    </span>
                    <span className="sit-item-meta">
                      <span className={`sit-status sit-status-${point.status}`}>{STATUS_LABELS[point.status][locale]}</span>
                      <time dateTime={point.published_date}>{formatDay(point.published_date, locale)}</time>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
