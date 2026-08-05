import type { ReactNode } from "react";
import { ExternalLink } from "lucide-react";

function normalizeText(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return null;
}
function asObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export type CitationReference = {
  citationIndex: number;
  sourceId: number | null;
  label: string;
  title: string | null;
  url: string | null;
  linkLabel: string;
  citationText: string;
  tooltip: string;
  key: string;
};

function numericValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && /^\d+$/.test(value.trim())) return Number(value.trim());
  return null;
}

function stripTrailingPeriod(value: string): string {
  return value.replace(/[.。]+$/g, "").trim();
}

function sentence(value: string | null | undefined): string | null {
  const text = normalizeText(value);
  if (!text) return null;
  return /[.!?。！？]$/.test(text) ? text : `${stripTrailingPeriod(text)}.`;
}

function recordValue(record: Record<string, unknown>, key: string): string | null {
  const direct = normalizeText(record[key]);
  if (direct) return direct;
  const metadata = asObject(record.metadata);
  return metadata ? normalizeText(metadata[key]) : null;
}

function referenceUrl(record: Record<string, unknown>): string | null {
  return normalizeText(record.resolved_url) ?? normalizeText(record.url);
}

function referenceLookup(record: Record<string, unknown>): string {
  return [
    record.source_type,
    record.source_name,
    recordValue(record, "provider"),
    recordValue(record, "content_kind"),
    referenceUrl(record),
  ]
    .map((value) => normalizeText(value)?.toLowerCase())
    .filter(Boolean)
    .join(" ");
}

function doiFromUrl(url: string | null): string | null {
  if (!url) return null;
  let decoded = url;
  try {
    decoded = decodeURIComponent(url);
  } catch {
    decoded = url;
  }
  const match = decoded.match(/(?:doi\.org\/|doi:\s*)(10\.\d{4,9}\/[^\s?#]+)/i);
  return match ? stripTrailingPeriod(match[1].replace(/[),;]+$/g, "")) : null;
}

function pmidFromUrl(url: string | null): string | null {
  if (!url) return null;
  const match = url.match(/pubmed\.ncbi\.nlm\.nih\.gov\/(\d+)/i) ?? url.match(/\bPMID:?\s*(\d+)\b/i);
  return match ? match[1] : null;
}

function referenceDoi(record: Record<string, unknown>): string | null {
  const doi = recordValue(record, "doi") ?? doiFromUrl(referenceUrl(record));
  if (!doi) return null;
  return doi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "").replace(/^doi:\s*/i, "").trim();
}

function referencePmid(record: Record<string, unknown>): string | null {
  return recordValue(record, "pmid") ?? pmidFromUrl(referenceUrl(record));
}

function isPubMedReference(record: Record<string, unknown>): boolean {
  const lookup = referenceLookup(record);
  return lookup.includes("pubmed") || Boolean(referencePmid(record));
}

function isScholarlyReference(record: Record<string, unknown>): boolean {
  if (isPubMedReference(record)) return false;
  const lookup = referenceLookup(record);
  return Boolean(referenceDoi(record)) ||
    lookup.includes("crossref") ||
    lookup.includes("scholarly_metadata") ||
    Boolean(recordValue(record, "container_title")) ||
    Boolean(recordValue(record, "journal"));
}

function authorPart(firstAuthor: string | null): string | null {
  if (!firstAuthor) return null;
  const author = stripTrailingPeriod(firstAuthor);
  return /\bet\s+al\.?$/i.test(author) ? `${author}.` : `${author} et al.`;
}

function referenceSourceName(record: Record<string, unknown>): string {
  const sourceType = normalizeText(record.source_type)?.toLowerCase() ?? "";
  const sourceName = normalizeText(record.source_name);
  const lookup = (sourceName ?? sourceType).toLowerCase();
  if (lookup === "who" || sourceType === "who" || sourceType === "who_don") return "World Health Organization";
  if (lookup.includes("wikidata") || sourceType === "wikidata") return "Wikidata contributors";
  if (lookup.includes("wikipedia") || sourceType === "wikipedia") return "Wikipedia contributors";
  if (lookup.includes("pubmed") || sourceType === "pubmed") return "National Library of Medicine";
  if (lookup.includes("msd") || sourceType === "msd") return "MSD Manual";
  return sourceName ?? normalizeText(record.source_type) ?? "Unknown source";
}

function referenceContainer(record: Record<string, unknown>): string | null {
  const sourceType = normalizeText(record.source_type)?.toLowerCase() ?? "";
  if (sourceType === "pubmed") return "PubMed";
  if (sourceType === "wikidata") return "Wikidata";
  if (sourceType === "wikipedia") return "Wikipedia";
  if (sourceType === "who_don") return "Disease Outbreak News";
  return null;
}

function referenceDate(record: Record<string, unknown>): string | null {
  const raw =
    normalizeText(record.publication_date) ??
    normalizeText(record.published_at) ??
    normalizeText(record.fetched_at) ??
    normalizeText(record.accessed_at) ??
    normalizeText(record.updated_at);
  if (!raw) return null;
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleDateString("en-GB", { year: "numeric", month: "short", day: "numeric" });
}

function formatReferenceCitation(record: Record<string, unknown>, citationIndex: number): string {
  if (isPubMedReference(record)) {
    const firstAuthor = recordValue(record, "first_author") ?? recordValue(record, "author");
    const title = normalizeText(record.title) ?? `PubMed record ${citationIndex}`;
    const journal = recordValue(record, "journal") ?? recordValue(record, "container_title");
    const pubDate =
      recordValue(record, "pub_date") ??
      recordValue(record, "publication_date") ??
      recordValue(record, "published_at");
    const pmid = referencePmid(record);
    const doi = referenceDoi(record);
    return [
      authorPart(firstAuthor),
      sentence(title),
      !firstAuthor && !journal ? "PubMed indexed record." : null,
      sentence(journal),
      sentence(pubDate),
      pmid ? `PMID: ${pmid}.` : null,
      doi ? `doi: ${doi}.` : null,
    ].filter(Boolean).join(" ");
  }

  if (isScholarlyReference(record)) {
    const title = normalizeText(record.title) ?? `Scholarly reference ${citationIndex}`;
    const container = recordValue(record, "container_title") ?? recordValue(record, "journal");
    const year =
      recordValue(record, "year") ??
      recordValue(record, "pub_date") ??
      recordValue(record, "publication_date") ??
      recordValue(record, "published_at");
    const doi = referenceDoi(record);
    return [
      sentence(title),
      container ? sentence(container) : "Scholarly DOI record.",
      sentence(year),
      doi ? `doi: ${doi}.` : null,
    ].filter(Boolean).join(" ");
  }

  const author = referenceSourceName(record);
  const title = normalizeText(record.title);
  const label = title && title.toLowerCase() !== author.toLowerCase()
    ? stripTrailingPeriod(title)
    : `Reference ${citationIndex}`;
  const container = referenceContainer(record);
  const citedDate = referenceDate(record);
  const parts = [`${stripTrailingPeriod(author)}. ${label} [Internet]`];
  if (container && container.toLowerCase() !== label.toLowerCase()) {
    parts.push(stripTrailingPeriod(container));
  }
  if (citedDate) {
    parts.push(`cited ${citedDate}`);
  }
  return `${parts.join(". ")}.`;
}

function referenceLinkLabel(record: Record<string, unknown>): string {
  if (isPubMedReference(record)) return "PubMed";
  if (referenceDoi(record)) return "DOI";
  return "Available from";
}

export function citationReferencesFromAttribution(attribution: unknown[]): CitationReference[] {
  return attribution
    .map((item, index) => {
      const record = asObject(item);
      if (!record) return null;
      const citationIndex = numericValue(record.citation_index) ?? index + 1;
      const sourceId = numericValue(record.source_id) ?? numericValue(record.id);
      const sourceName = normalizeText(record.source_name) ?? normalizeText(record.source_type);
      const title = normalizeText(record.title);
      const url = referenceUrl(record);
      const citationText = formatReferenceCitation(record, citationIndex);
      const label = title ?? sourceName ?? (sourceId ? `Source ${sourceId}` : `Reference ${citationIndex}`);
      const tooltip = [citationText, url].filter(Boolean).join(" · ");
      return {
        citationIndex,
        sourceId,
        label,
        title,
        url,
        linkLabel: referenceLinkLabel(record),
        citationText,
        tooltip,
        key: `${citationIndex}-${sourceId ?? "no-source"}-${url ?? label}`,
      };
    })
    .filter((item): item is CitationReference => Boolean(item))
    .sort((a, b) => a.citationIndex - b.citationIndex);
}

export function CitationText({
  text,
  references,
}: {
  text: string;
  references: CitationReference[];
}) {
  if (!text) return null;
  const refByNumber = new Map<number, CitationReference>();
  references.forEach((reference) => {
    refByNumber.set(reference.citationIndex, reference);
    if (reference.sourceId !== null && !refByNumber.has(reference.sourceId)) {
      refByNumber.set(reference.sourceId, reference);
    }
  });

  const parts: ReactNode[] = [];
  const citationPattern = /\[(\d+)\]/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = citationPattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const citationNumber = Number(match[1]);
    const reference = refByNumber.get(citationNumber);
    if (reference) {
      parts.push(
        <sup key={`${match.index}-${citationNumber}`} className="mx-0.5 align-super text-[10px] leading-none">
          <a
            href={reference.url ?? undefined}
            target={reference.url ? "_blank" : undefined}
            rel={reference.url ? "noreferrer" : undefined}
            title={reference.tooltip}
            className="rounded-[3px] border border-blue-200 bg-blue-50 px-1 font-semibold text-blue-700 no-underline hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300"
          >
            [{reference.citationIndex}]
          </a>
        </sup>,
      );
    } else {
      parts.push(match[0]);
    }
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return <>{parts}</>;
}

export function BriefField({
  label,
  value,
  compact = false,
  references = [],
}: {
  label: string;
  value?: string | null;
  compact?: boolean;
  references?: CitationReference[];
}) {
  const text = value?.trim() ?? "";
  if (!text) return null;

  return (
    <div className={compact ? "space-y-1" : "space-y-1.5"}>
      <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {label}
      </p>
      <p className="whitespace-pre-line text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
        <CitationText text={text} references={references} />
      </p>
    </div>
  );
}

export function BriefDisclosureField({
  label,
  value,
  references = [],
}: {
  label: string;
  value?: string | null;
  references?: CitationReference[];
}) {
  const text = value?.trim() ?? "";
  if (!text) return null;

  return (
    <details className="rounded-tremor-default border border-tremor-border bg-tremor-background-muted/30 dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/20">
      <summary className="cursor-pointer list-none px-3 py-2 text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {label}
      </summary>
      <p className="border-t border-tremor-border px-3 py-3 text-sm leading-6 text-tremor-content dark:border-dark-tremor-border dark:text-dark-tremor-content">
        <CitationText text={text} references={references} />
      </p>
    </details>
  );
}
