import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  buildPublishableReports,
  normalizeReportRouteSegment,
  type PublishableReport,
} from './report-routes';

const reportsIndexPath = resolve('./src/data/reports/index.json');
const cacheDuringBuild = process.env.NODE_ENV === 'production';
const jsonCache = new Map<string, unknown | null>();
let publishableReportsCache: PublishableReport[] | undefined;

export function readJsonFile(path: string): unknown | null {
  if (cacheDuringBuild && jsonCache.has(path)) return jsonCache.get(path) ?? null;
  if (!existsSync(path)) return null;

  try {
    const value = JSON.parse(readFileSync(path, 'utf-8'));
    if (cacheDuringBuild) jsonCache.set(path, value);
    return value;
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
  if (cacheDuringBuild && publishableReportsCache) return publishableReportsCache;
  const reports = buildPublishableReports(readReportsIndex(), loadReportDetail);
  if (cacheDuringBuild) publishableReportsCache = reports;
  return reports;
}
