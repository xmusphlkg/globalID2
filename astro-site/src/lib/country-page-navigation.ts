type Report = Record<string, any>;
type Route = { code: string; slug: string };

/** Index once per renderer, instead of rescanning both catalogues per locale/page. */
export function createCountryPageNavigation(reports: Iterable<Report>, routes: Iterable<Route>) {
  const reportsByCountry = new Map<string, Report[]>();
  const routesByCountry = new Map<string, Route[]>();
  for (const report of reports) {
    const code = String(report.country_code ?? '').toLowerCase();
    const group = reportsByCountry.get(code) ?? [];
    group.push(report);
    reportsByCountry.set(code, group);
  }
  for (const route of routes) {
    const code = route.code.toLowerCase();
    const group = routesByCountry.get(code) ?? [];
    group.push(route);
    routesByCountry.set(code, group);
  }
  return (country: string, prefix = '') => {
    const code = country.toLowerCase();
    const reports = reportsByCountry.get(code) ?? [];
    return {
      reports: reports.slice(0, 6),
      reportCount: reports.length,
      diseaseRoutes: Object.fromEntries((routesByCountry.get(code) ?? [])
        .map(({ slug }) => [slug, `${prefix}/countries/${code}/diseases/${slug}/`])),
    };
  };
}
