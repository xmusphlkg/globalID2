import { downloadCenterHref, downloadScopeFromSummary } from '../../lib/download-center-link';
import { pick, type Language } from '../../lib/surveillance-presentation.ts';

export default function SurveillanceDownloads({ dataUrl, language, diseaseId }: { dataUrl: string; language: Language; diseaseId?: string }) {
  return <div data-testid="download-archive" className="mt-4">
    <a data-download-center-link className="site-action-secondary inline-flex items-center gap-2 px-3 py-2"
      href={downloadCenterHref(language, { ...downloadScopeFromSummary(dataUrl), ...(diseaseId ? { disease: diseaseId } : {}) })}>
      {pick(language, 'Open download center', '进入下载中心', 'Ouvrir le centre de téléchargement')}
    </a>
  </div>;
}
