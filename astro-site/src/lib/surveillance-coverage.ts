import { sourceEntries, type Summary } from './surveillance-presentation.ts';

export const isSubdivision = (row: Summary) => row.location_type === 'subdivision' || Boolean(row.parent_code) || String(row.code ?? '').includes('-');
// Compatibility for shells built before parent metadata was corrected. New
// data exports resolve the relationship from the geography registry.
export const parentAreaCode = (row: Summary): string | null => row.parent_code ? String(row.parent_code).toUpperCase() : row.location_type === 'subdivision' && /^[A-Z]{2}-[A-Z0-9-]+$/i.test(String(row.code ?? '')) ? String(row.code).split('-')[0].toUpperCase() : null;
export const hasData = (row: Summary) => row.data_available !== false && (Number(row.record_count ?? row.disease_count ?? 0) > 0 || row.data_available === true);
export const nationalRows = (data: Summary) => (data.countries ?? []).filter((row: Summary) => !isSubdivision(row) && hasData(row));
export const subdivisionRows = (data: Summary) => (data.countries ?? []).filter((row: Summary) => isSubdivision(row) && hasData(row));
export const officialSourceCount = (data: Summary) => new Set(nationalRows(data).flatMap((row: Summary) => sourceEntries(row.source_info).map(source => source.label || source.url))).size;
