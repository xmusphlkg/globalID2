import { readSurveillanceDownloadIndex } from '../../../lib/surveillance-downloads.ts';
import { getFlagAssetPath } from '../../../lib/country-flag.ts';
import { downloadCatalogue } from './catalogue.ts';
import { downloadMessages } from './messages.ts';
import { initializeDownloadCatalogue } from './controller.js';

type Locale = 'en' | 'zh' | 'fr';
type Model = ReturnType<typeof downloadCatalogue>;

/** Fill the server's shared row template; never interpolate publication HTML. */
export function renderDownloadCatalogue(root: HTMLElement, model: Model, copy: ReturnType<typeof downloadMessages>, template: HTMLTableRowElement) {
  const text = (selector: string, value: string | number) => {
    const element = root.querySelector(selector);
    if (element) element.textContent = String(value);
  };
  text('[data-download-metric="countries"]', model.countries.length);
  text('[data-download-metric="diseases"]', model.diseases.length);
  text('[data-download-metric="records"]', model.compact(model.totalRecords));
  text('[data-download-metric="date"]', model.generatedAt);
  text('[data-download-kind="all"] span', model.entries.length);
  text('[data-download-kind="country"] span', model.countries.length);
  text('[data-download-kind="disease"] span', model.diseases.length);
  text('[data-download-subdivision-note]', copy.subdivisionNote);
  const subdivision = root.querySelector<HTMLElement>('[data-download-subdivisions]');
  if (subdivision) subdivision.hidden = model.subdivisionCount === 0;
  const rows = model.entries.map((entry: any, index: number) => {
    const row = template.cloneNode(true) as HTMLTableRowElement;
    row.removeAttribute('data-download-template');
    row.setAttribute('data-download-row', '');
    row.hidden = false;
    Object.assign(row.dataset, { index: String(index), kind: entry.kind, name: entry.name.toLowerCase(),
      count: String(entry.count), search: `${entry.name} ${entry.aliases} ${entry.id}`.toLowerCase() });
    for (const [key, value] of Object.entries({ name: entry.name, id: entry.id, period: entry.period, detail: entry.detail })) {
      row.querySelector(`[data-download-${key}]`)!.textContent = String(value);
    }
    row.querySelector<HTMLElement>('[data-download-country]')!.hidden = entry.kind !== 'country';
    row.querySelector<HTMLElement>('[data-download-disease]')!.hidden = entry.kind === 'country';
    const flag = row.querySelector<HTMLImageElement>('.country-flag img');
    if (flag) { flag.src = getFlagAssetPath(entry.id); flag.alt = entry.name; }
    const fallback = row.querySelector<HTMLElement>('.country-flag-fallback');
    if (fallback) { fallback.textContent = entry.id; fallback.setAttribute('aria-label', entry.name); }
    for (const format of ['csv', 'json', 'xlsx']) {
      const link = row.querySelector<HTMLAnchorElement>(`[data-download-format="${format}"]`)!;
      const file = entry.files[format];
      link.hidden = !file?.url;
      if (file?.url) link.href = file.url;
      else link.removeAttribute('href');
      link.setAttribute('aria-label', `${copy.download} ${entry.name} ${format.toUpperCase()}`);
      link.querySelector('small')!.textContent = model.bytes(file?.bytes);
    }
    row.querySelector<HTMLAnchorElement>('a.details')!.href = entry.pageHref;
    return row;
  });
  root.querySelector('tbody')!.replaceChildren(...rows);
}

export async function hydrateDownloadCatalogue(locale: Locale, initialCopy: ReturnType<typeof downloadMessages>) {
  const root = document.querySelector<HTMLElement>('.downloads-page');
  const template = root?.querySelector<HTMLTableRowElement>('[data-download-template]');
  if (!root || !template) return;
  const status = root.querySelector<HTMLElement>('[data-download-status]')!;
  const retry = root.querySelector<HTMLButtonElement>('[data-download-retry]')!;
  template.remove();
  const load = async () => {
    root.dataset.downloadState = 'loading';
    status.hidden = false;
    status.textContent = initialCopy.loading;
    retry.hidden = true;
    try {
      const publication = await readSurveillanceDownloadIndex();
      let copy = initialCopy;
      if (publication) {
        const model = downloadCatalogue(publication.index, publication.meta, locale);
        copy = downloadMessages(locale, model.subdivisionCount);
        renderDownloadCatalogue(root, model, copy, template);
        root.dataset.surveillanceVersion = publication.version;
      }
      // A manifest 404 is the only supported static-directory compatibility path.
      initializeDownloadCatalogue(locale, copy);
      root.dataset.downloadState = publication ? 'publication' : 'legacy';
      status.hidden = true;
    } catch {
      root.dataset.downloadState = 'error';
      status.textContent = initialCopy.unavailable;
      retry.hidden = false;
    }
  };
  retry.addEventListener('click', () => { void load(); });
  await load();
}
