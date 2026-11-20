import { useEffect, useMemo, useRef, useState } from 'react';
import GIDSLoader from '../GIDSLoader';
import { useSurveillanceSummary } from './useSurveillanceSummary';
import GeographicAreaCards from './GeographicAreaCards';
import DirectoryPagination from './DirectoryPagination';
import { directoryPage, directoryPageFromSearch, syncDirectoryPage } from '../../lib/directory-pagination';
import { parentAreaCode } from '../../lib/surveillance-coverage';
import type { Language, Summary } from '../../lib/surveillance-presentation';
import './surveillance-directory.css';

/** Country pages need their own subdivisions, not the global filter catalogues. */
export default function SubdivisionDirectory({ language = 'en', parentCode }: { language?: Language; parentCode: string }) {
  const publication = useSurveillanceSummary('/site-data/meta.json');
  const rows = useMemo(() => {
    const saved = new Map<string, Summary>((publication.data?.countries ?? []).map((row: Summary) => [String(row.code).toUpperCase(), row]));
    return [...saved.values()].filter(row => parentAreaCode(row) === parentCode.toUpperCase());
  }, [publication.data, parentCode]);
  const [pagination, setPagination] = useState(() => ({ parentCode, page: directoryPageFromSearch(typeof window === 'undefined' ? '' : window.location.search) }));
  const grid = useRef<HTMLDivElement>(null);
  const page = directoryPage<Summary>(rows, pagination.parentCode === parentCode ? pagination.page : 1);
  useEffect(() => { if (publication.data) syncDirectoryPage(page.page); }, [page.page, publication.data]);
  const pick = (en: string, zh: string, fr: string) => language === 'zh' ? zh : language === 'fr' ? fr : en;
  if (!publication.data) return <div role="status" className="surveillance-directory">
    {publication.error ? <>
      <p className="figure-caption">{pick('Current coverage is unavailable.', '当前覆盖数据暂不可用。', 'La couverture actuelle est indisponible.')}</p>
      <button type="button" className="directory-reset" onClick={publication.retry}>{pick('Retry', '重试', 'Réessayer')}</button>
    </> : <GIDSLoader language={language} />}
  </div>;
  return <div data-surveillance-version={publication.version} className="surveillance-directory">
    <GeographicAreaCards rows={page.rows} language={language} gridRef={grid} />
    <DirectoryPagination page={page} language={language} onPage={number => { setPagination({ parentCode, page: number }); grid.current?.scrollIntoView({ block: 'start' }); }} />
    {!rows.length && <p className="figure-caption">{pick('No matching areas', '没有符合条件的区域', 'Aucune zone correspondante')}</p>}
  </div>;
}
