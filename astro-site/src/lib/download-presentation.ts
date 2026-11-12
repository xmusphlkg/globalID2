/** File sizes use decimal units consistently in the directory and download dialog. */
export function formatFileSize(bytes?: number): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  if (bytes >= 1_000) return `${Math.max(1, Math.round(bytes / 1_000))} KB`;
  return `${Math.round(bytes)} B`;
}

export function downloadPartRecordCount(entry: { record_count?: number; parts?: Array<{ record_count?: number; is_current?: boolean }> }): { current: number; total: number } {
  const part = entry.parts?.find(item => item.is_current) ?? entry.parts?.at(-1);
  return { current: Number(part?.record_count ?? 0), total: Number(entry.record_count ?? 0) };
}
