import { downloadCenterHref, downloadScopeFromSummary } from '../../lib/download-center-link';
import { pick, type Language } from '../../lib/surveillance-presentation.ts';

export default function SurveillanceDownloads({ dataUrl, language, diseaseId }: { dataUrl: string; language: Language; diseaseId?: string }) {
  return <div data-testid="download-archive" className="surveillance-downloads">
    <a data-download-center-link className="intl-panel-action"
      href={downloadCenterHref(language, { ...downloadScopeFromSummary(dataUrl), ...(diseaseId ? { disease: diseaseId } : {}) })}>
      {pick(language, 'Open download center', '进入下载中心', 'Ouvrir le centre de téléchargement')}
    </a>
  </div>;
}
