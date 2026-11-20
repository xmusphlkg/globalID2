import type { Language } from '../../lib/surveillance-presentation';

export default function DirectoryPagination({ page, language, onPage }: {
  page: { page: number; pageCount: number; start: number; end: number; total: number };
  language: Language;
  onPage: (page: number) => void;
}) {
  const pick = (en: string, zh: string, fr: string) => language === 'zh' ? zh : language === 'fr' ? fr : en;
  return page.pageCount > 1 && <nav className="directory-pagination" aria-label={pick('Geographic area pages', '地理区域分页', 'Pages des zones géographiques')}>
    <p aria-live="polite">{pick(`Showing ${page.start}–${page.end} of ${page.total} areas`, `显示第 ${page.start}–${page.end} 项，共 ${page.total} 个区域`, `Zones ${page.start}–${page.end} sur ${page.total}`)}</p>
    <div><button type="button" disabled={page.page === 1} onClick={() => onPage(page.page - 1)}>{pick('Previous', '上一页', 'Précédent')}</button>
      <span>{pick(`Page ${page.page} of ${page.pageCount}`, `第 ${page.page} / ${page.pageCount} 页`, `Page ${page.page} sur ${page.pageCount}`)}</span>
      <button type="button" disabled={page.page === page.pageCount} onClick={() => onPage(page.page + 1)}>{pick('Next', '下一页', 'Suivant')}</button></div>
  </nav>;
}
