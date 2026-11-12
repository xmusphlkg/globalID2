import { createHash } from 'node:crypto';

/** Public, deterministic identifiers for evidence versions. The manifest is
 * an index of the current public export, not a claim that a Papers-host page
 * or an old weekly brief has the same contents. */
const SUMMARY_FIELDS = [
  'research_question', 'study_design', 'population_setting', 'main_findings',
  'public_health_relevance', 'limitations', 'gids_interpretation',
] as const;

type RecordValue = Record<string, unknown>;
const isRecord = (value: unknown): value is RecordValue =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): string | null => typeof value === 'string' && value.trim() ? value.trim() : null;
const sha256 = (value: string): string => createHash('sha256').update(value, 'utf8').digest('hex');
const canonical = (value: unknown): unknown => Array.isArray(value) ? value.map(canonical)
  : isRecord(value) ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;

/** The digest is SHA-256 of UTF-8 JSON.stringify of seven trimmed strings in
 * SUMMARY_FIELDS order. It intentionally excludes model/internal metadata.
 * main_findings_sha256 is SHA-256 of the trimmed UTF-8 finding itself. */
function summaryVersion(value: unknown): RecordValue | null {
  if (!isRecord(value)) return null;
  const fields = SUMMARY_FIELDS.map(field => text(value[field]));
  if (fields.some(field => field === null)) return null;
  return {
    summary_sha256: sha256(JSON.stringify(fields)),
    main_findings_sha256: sha256(fields[3]!),
  };
}

export function buildResearchEvidenceManifest(index: unknown, loadBrief?: (week: string) => string,
                                              listBriefWeeks?: () => string[]): RecordValue {
  if (!isRecord(index)) throw new Error('research_evidence_index_invalid');
  const items: RecordValue[] = [];
  const ids = new Set<string>();
  for (const collection of ['articles', 'preprints', 'bibliographic_articles']) {
    const rows = index[collection] ?? [];
    if (!Array.isArray(rows)) throw new Error('research_evidence_collection_invalid');
    for (const value of rows) {
      if (!isRecord(value)) throw new Error('research_evidence_article_invalid');
      const articleId = text(value.article_id), slug = text(value.slug);
      if (!articleId || !slug || ids.has(articleId)) throw new Error('research_evidence_article_identity_invalid');
      ids.add(articleId);
      const summary = isRecord(value.summary) ? value.summary : {};
      items.push({
        article_id: articleId,
        slug,
        editorial_status: text(value.editorial_status),
        integrity_status: text(value.integrity_status),
        peer_review_status: text(value.peer_review_status),
        content_tier: text(value.content_tier),
        indexable: value.indexable === true,
        detail_available: collection !== 'bibliographic_articles' && value.indexable === true,
        updated_at: text(value.updated_at),
        summaries: { en: summaryVersion(summary.en), zh: summaryVersion(summary.zh) },
      });
    }
  }
  const rawEvents = index.integrity_alerts ?? [];
  if (!Array.isArray(rawEvents)) throw new Error('research_evidence_integrity_invalid');
  const events = rawEvents.map(value => {
    if (!isRecord(value)) throw new Error('research_evidence_integrity_invalid');
    const alertId = text(value.alert_id), articleId = text(value.article_id);
    const eventType = text(value.event_type), currentStatus = text(value.current_status);
    if (!alertId || !articleId || !eventType || !currentStatus) throw new Error('research_evidence_integrity_invalid');
    return { alert_id: alertId, article_id: articleId, event_type: eventType, current_status: currentStatus,
      effective_at: text(value.effective_at), recorded_at: text(value.recorded_at),
      is_currently_public: value.is_currently_public === true };
  });
  items.sort((a, b) => String(a.article_id).localeCompare(String(b.article_id), 'en'));
  events.sort((a, b) => a.alert_id.localeCompare(b.alert_id, 'en'));
  if (new Set(events.map(event => event.alert_id)).size !== events.length) throw new Error('research_evidence_integrity_duplicate');
  const weeklyInventoryComplete = Array.isArray(index.weekly_briefs) && typeof loadBrief === 'function'
    && typeof listBriefWeeks === 'function';
  const weeks = new Set<string>();
  const weeklyBriefs = weeklyInventoryComplete ? (index.weekly_briefs as unknown[]).map(value => {
    if (!isRecord(value)) throw new Error('research_evidence_week_invalid');
    const week = text(value.week), status = text(value.brief_status);
    if (!week || !/^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$/.test(week) || !status || weeks.has(week))
      throw new Error('research_evidence_week_invalid');
    weeks.add(week);
    const bytes = loadBrief!(week);
    const brief: unknown = JSON.parse(bytes);
    if (!isRecord(brief)) throw new Error('research_evidence_week_invalid');
    for (const key of ['week', 'brief_status', 'cited_findings', 'methodology', 'byline']) {
      if (JSON.stringify(canonical(value[key])) !== JSON.stringify(canonical(brief[key])))
        throw new Error('research_evidence_week_data_mismatch');
    }
    if (!Array.isArray(value.cited_findings)) throw new Error('research_evidence_week_invalid');
    const cited = value.cited_findings.map(finding => isRecord(finding) ? text(finding.article_id) : null);
    if (cited.some(id => !id) || new Set(cited).size !== cited.length) throw new Error('research_evidence_week_invalid');
    return { week, brief_status: status, brief_sha256: sha256(bytes), cited_article_ids: cited.sort() };
  }).sort((a, b) => a.week.localeCompare(b.week, 'en')) : [];
  if (weeklyInventoryComplete) {
    const files = listBriefWeeks!();
    if (!Array.isArray(files) || files.some(week => typeof week !== 'string' || !/^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$/.test(week))
        || new Set(files).size !== files.length || files.length !== weeks.size || files.some(week => !weeks.has(week)))
      throw new Error('research_evidence_week_inventory_mismatch');
  }
  return { schema_version: 'gids.research-evidence.v1', generated_at: text(index.last_updated),
    summary_hash_algorithm: 'sha256-json-seven-trimmed-fields-v1', articles: items, integrity_events: events,
    weekly_inventory_complete: weeklyInventoryComplete, weekly_briefs: weeklyBriefs };
}
