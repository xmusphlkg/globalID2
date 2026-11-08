import { sourceEntries, type Summary } from './surveillance-presentation.ts';

export const isSubdivision = (row: Summary) => row.location_type === 'subdivision' || Boolean(row.parent_code) || String(row.code ?? '').includes('-');
export const hasData = (row: Summary) => row.data_available !== false && (Number(row.record_count ?? row.disease_count ?? 0) > 0 || row.data_available === true);
export const nationalRows = (data: Summary) => (data.countries ?? []).filter((row: Summary) => !isSubdivision(row) && hasData(row));
export const subdivisionRows = (data: Summary) => (data.countries ?? []).filter((row: Summary) => isSubdivision(row) && hasData(row));
export const officialSourceCount = (data: Summary) => new Set(nationalRows(data).flatMap((row: Summary) => sourceEntries(row.source_info).map(source => source.label || source.url))).size;
