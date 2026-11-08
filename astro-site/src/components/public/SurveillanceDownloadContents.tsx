import '../download-dialog.css';
import { useEffect, useRef, useState } from 'react';
import { mainSiteBase } from '../../lib/papers';
import { coverage, number, pick, safeUrl, type Language, type Summary } from '../../lib/surveillance-presentation.ts';

const formats = ['csv', 'json', 'xlsx'] as const;
type File = { url: string; filename?: string; bytes?: number };
const size = (bytes?: number) => bytes === undefined ? '—' : bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : bytes >= 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${bytes} B`;

function FileCard({ part, format, file, language }: { part: Summary; format: string; file: File; language: Language }) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const text = (en: string, zh: string, fr: string) => pick(language, en, zh, fr);
  async function download() {
    setBusy(true); setStatus(text('Preparing download…', '正在准备下载…', 'Préparation du téléchargement…'));
    const controller = new AbortController();
    request.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 60_000);
    try {
      const response = await fetch(file.url, { cache: 'no-cache', signal: controller.signal });
      if (!response.ok) throw new Error('Download unavailable');
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = file.filename || `globalid-${part.id}.${format}`;
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
      setStatus(text('Download started.', '下载已开始。', 'Téléchargement lancé.'));
    } catch {
      setStatus(text('Download unavailable. Retry or use the original file link below.', '下载暂不可用，请重试或使用下方原始文件链接。', 'Téléchargement indisponible. Réessayez ou utilisez le lien du fichier original ci-dessous.'));
    } finally { window.clearTimeout(timeout); setBusy(false); }
  }
  return <article className="download-file-item" data-download-file data-download-time={part.id} data-download-format={format}>
    <div className="download-file-item__heading"><div className="min-w-0"><h3>{part.label || part.id}</h3><p>{coverage(part)}</p>{part.is_current && <span className="download-file-item__current">{text('Current', '当前', 'Actuelle')}</span>}</div>
      <button className="site-action-primary download-file-item__action" type="button" onClick={download} disabled={busy}>{text(`Download ${format.toUpperCase()}`, `下载 ${format.toUpperCase()}`, `Télécharger ${format.toUpperCase()}`)}</button>
    </div>
    <div className="download-file-item__meta"><span>{number(part.record_count, language)} {text('records', '条记录', 'enregistrements')}</span><span>{size(file.bytes)}</span><span>{format.toUpperCase()}</span></div>
    <a className="download-file-item__raw" href={file.url} target="_blank" rel="noopener noreferrer" title={file.url}><code>{file.url}</code></a>
    <p className="download-file-item__status" role="status">{status}</p>
  </article>;
}

export default function SurveillanceDownloadContents({ dataUrl, language }: { dataUrl: string; language: Language }) {
  const [data, setData] = useState<Summary>();
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [period, setPeriod] = useState('all');
  const [format, setFormat] = useState('all');
  const text = (en: string, zh: string, fr: string) => pick(language, en, zh, fr);
  const url = dataUrl.replace('/site-data/', '/site-data/downloads/').replace('-summary.json', '.json');
  const prefix = language === 'en' ? '' : `/${language}`;
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    setData(undefined); setError(false);
    void fetch(`${mainSiteBase()}${url}`, { cache: 'no-cache', signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Download catalogue unavailable');
      const value = await response.json();
      if (value?.schema_version !== 1 || !Array.isArray(value.parts) || !value.parts.every((part: Summary) => part && typeof part.id === 'string')) throw new Error('Invalid download catalogue');
      if (active) setData(value);
    }).catch(() => { if (active) setError(true); }).finally(() => window.clearTimeout(timeout));
    return () => { active = false; controller.abort(); window.clearTimeout(timeout); };
  }, [url, attempt]);
  if (!data) return <p className="download-dialog__feedback" role="status">{error ? <>{text('Download information is unavailable.', '下载信息暂不可用。', 'Informations de téléchargement indisponibles.')} <button className="underline" onClick={() => setAttempt(value => value + 1)}>{text('Retry', '重试', 'Réessayer')}</button> · <a className="underline" href={`${prefix}/downloads/`}>{text('Browse downloads', '浏览下载档案', 'Parcourir les téléchargements')}</a></> : text('Loading download information…', '正在加载下载信息…', 'Chargement des téléchargements…')}</p>;
  const files = data.parts.filter((part: Summary) => period === 'all' || part.id === period).flatMap((part: Summary) => formats.filter(kind => format === 'all' || kind === format).flatMap(kind => {
    const file = part.files?.[kind];
    const url = safeUrl(file?.url);
    return url ? [{ part, format: kind, file: { ...file, url } as File }] : [];
  }));
  return <div className="download-dialog__content">
    <div className="download-dialog__selection">
      <div className="download-dialog__filters">
        <label className="download-filter"><span>{text('Time range', '时间范围', 'Période')}</span><select data-download-time-filter value={period} onChange={event => setPeriod(event.target.value)}><option value="all">{text('All time ranges', '全部时间范围', 'Toutes les périodes')}</option>{data.parts.map((part: Summary) => <option key={part.id} value={part.id}>{part.label || part.id}</option>)}</select></label>
        <label className="download-filter"><span>{text('File format', '文件格式', 'Format de fichier')}</span><select data-download-format-filter value={format} onChange={event => setFormat(event.target.value)}><option value="all">{text('All formats', '全部格式', 'Tous les formats')}</option>{formats.map(kind => <option key={kind} value={kind}>{kind.toUpperCase()}</option>)}</select></label>
      </div>
      <p className="download-dialog__snapshot"><span>{text('Download snapshot', '下载文件版本', 'Version des téléchargements')}: {String(data.generated_at ?? '—').slice(0, 10)}</span><span>{number(data.record_count, language)} {text('rows', '行', 'lignes')}</span><span>{coverage(data)}</span></p>
    </div>
    <div className="download-dialog__body"><div className="download-file-grid">{files.map(({ part, format, file }: { part: Summary; format: string; file: File }) => <FileCard key={`${part.id}-${format}`} part={part} format={format} file={file} language={language} />)}</div>
      {!files.length && <p className="download-dialog__empty">{text('No files match these filters.', '没有符合筛选条件的文件。', 'Aucun fichier ne correspond à ces filtres.')}</p>}
    </div>
  </div>;
}
