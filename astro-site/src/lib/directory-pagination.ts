export const DIRECTORY_PAGE_SIZE = 24;

/** Keep shared links, language changes and browser back on the same area page. */
export function directoryPageFromSearch(search: string): number {
  const raw = new URLSearchParams(search).get('areas_page') ?? '';
  const value = Number(raw);
  return /^\d+$/.test(raw) && Number.isSafeInteger(value) && value > 0 ? value : 1;
}

export function syncDirectoryPage(page: number) {
  const url = new URL(window.location.href);
  if (page > 1) url.searchParams.set('areas_page', String(page));
  else url.searchParams.delete('areas_page');
  if (url.href !== window.location.href) window.history.replaceState(window.history.state, '', url);
}

/** Bound rendered cards while retaining the complete filtered catalogue. */
export function directoryPage<T>(rows: readonly T[], requestedPage = 1, pageSize = DIRECTORY_PAGE_SIZE) {
  if (!Number.isInteger(pageSize) || pageSize < 1) throw new RangeError('Directory page size must be a positive integer');
  const total = rows.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(pageCount, Math.max(1, Number.isFinite(requestedPage) ? Math.trunc(requestedPage) : 1));
  const offset = (page - 1) * pageSize;
  return { rows: rows.slice(offset, offset + pageSize), page, pageCount, total,
    start: total ? offset + 1 : 0, end: Math.min(offset + pageSize, total) };
}
