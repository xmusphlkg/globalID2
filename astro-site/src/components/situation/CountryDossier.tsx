import type { Locale, MapPoint, SeriesGlance, SiteIndex } from '../../utils/situationTypes.ts';
import { recencyOf, placeLabel, RECENCY_COLORS } from './model.ts';
import { COPY, STATUS_LABELS, countryName, diseaseName, formatCount, formatDay, formatSignedPercent } from './labels.ts';
import { seriesKey } from './site.ts';


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
  const glance = point.disease_id ? site.series[seriesKey(point.country_code, point.disease_id)] : undefined;
  const diseaseLabel = diseaseName(point, locale);
  return (
    <div className="sit-fusion">
      <h3>{copy.surveillanceHeading}</h3>
      {glance ? (
        <SeriesRow glance={glance} locale={locale} />
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
      {diseaseId && !series && country.seriesCount > 0 && disease && (
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
