import { useEffect, useState } from 'react';
import GIDSLoader from '../GIDSLoader';
import DownloadFileCard from './DownloadFileCard';
import { readSurveillanceDownloadIndex } from '../../lib/surveillance-download-index.ts';
import { readSurveillanceDownloads } from '../../lib/surveillance-downloads.ts';
import { downloadCenterSelection, downloadWindowLabel, emptyDownloadFilters, readDownloadFilters, type DownloadFilters } from '../../lib/download-filters.ts';
import { downloadPartKey } from '../../lib/download-presentation.ts';
import { number, pick, safeUrl, type Language, type Summary } from '../../lib/surveillance-presentation.ts';
import '../download-dialog.css';
import './download-center.css';

const formats = ['csv', 'json', 'xlsx'];
const pageSize = 24;
const label = (row: Summary, language: Language) => row[`name_${language}`] || row.name_en || row.name || row.code || row.disease_id;

export default function DownloadCenter({ language = 'en' }: { language?: Language }) {
  const text = (en: string, zh: string, fr: string) => pick(language, en, zh, fr);
  const [filters, setFilters] = useState<DownloadFilters>(emptyDownloadFilters);
  const [initialized, setInitialized] = useState(false);
  const [index, setIndex] = useState<Summary>();
  const [indexError, setIndexError] = useState(false);
  const [dataset, setDataset] = useState<{ key: string; data: Summary; version?: string }>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState(0);
  const key = filters.country ? `countries/${filters.country.toLowerCase()}` : filters.disease ? `diseases/${filters.disease.toLowerCase()}` : '';

  useEffect(() => {
    const sync = () => { setFilters(readDownloadFilters(window.location.search)); setPage(0); setInitialized(true); };
    sync(); window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  useEffect(() => {
    if (!initialized) return;
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries(filters)) value ? url.searchParams.set(key, value) : url.searchParams.delete(key);
    window.history.replaceState(null, '', url);
  }, [filters, initialized]);
  useEffect(() => {
    const controller = new AbortController();
    setIndexError(false);
    void readSurveillanceDownloadIndex({ signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) {
        if (!value) throw new Error('Download directory unavailable');
        setIndex(value.index);
      }
    }).catch(() => { if (!controller.signal.aborted) setIndexError(true); });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    if (!initialized || !key || !index) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true); setError(false);
    void readSurveillanceDownloads(`/site-data/${key}-summary.json`, { signal: controller.signal }).then(value => {
      if (value.source !== 'publication') throw new Error('Versioned downloads required');
      if (!controller.signal.aborted) setDataset({ key, data: value.data, version: value.version });
    }).catch(() => { if (!controller.signal.aborted) setError(true); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [key, initialized, index, attempt]);

  const update = (name: keyof DownloadFilters, value: string) => {
    setFilters(previous => ({ ...previous, [name]: value, ...(['country', 'disease'].includes(name) ? { window: '' } : {}) }));
    setPage(0);
  };
  const countries: Summary[] = index?.countries || [];
  const diseases: Summary[] = index?.diseases || [];
  const countryNames = new Map(countries.map(row => [row.code, label(row, language)]));
  const diseaseNames = new Map(diseases.map(row => [row.disease_id, label(row, language)]));
  const named = (names: Map<string, string>, code: string) => names.has(code) ? `${names.get(code)} (${code})` : code;
  const data = dataset?.key === key ? dataset.data : undefined;
  const windowOptions = { language };
  const selection = downloadCenterSelection(data?.parts || [], filters, windowOptions);
  const files = selection.parts.flatMap(part => formats.filter(format => !filters.format || filters.format === format).flatMap(format => {
    const file = part.files?.[format];
    const url = safeUrl(file?.url);
    return url ? [{ part, format, file: { ...file, url } }] : [];
  }));
  const pages = Math.ceil(files.length / pageSize);
  const visiblePage = Math.min(page, Math.max(0, pages - 1));
  const retry = <button type="button" className="site-action-secondary" onClick={() => setAttempt(value => value + 1)}>{text('Retry', '重试', 'Réessayer')}</button>;

  return <section className="download-center" data-download-center data-surveillance-version={dataset?.key === key ? dataset.version : undefined}>
    <div className="download-center__filters">
      <label className="download-filter"><span>{text('Country / region', '国家／地区', 'Pays / région')}</span><select data-download-country-filter value={filters.country} onChange={event => update('country', event.target.value)} disabled={!index}>
        <option value="">{text('All countries / regions', '全部国家／地区', 'Tous les pays / régions')}</option>{countries.map(row => <option key={row.code} value={row.code}>{label(row, language)} ({row.code})</option>)}
      </select></label>
      <label className="download-filter"><span>{text('Disease', '疾病', 'Maladie')}</span><select data-download-disease-filter value={filters.disease} onChange={event => update('disease', event.target.value)} disabled={!index}>
        <option value="">{text('All diseases', '全部疾病', 'Toutes les maladies')}</option>{diseases.map(row => <option key={row.disease_id} value={row.disease_id}>{label(row, language)} ({row.disease_id})</option>)}
      </select></label>
      <label className="download-filter"><span>{text('Time range', '时间范围', 'Période')}</span><select data-download-time-filter value={filters.window} onChange={event => update('window', event.target.value)} disabled={!data || loading}>
        <option value="">{text('All time ranges', '全部时间范围', 'Toutes les périodes')}</option>{selection.windows.map(window => <option key={window.id} value={window.id}>{window.label}</option>)}
      </select></label>
      <label className="download-filter"><span>{text('File format', '文件格式', 'Format de fichier')}</span><select data-download-format-filter value={filters.format} onChange={event => update('format', event.target.value)}>
        <option value="">{text('All formats', '全部格式', 'Tous les formats')}</option>{formats.map(format => <option key={format} value={format}>{format.toUpperCase()}</option>)}
      </select></label>
    </div>
    {!index && <div role="status">{indexError ? <>{text('Download directory unavailable.', '下载目录暂不可用。', 'Répertoire indisponible.')} {retry}</> : <GIDSLoader language={language} />}</div>}
    {index && <p className="download-center__catalogue">{number(countries.length, language)} {text('countries / regions', '个国家／地区', 'pays / régions')} · {number(diseases.length, language)} {text('diseases', '种疾病', 'maladies')} · CSV / JSON / XLSX</p>}
    {!key && <p className="download-center__notice">{text('Choose a country or disease to browse its files, then narrow the time range and format.', '选择国家／地区或疾病，查看全部相关文件，再按时间范围和格式筛选。', 'Choisissez un pays ou une maladie, puis filtrez les fichiers par période et format.')}</p>}
    {key && loading && <GIDSLoader language={language} />}
    {key && !loading && error && <p role="status">{text('Files unavailable. Please retry.', '文件列表暂不可用，请重试。', 'Fichiers indisponibles. Réessayez.')} {retry}</p>}
    {key && !loading && !error && data && <>
      <p className="download-center__count" role="status">{number(files.length, language)} {text('matching files', '个匹配文件', 'fichiers correspondants')} · {text('Download snapshot', '文件更新日期', 'Version des téléchargements')}: {String(data.generated_at || '—').slice(0, 10)}</p>
      <div className="download-file-grid">{files.slice(visiblePage * pageSize, (visiblePage + 1) * pageSize).map(({ part, format, file }) => <DownloadFileCard key={`${downloadPartKey(part)}-${format}`} part={part} file={file} format={format} language={language}
        title={`${named(countryNames, part.country_code || '')} · ${named(diseaseNames, part.disease_id || '')} · ${downloadWindowLabel(part, windowOptions)}`} />)}</div>
      {!files.length && <p>{text('No files match these filters.', '没有符合筛选条件的文件。', 'Aucun fichier ne correspond à ces filtres.')}</p>}
      {pages > 1 && <nav className="download-center__pages" aria-label={text('File pages', '文件分页', 'Pages de fichiers')}>
        <button type="button" className="site-action-secondary" disabled={visiblePage === 0} onClick={() => setPage(visiblePage - 1)}>{text('Previous', '上一页', 'Précédent')}</button>
        <span>{visiblePage + 1} / {pages}</span>
        <button type="button" className="site-action-secondary" disabled={visiblePage + 1 >= pages} onClick={() => setPage(visiblePage + 1)}>{text('Next', '下一页', 'Suivant')}</button>
      </nav>}
    </>}
  </section>;
}
