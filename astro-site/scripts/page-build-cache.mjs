import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileHash } from './file-fingerprints.mjs';
import { createSourceDependencyFingerprinter } from './source-dependencies.mjs';
import { selectDirectoryCandidates } from '../src/lib/directory-facts.ts';
import { openDirectoryBuildCache, directoryFactsVersion } from './directory-build-cache.mjs';
import { hasCountryDataSnapshot } from '../src/lib/country-coverage.ts';
import { toSeoSlug } from '../src/lib/seo.ts';
import { buildPublishableReports } from '../src/lib/report-routes.ts';
import { addSituationPages } from './situation-build-cache.mjs';
import { prerenderSituationJson } from '../src/lib/situation-build-mode.ts';
import { cacheRoot, siteRoot, sharedFingerprint, planResearchBuild, finalizeResearchSnapshot, reportDiseaseTemplate } from './research-build-cache.mjs';

export const pageManifestPath = resolve(cacheRoot, 'site-page-manifest.json');
export const pagePendingPath = resolve(cacheRoot, 'site-page-pending.json');
export const pagePlanPath = resolve(cacheRoot, 'site-page-plan.json');
export const sitePageCacheRoot = resolve(cacheRoot, 'site-pages');
const sha = value => createHash('sha256').update(value).digest('hex');
const json = (path, fallback = null) => existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : fallback;
const hashFile = path => existsSync(path) ? fileHash(path) : 'missing';
const localePaths = path => ['', 'zh/', 'fr/'].map(prefix => `${prefix}${path}/index.html`);
const safeSegment = value => {
  const segment = String(value ?? '');
  if (!/^[A-Za-z0-9][A-Za-z0-9_()\-]*$/.test(segment)) throw new Error(`Unsafe page cache segment: ${segment}`);
  return segment;
};

export function pageSourceSnapshot({ dataRoot = resolve(siteRoot, 'src/data'), shared = sharedFingerprint(), reportDiseaseTemplateHash = hashFile(reportDiseaseTemplate), directoryCachePath, directoryStats, env = process.env } = {}) {
  const graph = createSourceDependencyFingerprinter({ siteRoot });
  const templates = new Map();
  const routeTemplates = {
    country: 'countries/[country]/index.astro',
    'country-disease': 'countries/[country]/diseases/[disease].astro',
    disease: 'diseases/[disease].astro',
    'report-archive': 'countries/[country]/reports/index.astro',
    report: 'countries/[country]/reports/[id].astro',
    'report-disease': 'countries/[country]/reports/[id]/[disease].astro',
    'research:countries': 'research/countries/[code].astro',
    'research:diseases': 'research/diseases/[slug].astro',
    'research:topics': 'research/topics/[topic].astro',
    'research:weekly': 'research/weekly/[week].astro',
    'situation-week': 'situation/weekly/[period].astro',
    'situation-month': 'situation/monthly/[period].astro',
    'situation-outbreak': 'situation/outbreaks/[id].json.ts',
  };
  const pages = {};
  const add = (key, path, dependencies, { locales = ['', 'fr/', 'zh/'], paths } = {}) => {
    if (pages[key]) throw new Error(`Duplicate page cache key: ${key}`);
    const group = key.startsWith('research:') ? key.split(':').slice(0, 2).join(':') : key.split(':')[0];
    if (!templates.has(group)) {
      const route = routeTemplates[group];
      if (!route) throw new Error(`Missing template dependencies for ${group}`);
      templates.set(group, graph(locales.map(prefix => `src/pages/${prefix}${route}`)));
    }
    pages[key] = { fingerprint: sha(JSON.stringify([templates.get(group), ...dependencies])), paths: paths ?? localePaths(path) };
  };
  addSituationPages({ dataRoot, add, hashFile, env });
  const routes = json(resolve(dataRoot, 'research/routes.json'), {});
  const facets = routes.facets ?? json(resolve(dataRoot, 'research/index.json'), {}).facets ?? {};
  for (const [group, facetGroup] of Object.entries({ countries: 'countries', diseases: 'diseases', topics: 'topics', weekly: 'weeks' })) {
    for (const facet of facets[facetGroup] ?? []) {
      const slug = safeSegment(group === 'weekly' ? facet.week : facet.slug);
      const file = group === 'diseases' ? safeSegment(String(facet.disease_id).toLowerCase()) : slug;
      add(`research:${group}:${slug}`, `research/${group}/${slug}`, [facet, hashFile(resolve(dataRoot, 'research', group, `${file}.json`))]);
    }
  }

  const countries = json(resolve(dataRoot, 'meta.json'), {}).countries ?? [];
  const diseases = json(resolve(dataRoot, 'diseases/index.json'), []);
  const downloads = json(resolve(dataRoot, 'downloads.json'), {});
  const directory = openDirectoryBuildCache(dataRoot, { path: directoryCachePath });
  const candidates = selectDirectoryCandidates(countries, code => directory.country(safeSegment(code)));
  const alertsDir = resolve(dataRoot, 'situation/international');
  const alertHash = path => {
    const value = json(path);
    // The panel reads schema_version and alerts, never the export's as_of timestamp.
    return sha(JSON.stringify(value ? [value.schema_version, value.alerts] : null));
  };
  const reportSummaries = json(resolve(dataRoot, 'reports/index.json'), []);
  for (const country of countries.filter(hasCountryDataSnapshot)) {
    const code = safeSegment(country.code).toLowerCase();
    const alertKeys = directory.country(code)?.alertKeys ?? [code.toUpperCase()];
    const countryAlerts = alertKeys.map(key => [key, alertHash(resolve(alertsDir, 'by-country', `${key}.json`))]);
    add(`country:${code}`, `countries/${code}`, [hashFile(resolve(dataRoot, 'countries', `${code}.json`)),
      countries.filter(entry => entry.parent_code === code.toUpperCase() && hasCountryDataSnapshot(entry)),
      candidates.filter(entry => entry.code === code).map(entry => entry.slug).sort(),
      reportSummaries.filter(entry => entry.country_code?.toLowerCase() === code).slice(0, 6), countryAlerts,
      downloads.countries?.find(entry => entry.code?.toLowerCase() === code) ?? null]);
    for (const candidate of candidates.filter(entry => entry.code === code)) {
      const slug = safeSegment(candidate.slug);
      const diseaseId = candidate.diseaseId;
      add(`country-disease:${code}:${slug}`, `countries/${code}/diseases/${slug}`, [
        hashFile(resolve(dataRoot, 'countries', `${code}.json`)), countryAlerts,
        hashFile(resolve(dataRoot, 'research/diseases', `${safeSegment(diseaseId || slug).toLowerCase()}.json`)),
        downloads.countries?.find(entry => entry.code?.toLowerCase() === code) ?? null,
      ]);
    }
  }

  const publishableReports = buildPublishableReports(reportSummaries, id => (
    json(resolve(dataRoot, 'reports', `${id}.json`))
  ));
  const diseaseIndexHash = hashFile(resolve(dataRoot, 'diseases/index.json'));
  const reportCountries = [...new Set(publishableReports.map(report => report.country))].sort();
  for (const country of reportCountries) {
    const reportsForCountry = publishableReports
      .filter(report => report.country === country)
      .map(report => report.summary);
    add(`report-archive:${country}`, `countries/${country}/reports`, [reportsForCountry,
      hashFile(resolve(dataRoot, 'countries', `${country}.json`))]);
  }
  for (const report of publishableReports) {
    const id = safeSegment(report.id);
    const country = safeSegment(report.country).toLowerCase();
    const detailPath = resolve(dataRoot, 'reports', `${id}.json`);
    const detailHash = hashFile(detailPath);
    const countryHash = hashFile(resolve(dataRoot, 'countries', `${country}.json`));
    const countryDownload = downloads.countries?.find(entry => entry.code?.toLowerCase() === country) ?? null;
    // Overview pages render links to the adjacent reports in creation order.
    // A new report must invalidate its neighbor even when that neighbor's own
    // data is unchanged.
    const ordered = publishableReports.filter(entry => entry.country === country)
      .map(entry => entry.summary)
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    const position = ordered.findIndex(entry => String(entry.id) === id);
    const neighbors = [ordered[position - 1] ?? null, ordered[position + 1] ?? null];
    add(`report:${country}:${id}`, `countries/${country}/reports/${id}`,
      [report.summary, detailHash, countryHash, countryDownload, neighbors]);

    const detail = json(detailPath, {});
    const document = detail.report_document_v4 ?? detail.metadata?.report_document_v4 ?? detail;
    const directory = Array.isArray(document?.disease_directory) ? document.disease_directory : [];
    for (const slugValue of report.diseaseSlugs) {
      const slug = safeSegment(slugValue);
      const row = directory.find(value => String(value?.slug ?? '').trim() === slugValue);
      const diseaseId = String(row?.disease_id ?? '').toLowerCase();
      const diseaseFileHash = diseaseId
        ? hashFile(resolve(dataRoot, 'diseases', `${safeSegment(diseaseId)}.json`))
        : 'missing';
      const download = downloads.diseases?.find(entry => entry.slug === slug) ?? null;
      add(`report-disease:${country}:${id}:${slug}`,
        `countries/${country}/reports/${id}/${slug}`,
        [detailHash, countryHash, diseaseIndexHash, diseaseFileHash, download, reportDiseaseTemplateHash]);
    }
  }

  for (const disease of diseases) {
    const id = safeSegment(disease.disease_id);
    directory.disease(id);
    const slug = safeSegment(toSeoSlug(disease.slug) || disease.slug);
    add(`disease:${id}`, `diseases/${slug}`, [countries, candidates.filter(entry => entry.slug === slug).map(entry => entry.code).sort(), disease,
      hashFile(resolve(dataRoot, 'diseases', `${id.toLowerCase()}.json`)),
      hashFile(resolve(dataRoot, 'disease-knowledge', `${id.toLowerCase()}.json`)),
      hashFile(resolve(dataRoot, 'research/diseases', `${id.toLowerCase()}.json`)),
      alertHash(resolve(alertsDir, 'by-disease', `${id}.json`)),
      downloads.diseases?.find(entry => entry.disease_id === id || entry.slug === slug || entry.id === id.toLowerCase()) ?? null]);
  }
  directory.save();
  if (directoryStats) Object.assign(directoryStats, directory.stats);
  // Reuse the bundle integrity planner, including all three locale outputs.
  return { schema: 1, shared: sha(`${shared}:${hashFile(new URL(import.meta.url))}:${hashFile(new URL('./situation-build-cache.mjs', import.meta.url))}:situation-json=${prerenderSituationJson(env)}:${directoryFactsVersion()}`), articles: pages };
}

export function planSitePages(snapshot, incremental) {
  let previous = null;
  try { previous = json(pageManifestPath); } catch { /* damaged cache must rebuild */ }
  return planResearchBuild({ snapshot, incremental, previous, outputRoot: sitePageCacheRoot });
}

export function writePagePending(plan) {
  writeFileSync(pagePendingPath, JSON.stringify({ ...plan.snapshot, reusedKeys: plan.reused }));
}

export function finalizeSitePages() {
  finalizeResearchSnapshot(json(pagePendingPath), {
    targetManifest: pageManifestPath, sourcePending: pagePendingPath, pageCache: sitePageCacheRoot, label: 'page-cache',
  });
}
