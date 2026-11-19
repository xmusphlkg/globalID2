import { useEffect, useRef, useState } from 'react';
import { downloadPartKey, formatFileSize } from '../../lib/download-presentation.ts';
import { coverage, number, pick, type Language, type Summary } from '../../lib/surveillance-presentation.ts';

type File = { url: string; filename?: string; bytes?: number };
const size = formatFileSize;

export default function DownloadFileCard({ part, title, format, file, language }: { part: Summary; title: string; format: string; file: File; language: Language }) {
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
  return <article className="download-file-item" data-download-file data-download-time={downloadPartKey(part)} data-download-format={format}>
    <div className="download-file-item__heading"><div className="min-w-0"><h3>{title}</h3><p>{coverage(part)}</p>{part.is_current && <span className="download-file-item__current">{text('Latest available', '最新可用', 'Dernière disponible')}</span>}</div>
      <button className="site-action-primary download-file-item__action" type="button" onClick={download} disabled={busy}>{text(`Download ${format.toUpperCase()}`, `下载 ${format.toUpperCase()}`, `Télécharger ${format.toUpperCase()}`)}</button>
    </div>
    <div className="download-file-item__meta"><span>{number(part.record_count, language)} {text('records', '条记录', 'enregistrements')}</span><span>{size(file.bytes)}</span><span>{format.toUpperCase()}</span></div>
    <a className="download-file-item__raw" href={file.url} target="_blank" rel="noopener noreferrer" title={file.url}><code>{file.url}</code></a>
    <p className="download-file-item__status" role="status">{status}</p>
  </article>;
}

