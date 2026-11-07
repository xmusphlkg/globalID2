import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { pick, type Language } from '../../lib/surveillance-presentation.ts';
import '../download-dialog.css';

const DownloadContents = lazy(() => import('./SurveillanceDownloadContents'));

export default function SurveillanceDownloads({ dataUrl, language }: { dataUrl: string; language: Language }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const text = (en: string, zh: string, fr: string) => pick(language, en, zh, fr);
  useEffect(() => {
    const element = dialog.current!;
    // Chart actions can open the native dialog directly. Observe that path too,
    // including browsers without the newer dialog toggle event.
    const sync = () => setOpen(element.open);
    const observer = new MutationObserver(sync);
    observer.observe(element, { attributes: true, attributeFilter: ['open'] });
    sync();
    return () => observer.disconnect();
  }, []);
  return <div data-testid="download-archive">
    <div className="download-entry">
      <p>{text('Choose a time range and download CSV, JSON or XLSX files.', '选择时间范围，下载 CSV、JSON 或 XLSX 文件。', 'Choisissez une période et téléchargez les fichiers CSV, JSON ou XLSX.')}</p>
      <button type="button" data-download-modal-trigger className="site-action-secondary download-entry__button" onClick={() => dialog.current?.showModal()}>
        <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 16V4m0 12-4-4m4 4 4-4M4 20h16" /></svg>
        {text('Download data', '下载数据', 'Télécharger les données')}
      </button>
    </div>
    <dialog ref={dialog} className="download-dialog download-dialog--live" data-download-confirm-dialog aria-label={text('Choose data download', '选择数据下载', 'Choisir un téléchargement')} onClick={event => { if (event.target === event.currentTarget) event.currentTarget.close(); }}>
      <div className="download-dialog__toolbar"><div className="download-dialog__header"><div>
        <p className="figure-kicker">{text('Partitioned public data', '分块公开数据', 'Données publiques partitionnées')}</p>
        <h2 className="download-dialog__title">{text('Choose a time range and format', '选择时间范围和格式', 'Choisir une période et un format')}</h2>
        <p className="download-dialog__note">{text('Download archives have their own release date and may differ from the live curves.', '下载档案有独立发布日期，可能与实时曲线的版本不同。', 'Les archives ont leur propre date de publication et peuvent différer des courbes actuelles.')}</p>
      </div><button type="button" className="download-dialog__close" data-download-modal-close aria-label={text('Close', '关闭', 'Fermer')} onClick={() => dialog.current?.close()}>×</button></div></div>
      {open && <Suspense fallback={<p className="download-dialog__feedback" role="status">{text('Loading downloads…', '正在加载下载信息…', 'Chargement des téléchargements…')}</p>}><DownloadContents dataUrl={dataUrl} language={language} /></Suspense>}
    </dialog>
  </div>;
}
