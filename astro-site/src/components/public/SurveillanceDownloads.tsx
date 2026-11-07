import { useEffect, useState } from 'react';
import { mainSiteBase } from '../../lib/papers';
import { coverage, number, pick, safeUrl, type Language, type Summary } from '../../lib/surveillance-presentation.ts';

const requests = new Map<string, Promise<Summary>>();
export default function SurveillanceDownloads({ dataUrl, language }: { dataUrl: string; language: Language }) {
  const [data, setData] = useState<Summary>();
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [period, setPeriod] = useState('');
  const [format, setFormat] = useState('');
  const url = dataUrl.replace('/site-data/', '/site-data/downloads/').replace('-summary.json', '.json');
  const prefix = language === 'en' ? '' : `/${language}`;
  useEffect(() => {
    let cancelled = false;
    setData(undefined); setError(false);
    if (!requests.has(url)) requests.set(url, fetch(`${mainSiteBase()}${url}`).then(async response => {
      if (!response.ok) throw new Error('Download catalogue unavailable');
      const value = await response.json();
      if (value.schema_version !== 1 || !Array.isArray(value.parts)) throw new Error('Invalid download catalogue');
      return value;
    }).catch(error => { requests.delete(url); throw error; }));
    requests.get(url)!.then(value => { if (!cancelled) setData(value); }).catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [url, attempt]);
  const text = (en: string, zh: string, fr: string) => pick(language, en, zh, fr);
  return <div className="space-y-4 mt-5" data-testid="download-archive">
    <p className="figure-caption">{text('Download archives have their own release date and may differ from the live curves.', '下载档案有独立发布日期，可能与实时曲线的版本不同。', 'Les archives ont leur propre date de publication et peuvent différer des courbes actuelles.')}</p>
    {!data && <p role="status" className="text-sm">{error ? <>{text('Download information is unavailable.', '下载信息暂不可用。', 'Informations de téléchargement indisponibles.')} <button className="underline" onClick={() => setAttempt(value => value + 1)}>{text('Retry', '重试', 'Réessayer')}</button> · <a className="underline" href={`${prefix}/downloads/`}>{text('Browse downloads', '浏览下载档案', 'Parcourir les téléchargements')}</a></> : text('Loading download information…', '正在加载下载信息…', 'Chargement des téléchargements…')}</p>}
    {data && <>
      <dl className="grid gap-3 sm:grid-cols-3 text-sm">{[
        [text('Download snapshot', '下载文件版本', 'Version des téléchargements'), String(data.generated_at ?? '—').slice(0, 10)],
        [text('Rows', '行数', 'Lignes'), number(data.record_count, language)],
        [text('Coverage', '覆盖范围', 'Couverture'), coverage(data)],
      ].map(([name, value]) => <div key={name}><dt className="academic-meta-label">{name}</dt><dd className="mt-1">{value}</dd></div>)}</dl>
      {data.includes_series_provenance && <p className="figure-caption">{number(data.source_series_count, language)} {text('source series', '条来源序列', 'séries sources')} · {number(data.source_observation_count, language)} {text('observations', '条观测', 'observations')}</p>}
      <div className="flex flex-wrap gap-4 text-sm">
        <label>{text('Time range', '时间范围', 'Période')} <select className="site-action-secondary border p-2" value={period} onChange={event => setPeriod(event.target.value)}><option value="">{text('All time ranges', '全部时间范围', 'Toutes les périodes')}</option>{data.parts.map((part: Summary) => <option key={part.id} value={part.id}>{part.label || part.id}</option>)}</select></label>
        <label>{text('File format', '文件格式', 'Format de fichier')} <select className="site-action-secondary border p-2" value={format} onChange={event => setFormat(event.target.value)}><option value="">{text('All formats', '全部格式', 'Tous les formats')}</option>{['csv', 'json', 'xlsx'].map(value => <option key={value} value={value}>{value.toUpperCase()}</option>)}</select></label>
      </div>
      {data.parts.filter((part: Summary) => !period || part.id === period).map((part: Summary) => <div className="border-t border-[rgb(var(--border))] pt-3" key={part.id}>
        <p className="text-sm font-medium">{part.label || part.id} · {coverage(part)}</p>
        <div className="flex flex-wrap gap-3 mt-2">{Object.entries(part.files ?? {}).filter(([kind]) => !format || kind === format).map(([kind, file]: [string, any]) => safeUrl(file.url) ? <a key={kind} className="site-action-secondary border px-3 py-2 text-sm" href={safeUrl(file.url)} target="_blank" rel="noopener noreferrer">{kind.toUpperCase()}{typeof file.bytes === 'number' ? ` · ${(file.bytes / 1024 / 1024).toLocaleString(language, { maximumFractionDigits: 1 })} MB` : ''}</a> : null)}</div>
      </div>)}
    </>}
  </div>;
}
