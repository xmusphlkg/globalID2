

import { type ReportDetail } from "@/features/reports/api";

import { asRecord, asArray, asString, asNullableString } from "./presentation";
export function sourceIdentity(source: Record<string, unknown>, index: number) {
  const id = asNullableString(source.source_id) ?? asNullableString(source.id);
  if (id) return `id:${id}`;
  const url = asNullableString(source.resolved_url) ?? asNullableString(source.url);
  if (url) return `url:${url}`;
  const title = asNullableString(source.title) ?? asNullableString(source.source_name);
  if (title) return `title:${title.toLowerCase()}`;
  return `index:${index}`;
}

export function referenceIndex(source: Record<string, unknown>, fallbackIndex: number) {
  const value = Number(source.citation_index);
  return Number.isFinite(value) && value > 0 ? value : fallbackIndex;
}

export function referenceUrl(source: Record<string, unknown>) {
  return asNullableString(source.resolved_url) ?? asNullableString(source.url);
}

export function referenceLinkLabel(source: Record<string, unknown>) {
  const lookup = [
    source.source_type,
    source.source_name,
    source.url,
    source.resolved_url,
  ].map((value) => asString(value).toLowerCase()).join(" ");
  if (lookup.includes("pubmed")) return "PubMed";
  if (lookup.includes("doi.org") || asNullableString(source.doi)) return "DOI";
  return "Available from";
}

export function formatReferenceCitation(source: Record<string, unknown>, fallbackIndex: number) {
  const title = asNullableString(source.title) ?? `Reference ${fallbackIndex}`;
  const sourceName = asNullableString(source.source_name) ?? asNullableString(source.source_type);
  const date = asNullableString(source.publication_date) ?? asNullableString(source.published_at) ?? asNullableString(source.fetched_at);
  const pmid = referenceUrl(source)?.match(/pubmed\.ncbi\.nlm\.nih\.gov\/(\d+)/i)?.[1];
  return [
    sourceName && sourceName.toLowerCase() !== title.toLowerCase() ? `${sourceName}.` : null,
    /[.!?。！？]$/.test(title) ? title : `${title}.`,
    date ? `${date}.` : null,
    pmid ? `PMID: ${pmid}.` : null,
  ].filter(Boolean).join(" ");
}

export function collectReportReferences(detail: ReportDetail, sectionTexts: string[]) {
  const metadata = asRecord(detail.metadata);
  const rawSources: Record<string, unknown>[] = [];

  asArray(metadata.references).map(asRecord).forEach((source) => rawSources.push(source));
  asArray(metadata.disease_cards).map(asRecord).forEach((card) => {
    const context = asRecord(card.knowledge_context);
    asArray(context.source_attribution).map(asRecord).forEach((source) => rawSources.push(source));
  });

  const markers = new Set<number>();
  sectionTexts.forEach((text) => {
    for (const match of text.matchAll(/\[(\d+)\]/g)) markers.add(Number(match[1]));
  });

  const seen = new Set<string>();
  const deduped = rawSources.filter((source, index) => {
    if (Object.keys(source).length === 0) return false;
    const key = sourceIdentity(source, index);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  const normalized = deduped.map((source, index) => ({
    ...source,
    citation_index: referenceIndex(source, index + 1),
  }));
  normalized.sort((a, b) => referenceIndex(a, Number.MAX_SAFE_INTEGER) - referenceIndex(b, Number.MAX_SAFE_INTEGER));
  return markers.size > 0
    ? normalized.filter((source, index) => markers.has(referenceIndex(source, index + 1)) || asArray(metadata.references).length > 0)
    : normalized;
}

export function linkCitationMarkers(content: string, references: Record<string, unknown>[]) {
  if (!content || references.length === 0) return content;
  const valid = new Set(references.map((source, index) => referenceIndex(source, index + 1)));
  return content.replace(/\[(\d+)\](?!\()/g, (match, value: string) => {
    const index = Number(value);
    return valid.has(index) ? `[[${index}]](#report-ref-${index})` : match;
  });
}
