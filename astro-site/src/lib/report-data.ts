import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  buildPublishableReports,
  normalizeReportRouteSegment,
  type PublishableReport,
} from './report-routes';

const reportsIndexPath = resolve('./src/data/reports/index.json');

export function readJsonFile(path: string): unknown | null {
  if (!existsSync(path)) return null;

  try {
    return JSON.parse(readFileSync(path, 'utf-8'));
  } catch (error) {
    console.warn(`Unable to read JSON data at ${path}`, error);
    return null;
  }
}

export function readReportsIndex(): unknown {
  return readJsonFile(reportsIndexPath) ?? [];
}

export function loadReportDetail(id: string): unknown | null {
  const safeId = normalizeReportRouteSegment(id);
  if (!safeId) return null;
  return readJsonFile(resolve(`./src/data/reports/${safeId}.json`));
}

export function readPublishableReports(): PublishableReport[] {
  return buildPublishableReports(readReportsIndex(), loadReportDetail);
}
