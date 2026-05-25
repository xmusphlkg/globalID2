"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge as UiStatusBadge } from "@/components/ui/StatusBadge";
import {
  type DiseaseKnowledgeDetailBrief,
  type DiseaseKnowledgeDetailSource,
  type StartDiseaseKnowledgeTaskResult,
  useDiseaseKnowledgeCatalogue,
  useDiseaseKnowledgeDetail,
  useStartDiseaseKnowledgeTasks,
} from "@/features/ai/api";
import { useTaskWebSocket } from "@/features/operations/tasks/api";
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CheckSquare2,
  Eye,
  ExternalLink,
  FlaskConical,
  Loader2,
  RefreshCcw,
  Search,
  ShieldCheck,
  Square,
  ListChecks,
} from "lucide-react";
import { Badge, Color } from "@tremor/react";

type KnowledgeStatusFilter = "all" | "published" | "requires_review" | "fallback";
type RefreshPriority = "low" | "normal" | "high" | "urgent";
type RefreshGenerator = "ai" | "auto" | "template";
type SourceGroup = "who" | "search" | "wikidata" | "wikipedia" | "pubmed" | "msd";
type DetailTab = "briefs" | "sources" | "meta";

const SOURCE_GROUPS: Array<{
  value: SourceGroup;
  label: string;
  note: string;
  color: Color;
}> = [
    {
      value: "who",
      label: "WHO",
      note: "health topics, fact sheets, and outbreak news",
      color: "teal",
    },
    {
      value: "search",
      label: "Search discovery",
      note: "trusted web discovery across CDC, NIH, WHO, BMJ, MSD, and Wikipedia",
      color: "indigo",
    },
    {
      value: "wikidata",
      label: "Wikidata",
      note: "structured identifiers and aliases",
      color: "violet",
    },
    {
      value: "wikipedia",
      label: "Wikipedia",
      note: "article text and section structure",
      color: "sky",
    },
    {
      value: "pubmed",
      label: "PubMed",
      note: "review article abstracts for supplementary knowledge",
      color: "rose",
    },
    {
      value: "msd",
      label: "MSD metadata",
      note: "links and metadata only, not public text",
      color: "amber",
    },
  ];

const inputClass =
  "h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

function ActionButton({
  children,
  icon,
  tone = "neutral",
  disabled,
  onClick,
  type = "button",
  className,
}: {
  children: ReactNode;
  icon?: ReactNode;
  tone?: "neutral" | "primary" | "danger";
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
  className?: string;
}) {
  const toneClass =
    tone === "primary"
      ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted hover:bg-tremor-brand/90"
      : tone === "danger"
        ? "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300"
        : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle";

  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center justify-center gap-2 rounded-tremor-default border px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-55",
        toneClass,
        className,
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "app-panel p-4",
        className,
      )}
    >
      {children}
    </section>
  );
}

function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

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

function statusColor(status: string): Color {
  switch (status) {
    case "published":
      return "emerald";
    case "requires_review":
      return "amber";
    case "draft":
      return "blue";
    case "fallback":
      return "slate";
    default:
      return "slate";
  }
}

function briefStatusColor(status: string): Color {
  switch (status) {
    case "published":
      return "emerald";
    case "requires_review":
      return "amber";
    case "draft":
      return "blue";
    default:
      return "slate";
  }
}

function reviewStatusColor(status: string): Color {
  switch (status) {
    case "approved":
      return "emerald";
    case "requires_review":
      return "amber";
    case "pending":
      return "blue";
    case "stale":
      return "slate";
    case "rejected":
    case "error":
      return "rose";
    default:
      return "slate";
  }
}

function sourceTypeColor(sourceType: string): Color {
  switch (sourceType) {
    case "who":
    case "who_don":
      return "teal";
    case "web_search":
      return "indigo";
    case "wikidata":
      return "violet";
    case "wikipedia":
      return "sky";
    case "pubmed":
      return "rose";
    case "msd":
      return "amber";
    default:
      return "slate";
  }
}

function fieldValue(value: unknown): string {
  const text = normalizeText(value);
  return text ?? "—";
}

type CitationReference = {
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

function citationReferencesFromAttribution(attribution: unknown[]): CitationReference[] {
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

function CitationText({
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

function BriefField({
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

function BriefDisclosureField({
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

function SourceTraceItem({
  source,
  lang,
}: {
  source: DiseaseKnowledgeDetailSource;
  lang: "en" | "zh";
}) {
  const sourceTitle = normalizeText(source.title) ?? source.source_name;
  const sourceSections = Array.isArray(source.content_sections) ? source.content_sections : [];

  const sectionLabels = sourceSections
    .map((section) => {
      const record = asObject(section);
      return normalizeText(record?.heading) ?? normalizeText(record?.title) ?? normalizeText(record?.name);
    })
    .filter((item): item is string => !!item);

  return (
    <details className="rounded-tremor-default border border-tremor-border bg-tremor-background/80 dark:border-dark-tremor-border dark:bg-dark-tremor-background/70">
      <summary className="cursor-pointer list-none px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="truncate text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {source.source_name}
              </p>
              <Badge color={sourceTypeColor(source.source_type)}>{source.source_type}</Badge>
              <Badge color={reviewStatusColor(source.review_status)}>{source.review_status}</Badge>
            </div>
            <p className="mt-1 line-clamp-2 text-xs text-tremor-content dark:text-dark-tremor-content">
              {sourceTitle}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {source.resolved_url ? (
              <a
                href={source.resolved_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 rounded-tremor-default border border-tremor-border px-2.5 py-1 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
                onClick={(e) => e.stopPropagation()}
              >
                <ExternalLink className="h-3.5 w-3.5" />
                URL
              </a>
            ) : null}
            <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {formatDateTime(source.fetched_at)}
            </span>
          </div>
        </div>
      </summary>
      <div className="border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "来源信息" : "Source metadata"}
            </p>
            <div className="mt-2 space-y-1 text-xs text-tremor-content dark:text-dark-tremor-content">
              <div><span className="font-medium">Source:</span> {source.source_name}</div>
              <div><span className="font-medium">Type:</span> {source.source_type}</div>
              <div><span className="font-medium">Status:</span> {source.status}</div>
              <div><span className="font-medium">Language:</span> {source.language}</div>
              <div><span className="font-medium">Review:</span> {source.review_status}</div>
            </div>
          </div>
          <div className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "链接与标题" : "Link and title"}
            </p>
            <div className="mt-2 space-y-1 text-xs text-tremor-content dark:text-dark-tremor-content">
              <div className="break-all"><span className="font-medium">URL:</span> {source.url}</div>
              {source.resolved_url && source.resolved_url !== source.url ? (
                <div className="break-all"><span className="font-medium">Resolved:</span> {source.resolved_url}</div>
              ) : null}
              <div className="break-words"><span className="font-medium">Title:</span> {sourceTitle}</div>
              {source.license ? <div className="break-words"><span className="font-medium">License:</span> {source.license}</div> : null}
            </div>
          </div>
        </div>

        {sectionLabels.length > 0 ? (
          <div className="mt-3 rounded-tremor-default border border-dashed border-tremor-border px-3 py-2 dark:border-dark-tremor-border">
            <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "解析章节" : "Parsed sections"}
            </p>
            <p className="mt-1.5 text-xs text-tremor-content dark:text-dark-tremor-content">
              {sectionLabels.join(" · ")}
            </p>
          </div>
        ) : null}

        {source.raw_excerpt ? (
          <div className="mt-3 rounded-tremor-default border border-dashed border-tremor-border bg-tremor-background-muted/40 px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/20">
            <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "摘要" : "Excerpt"}
            </p>
            <p className="mt-1.5 whitespace-pre-line text-xs leading-6 text-tremor-content dark:text-dark-tremor-content">
              {source.raw_excerpt}
            </p>
          </div>
        ) : null}

        {source.content_text ? (
          <div className="mt-3 rounded-tremor-default border border-dashed border-tremor-border bg-white px-3 py-2 dark:border-dark-tremor-border dark:bg-black/10">
            <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "正文" : "Full text"}
            </p>
            <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap break-words font-sans text-xs leading-6 text-tremor-content dark:text-dark-tremor-content">
              {source.content_text}
            </pre>
          </div>
        ) : null}
      </div>
    </details>
  );
}

function BriefCard({
  brief,
  lang,
}: {
  brief: DiseaseKnowledgeDetailBrief;
  lang: "en" | "zh";
}) {
  const attribution = Array.isArray(brief.source_attribution) ? brief.source_attribution : [];
  const citationReferences = citationReferencesFromAttribution(attribution);

  const definition = brief.definition ?? brief.brief;
  const clinical = brief.clinical_features ?? brief.clinical_summary;
  const definitionText = definition?.trim();
  const briefText = brief.brief.trim();
  const primaryFields = [
    {
      label: lang === "zh" ? "定义" : "Definition",
      value: definitionText && definitionText !== briefText ? definitionText : null,
    },
    {
      label: lang === "zh" ? "临床特征" : "Clinical features",
      value: clinical,
    },
  ].filter((item) => item.value);
  const expandableFields = [
    { label: lang === "zh" ? "流行病学" : "Epidemiology", value: brief.epidemiology },
    { label: lang === "zh" ? "传播途径" : "Transmission", value: brief.transmission },
    { label: lang === "zh" ? "预防" : "Prevention", value: brief.prevention },
    { label: lang === "zh" ? "监测备注" : "Surveillance note", value: brief.surveillance_note },
    { label: lang === "zh" ? "重点人群" : "Risk groups", value: brief.risk_groups },
    { label: lang === "zh" ? "免责声明" : "Disclaimer", value: brief.disclaimer },
  ].filter((item) => item.value);

  return (
    <section className="rounded-tremor-default border border-tremor-border bg-tremor-background/90 px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/70">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {brief.language.toUpperCase()}
            </p>
            <Badge color={briefStatusColor(brief.status)}>{brief.status}</Badge>
            <Badge color={brief.source_confidence === "high" ? "emerald" : brief.source_confidence === "medium" ? "amber" : "slate"}>
              {brief.source_confidence}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {brief.model ? `${brief.model} · ` : ""}
            {formatDateTime(brief.updated_at)}
          </p>
        </div>
        {typeof brief.quality_score === "number" ? (
          <Badge color={brief.quality_score >= 0.8 ? "emerald" : brief.quality_score >= 0.6 ? "amber" : "slate"}>
            Q {brief.quality_score.toFixed(2)}
          </Badge>
        ) : null}
      </div>

      <div className="mt-3 space-y-3">
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
          <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh" ? "Brief" : "Brief"}
          </p>
          <p className="mt-2 whitespace-pre-line text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
            <CitationText text={brief.brief} references={citationReferences} />
          </p>
        </div>

        {primaryFields.length > 0 ? (
          <div className="space-y-3 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            {primaryFields.map((item) => (
              <BriefField key={item.label} label={item.label} value={item.value} references={citationReferences} compact />
            ))}
          </div>
        ) : null}

        {expandableFields.length > 0 ? (
          <div className="space-y-2">
            {expandableFields.map((item) => (
              <BriefDisclosureField key={item.label} label={item.label} value={item.value} references={citationReferences} />
            ))}
          </div>
        ) : null}

        {(brief.source_ids && brief.source_ids.length > 0) || citationReferences.length > 0 ? (
          <details className="rounded-tremor-default border border-dashed border-tremor-border px-3 py-2 dark:border-dark-tremor-border">
            <summary className="cursor-pointer list-none text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "来源标注" : "Source attribution"}
            </summary>
            {citationReferences.length > 0 ? (
              <div className="mt-2 space-y-2">
                {citationReferences.map((entry) => (
                  <div key={entry.key} className="flex items-start gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-2.5 py-2 text-xs text-tremor-content dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content">
                    <span className="mt-0.5 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-[3px] border border-tremor-border px-1 font-mono text-[10px] font-semibold text-tremor-content-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle">
                      {entry.citationIndex}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium leading-5 text-tremor-content-strong dark:text-dark-tremor-content-strong" title={entry.tooltip}>
                        {entry.citationText}
                      </p>
                      {entry.url ? (
                        <p className="mt-1 break-all text-[11px] leading-5 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {entry.linkLabel}:{" "}
                          <a
                            href={entry.url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-blue-700 underline-offset-2 hover:underline dark:text-blue-300"
                          >
                            {entry.url}
                            <ExternalLink className="h-3 w-3 shrink-0" />
                          </a>
                        </p>
                      ) : null}
                      {entry.sourceId !== null ? (
                        <p className="mt-1 font-mono text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          source {entry.sourceId}
                        </p>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : brief.source_ids && brief.source_ids.length > 0 ? (
              <p className="mt-2 text-xs text-tremor-content dark:text-dark-tremor-content">
                {lang === "zh" ? "来源 ID" : "Source IDs"}: {brief.source_ids.join(", ")}
              </p>
            ) : null}
          </details>
        ) : null}
      </div>
    </section>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-4">
      {[1, 2, 3].map((index) => (
        <div
          key={index}
          className="h-24 animate-pulse rounded-tremor-default border border-tremor-border bg-tremor-background-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted"
        />
      ))}
    </div>
  );
}

export default function KnowledgePage() {
  const { lang } = useAppStore();
  const { data: catalogue, isLoading, isFetching } = useDiseaseKnowledgeCatalogue();
  const detailPanelRef = useRef<HTMLDivElement | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<KnowledgeStatusFilter>("all");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedDiseaseId, setSelectedDiseaseId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("briefs");
  const [briefLanguage, setBriefLanguage] = useState<string>(lang);
  const [refreshSources, setRefreshSources] = useState<SourceGroup[]>(["who", "search", "wikidata", "wikipedia", "pubmed"]);
  const [forceRefresh, setForceRefresh] = useState(true);
  const [generator, setGenerator] = useState<RefreshGenerator>("ai");
  const [priority, setPriority] = useState<RefreshPriority>("normal");
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshResult, setRefreshResult] = useState<StartDiseaseKnowledgeTaskResult | null>(null);

  useTaskWebSocket({
    extraQueryKeys: [
      ["ai", "disease-knowledge", "catalogue"],
      ["ai", "disease-knowledge", "detail"],
    ],
  });

  const { data: detail, isFetching: detailLoading } = useDiseaseKnowledgeDetail(selectedDiseaseId);
  const { mutate: startTasks, isPending: refreshPending } = useStartDiseaseKnowledgeTasks();

  const visibleEntries = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = catalogue ?? [];

    return rows.filter((item) => {
      if (statusFilter !== "all" && item.knowledge_status !== statusFilter) {
        return false;
      }

      if (!needle) return true;

      return [
        item.disease_id,
        item.name_en,
        item.name_zh,
        item.category,
        item.icd_10,
        item.icd_11,
        item.description,
        item.slug,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    });
  }, [catalogue, search, statusFilter]);

  useEffect(() => {
    if (selectedDiseaseId && visibleEntries.length > 0 && !visibleEntries.some((item) => item.disease_id === selectedDiseaseId)) {
      setSelectedDiseaseId(null);
    }
  }, [selectedDiseaseId, visibleEntries]);

  useEffect(() => {
    setDetailTab("briefs");
    setBriefLanguage(lang);
  }, [lang, selectedDiseaseId]);

  useEffect(() => {
    const languages = Array.isArray(detail?.briefs) ? detail.briefs.map((brief) => brief.language).filter(Boolean) : [];
    if (languages.length > 0 && !languages.includes(briefLanguage)) {
      setBriefLanguage(languages.includes(lang) ? lang : languages[0]);
    }
  }, [briefLanguage, detail?.briefs, lang]);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedCount = selectedIds.length;
  const visibleSelectedCount = visibleEntries.filter((item) => selectedSet.has(item.disease_id)).length;
  const visibleAllSelected = visibleEntries.length > 0 && visibleEntries.every((item) => selectedSet.has(item.disease_id));
  const selectedDisease = useMemo(
    () => visibleEntries.find((item) => item.disease_id === selectedDiseaseId) ?? catalogue?.find((item) => item.disease_id === selectedDiseaseId) ?? null,
    [catalogue, selectedDiseaseId, visibleEntries],
  );
  const detailBriefs = Array.isArray(detail?.briefs) ? detail.briefs : [];
  const detailSources = Array.isArray(detail?.sources) ? detail.sources : [];
  const availableBriefLanguages = detailBriefs.map((brief) => brief.language).filter(Boolean);
  const selectedBrief = detailBriefs.find((brief) => brief.language === briefLanguage) ?? detailBriefs[0] ?? null;

  const totalDiseases = catalogue?.length ?? 0;
  const publishedBriefs = catalogue?.filter((item) => item.knowledge_status === "published").length ?? 0;
  const reviewQueue = catalogue?.filter((item) => item.knowledge_status === "requires_review").length ?? 0;
  const totalSourceRows = catalogue?.reduce((sum, item) => sum + (item.source_count || 0), 0) ?? 0;

  const taskLogsHref = selectedDiseaseId
    ? `/ai/tasks?task_type=update_disease_knowledge&search=${encodeURIComponent(selectedDiseaseId)}`
    : "/ai/tasks?task_type=update_disease_knowledge";
  const detailTabs: Array<{ key: DetailTab; label: string; count: number }> = [
    { key: "briefs", label: lang === "zh" ? "简介" : "Briefs", count: detailBriefs.length },
    { key: "sources", label: lang === "zh" ? "来源" : "Sources", count: detailSources.length },
    { key: "meta", label: lang === "zh" ? "元信息" : "Meta", count: selectedDisease ? 1 : 0 },
  ];

  const toggleSourceGroup = (value: SourceGroup) => {
    setRefreshSources((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    );
  };

  const toggleDiseaseSelection = (diseaseId: string) => {
    setSelectedIds((current) =>
      current.includes(diseaseId) ? current.filter((id) => id !== diseaseId) : [...current, diseaseId],
    );
  };

  const toggleVisibleSelection = () => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (visibleAllSelected) {
        visibleEntries.forEach((item) => next.delete(item.disease_id));
      } else {
        visibleEntries.forEach((item) => next.add(item.disease_id));
      }
      return Array.from(next);
    });
  };

  const clearSelection = () => setSelectedIds([]);

  const refreshDiseases = (diseaseIds: string[]) => {
    if (diseaseIds.length === 0) return;
    setRefreshError(null);
    setRefreshResult(null);

    startTasks(
      {
        disease_ids: diseaseIds,
        source: refreshSources,
        force: forceRefresh,
        generator,
        priority,
      },
      {
        onSuccess: (result) => {
          setRefreshResult(result);
        },
        onError: (err: unknown) => {
          const msg = err instanceof Error ? err.message : String(err);
          setRefreshError(msg);
        },
      },
    );
  };

  const handleBatchRefresh = () => {
    refreshDiseases(selectedIds);
  };

  const handleSingleRefresh = () => {
    if (!selectedDiseaseId) return;
    refreshDiseases([selectedDiseaseId]);
  };

  const queryKnowledgeBrief = (diseaseId: string) => {
    setDetailTab("briefs");
    setSelectedDiseaseId(diseaseId);
    if (window.matchMedia("(max-width: 1023px)").matches) {
      window.requestAnimationFrame(() => {
        detailPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_database")}
        title={t(lang, "knowledge_base")}
        description={t(lang, "knowledge_base_subtitle")}
        meta={
          <>
            <UiStatusBadge tone="primary">
              {lang === "zh" ? `已选 ${selectedCount}` : `${selectedCount} selected`}
            </UiStatusBadge>
            <UiStatusBadge tone={reviewQueue > 0 ? "warning" : "success"}>
              {lang === "zh" ? `待审核 ${reviewQueue}` : `${reviewQueue} in review`}
            </UiStatusBadge>
            <UiStatusBadge>{lang === "zh" ? `可见 ${visibleEntries.length}` : `${visibleEntries.length} visible`}</UiStatusBadge>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={lang === "zh" ? "监测疾病" : "Tracked diseases"}
          value={totalDiseases}
          icon={<BookOpen className="h-4 w-4" />}
          tone="primary"
        />
        <MetricTile
          label={lang === "zh" ? "已发布简介" : "Published briefs"}
          value={publishedBriefs}
          icon={<ShieldCheck className="h-4 w-4" />}
          tone="success"
        />
        <MetricTile
          label={lang === "zh" ? "待审核队列" : "Review queue"}
          value={reviewQueue}
          icon={<AlertTriangle className="h-4 w-4" />}
          tone="warning"
        />
        <MetricTile
          label={lang === "zh" ? "来源记录" : "Source rows"}
          value={totalSourceRows}
          icon={<FlaskConical className="h-4 w-4" />}
          tone="info"
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="space-y-5">
          <Panel>
            <div className="space-y-4">
              <FilterToolbar className="border-0 bg-transparent p-0 dark:bg-transparent">
                <div className="relative min-w-[240px] flex-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                  <input
                    type="search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder={t(lang, "knowledge_search_placeholder")}
                    className={cn(inputClass, "pl-9")}
                  />
                </div>
                <select
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value as KnowledgeStatusFilter)}
                  className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                  aria-label={lang === "zh" ? "知识状态筛选" : "Knowledge status filter"}
                >
                  <option value="all">{lang === "zh" ? "全部状态" : "All statuses"}</option>
                  <option value="published">{lang === "zh" ? "已发布" : "Published"}</option>
                  <option value="requires_review">{lang === "zh" ? "待审核" : "Needs review"}</option>
                  <option value="fallback">{lang === "zh" ? "回退简介" : "Fallback"}</option>
                </select>
                <select
                  value={generator}
                  onChange={(event) => setGenerator(event.target.value as RefreshGenerator)}
                  className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                  aria-label={lang === "zh" ? "生成方式" : "Generator"}
                >
                  <option value="ai">AI</option>
                  <option value="auto">Auto</option>
                  <option value="template">Template</option>
                </select>
                <select
                  value={priority}
                  onChange={(event) => setPriority(event.target.value as RefreshPriority)}
                  className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                  aria-label={lang === "zh" ? "任务优先级" : "Priority"}
                >
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </select>
              </FilterToolbar>

              <div className="flex flex-wrap items-center gap-2">
                <ActionButton onClick={toggleVisibleSelection} icon={<CheckSquare2 className="h-4 w-4" />}>
                  {t(lang, "knowledge_select_all_visible")}
                </ActionButton>
                <ActionButton onClick={clearSelection} icon={<Square className="h-4 w-4" />}>
                  {lang === "zh" ? "清空选择" : "Clear selection"}
                </ActionButton>
                <ActionButton
                  tone="primary"
                  onClick={handleBatchRefresh}
                  disabled={selectedCount === 0 || refreshPending}
                  icon={refreshPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
                >
                  {t(lang, "knowledge_refresh_selected")}
                </ActionButton>
                <label className="inline-flex cursor-pointer items-center gap-2 rounded-tremor-default border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle">
                  <input
                    type="checkbox"
                    checked={forceRefresh}
                    onChange={(e) => setForceRefresh(e.target.checked)}
                    className="h-4 w-4 rounded border-tremor-border text-blue-600 focus:ring-blue-500"
                  />
                  {t(lang, "knowledge_force_refresh")}
                </label>
                <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {lang === "zh"
                    ? `已选 ${selectedCount} 项，当前可见 ${visibleEntries.length} 项，已选可见 ${visibleSelectedCount} 项`
                    : `Selected ${selectedCount}, visible ${visibleEntries.length}, selected in view ${visibleSelectedCount}`}
                </span>
              </div>

              <details className="rounded-tremor-default border border-dashed border-tremor-border bg-tremor-background-muted/30 dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/20">
                <summary className="cursor-pointer list-none px-3 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <ListChecks className="h-4 w-4 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                      <p className="text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "可信刷新源" : "Trusted refresh sources"}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {refreshSources.map((source) => (
                        <Badge key={source} color={SOURCE_GROUPS.find((item) => item.value === source)?.color ?? "slate"} size="xs">
                          {source}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </summary>
                <div className="border-t border-dashed border-tremor-border px-3 py-3 dark:border-dark-tremor-border">
                  <div className="flex flex-wrap gap-2">
                    {SOURCE_GROUPS.map((source) => {
                      const checked = refreshSources.includes(source.value);
                      return (
                        <label
                          key={source.value}
                          className="flex cursor-pointer items-center gap-2 rounded-full border border-tremor-border bg-white px-3 py-2 text-xs text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleSourceGroup(source.value)}
                            className="h-4 w-4 rounded border-tremor-border text-blue-600 focus:ring-blue-500"
                          />
                          <span className="font-medium">{source.label}</span>
                          <Badge color={source.color} size="xs">
                            {source.value}
                          </Badge>
                        </label>
                      );
                    })}
                  </div>
                  <p className="mt-2 text-xs leading-6 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {lang === "zh"
                      ? "先用搜索发现层聚合 CDC、NIH、WHO、BMJ、MSD 等可信结果，再由 WHO、Wikidata、Wikipedia、PubMed 和 MSD 适配器补充结构化来源。"
                      : "The search discovery layer first gathers trusted CDC, NIH, WHO, BMJ, MSD, and Wikipedia results, then WHO, Wikidata, Wikipedia, PubMed, and MSD adapters add structured sources."}
                  </p>
                </div>
              </details>

              {refreshError ? (
                <div className="rounded-tremor-default border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-900/40 dark:bg-rose-950/25 dark:text-rose-300">
                  {refreshError}
                </div>
              ) : null}

              {refreshResult ? (
                <div className="rounded-tremor-default border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-900 dark:border-emerald-900/40 dark:bg-emerald-950/25 dark:text-emerald-200">
                  <div className="flex items-center gap-2 font-semibold">
                    <ShieldCheck className="h-4 w-4" />
                    {t(lang, "knowledge_refresh_result")}
                  </div>
                  <div className="mt-2 text-xs leading-6">
                    {lang === "zh"
                      ? `已创建 ${refreshResult.created_tasks.length} 个任务，跳过 ${refreshResult.skipped.length} 个疾病。队列中的任务会并行执行。`
                      : `Created ${refreshResult.created_tasks.length} task(s) and skipped ${refreshResult.skipped.length} disease(s). The queued tasks will execute in parallel.`}
                  </div>
                  {refreshResult.created_tasks.length > 0 ? (
                    <div className="mt-3 space-y-2">
                      {refreshResult.created_tasks.map((task) => (
                        <div
                          key={task.task_uuid}
                          className="flex flex-wrap items-center gap-2 rounded-tremor-default border border-emerald-200 bg-white px-3 py-2 text-xs dark:border-emerald-900/40 dark:bg-black/10"
                        >
                          <Badge color="emerald">{task.status}</Badge>
                          <span className="font-medium text-emerald-900 dark:text-emerald-200">
                            {task.task_name}
                          </span>
                          <span className="font-mono text-emerald-800 dark:text-emerald-300">
                            {task.task_uuid}
                          </span>
                          <Link
                            href={`/ai/tasks?task=${encodeURIComponent(task.task_uuid)}&task_type=update_disease_knowledge`}
                            className="inline-flex items-center gap-1 rounded-tremor-default border border-emerald-200 px-2 py-1 font-medium text-emerald-800 transition hover:bg-emerald-100 dark:border-emerald-900/40 dark:text-emerald-300 dark:hover:bg-emerald-950/30"
                          >
                            {t(lang, "knowledge_view_task_logs")}
                            <ArrowRight className="h-3.5 w-3.5" />
                          </Link>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {refreshResult.skipped.length > 0 ? (
                    <div className="mt-3 rounded-tremor-default border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/25 dark:text-amber-200">
                      <div className="font-medium">{lang === "zh" ? "跳过项" : "Skipped items"}</div>
                      <div className="mt-2 space-y-1.5">
                        {refreshResult.skipped.map((item) => (
                          <div key={item.disease_id} className="flex flex-wrap gap-x-2 gap-y-1">
                            <span className="font-mono">{item.disease_id}</span>
                            <span>•</span>
                            <span>{item.reason}</span>
                            {item.existing_task_uuid ? (
                              <>
                                <span>•</span>
                                <span className="font-mono">{item.existing_task_uuid}</span>
                              </>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </Panel>

          <Panel>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {lang === "zh" ? "疾病目录" : "Disease catalogue"}
                </h2>
                <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {lang === "zh"
                    ? "点击“查询简介”读取该疾病的 AI 知识简介、来源追踪和刷新记录。"
                    : "Use Query brief to load AI briefs, source traces, and refresh records for a disease."}
                </p>
              </div>
              <Badge color={isFetching ? "amber" : "slate"}>
                {isFetching ? (lang === "zh" ? "刷新中" : "Refreshing") : `${visibleEntries.length}`}
              </Badge>
            </div>

            <div className="mt-4 overflow-hidden rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
              <div className="max-h-[70vh] min-h-[360px] overflow-auto lg:max-h-[calc(100vh-24rem)]">
                <table className="w-full min-w-[980px] divide-y divide-tremor-border dark:divide-dark-tremor-border">
                  <thead className="sticky top-0 z-10 bg-tremor-background-subtle shadow-sm dark:bg-dark-tremor-background-subtle">
                    <tr>
                      <th className="w-10 px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        <input
                          type="checkbox"
                          checked={visibleAllSelected}
                          onChange={toggleVisibleSelection}
                          className="h-4 w-4 rounded border-tremor-border text-blue-600 focus:ring-blue-500"
                        />
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "病种" : "Disease"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "简介" : "Profile"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "状态" : "Status"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "语言" : "Languages"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "来源" : "Sources"}
                      </th>
                        <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {lang === "zh" ? "更新时间" : "Updated"}
                        </th>
                        <th className="px-3 py-2 text-right text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {lang === "zh" ? "操作" : "Actions"}
                        </th>
                      </tr>
                  </thead>
                  <tbody className="divide-y divide-tremor-border bg-white dark:divide-dark-tremor-border dark:bg-dark-tremor-background">
                    {isLoading ? (
                      [1, 2, 3, 4, 5].map((index) => (
                        <tr key={index}>
                            <td colSpan={8} className="px-3 py-4">
                            <div className="h-10 animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
                          </td>
                        </tr>
                      ))
                    ) : visibleEntries.length > 0 ? (
                      visibleEntries.map((item) => {
                        const checked = selectedSet.has(item.disease_id);
                        const active = selectedDiseaseId === item.disease_id;
                        const publishedLanguages = item.published_languages.length > 0 ? item.published_languages : [];

                        return (
                          <tr
                            key={item.disease_id}
                            className={active ? "bg-blue-50/60 dark:bg-blue-950/20" : "hover:bg-tremor-background-subtle/60 dark:hover:bg-dark-tremor-background-subtle/60"}
                          >
                            <td className="px-3 py-3 align-top">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleDiseaseSelection(item.disease_id)}
                                className="h-4 w-4 rounded border-tremor-border text-blue-600 focus:ring-blue-500"
                              />
                            </td>
                            <td className="px-3 py-3 align-top">
                                <button
                                  type="button"
                                  onClick={() => queryKnowledgeBrief(item.disease_id)}
                                  className="text-left"
                              >
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="font-mono text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                    {item.disease_id}
                                  </span>
                                  <Badge color={statusColor(item.knowledge_status)}>{item.knowledge_status}</Badge>
                                </div>
                                <p className="mt-1 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                  {item.name_en ?? item.name_zh ?? item.disease_id}
                                </p>
                                {item.name_en && item.name_zh ? (
                                  <p className="mt-0.5 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                    {item.name_zh} / {item.name_en}
                                  </p>
                                ) : null}
                              </button>
                            </td>
                            <td className="px-3 py-3 align-top">
                              <p className="max-w-xl whitespace-pre-line text-sm leading-6 text-tremor-content dark:text-dark-tremor-content line-clamp-3">
                                {item.description || (lang === "zh" ? "暂无目录描述" : "No catalogue description")}
                              </p>
                              <div className="mt-2 flex flex-wrap gap-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                {item.icd_10 ? <span className="rounded-full border border-tremor-border px-2 py-0.5">ICD-10 {item.icd_10}</span> : null}
                                {item.icd_11 ? <span className="rounded-full border border-tremor-border px-2 py-0.5">ICD-11 {item.icd_11}</span> : null}
                                {item.category ? <span className="rounded-full border border-tremor-border px-2 py-0.5">{item.category}</span> : null}
                              </div>
                            </td>
                            <td className="px-3 py-3 align-top">
                              <Badge color={statusColor(item.knowledge_status)}>
                                {item.knowledge_status}
                              </Badge>
                              <div className="mt-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                {item.brief_statuses && Object.keys(item.brief_statuses).length > 0 ? (
                                  <div className="space-y-1">
                                    {Object.entries(item.brief_statuses).map(([language, briefStatus]) => (
                                      <div key={language} className="flex items-center gap-2">
                                        <span className="font-medium">{language.toUpperCase()}</span>
                                        <Badge color={briefStatusColor(briefStatus)} size="xs">
                                          {briefStatus}
                                        </Badge>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <span>{lang === "zh" ? "暂无简介" : "No brief yet"}</span>
                                )}
                              </div>
                            </td>
                            <td className="px-3 py-3 align-top">
                              <div className="flex flex-wrap gap-1.5">
                                {publishedLanguages.length > 0 ? (
                                  publishedLanguages.map((language) => (
                                    <Badge key={language} color="emerald" size="xs">
                                      {language.toUpperCase()}
                                    </Badge>
                                  ))
                                  ) : (
                                    <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                      —
                                    </span>
                                  )}
                              </div>
                            </td>
                            <td className="px-3 py-3 align-top">
                              <Badge color={item.source_count > 3 ? "emerald" : item.source_count > 0 ? "amber" : "slate"}>
                                {item.source_count}
                              </Badge>
                            </td>
                              <td className="px-3 py-3 align-top text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                {formatDateTime(item.knowledge_updated_at)}
                              </td>
                              <td className="px-3 py-3 align-top text-right">
                                <ActionButton
                                  onClick={() => queryKnowledgeBrief(item.disease_id)}
                                  icon={<Eye className="h-4 w-4" />}
                                  className="h-8 px-2.5 text-xs"
                                >
                                  {lang === "zh" ? "查询简介" : "Query brief"}
                                </ActionButton>
                              </td>
                            </tr>
                        );
                      })
                    ) : (
                      <tr>
                          <td colSpan={8} className="px-3 py-12">
                            <EmptyState
                              icon={<BookOpen className="h-10 w-10" />}
                              title={t(lang, "knowledge_empty")}
                            />
                          </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
            </Panel>
          </div>

          <div ref={detailPanelRef} className="space-y-5">
            <Panel className="overflow-hidden p-0 lg:sticky lg:top-5 lg:max-h-[calc(100vh-2.5rem)]">
              <div className="border-b border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                      {lang === "zh" ? "知识简介查询" : "Knowledge brief query"}
                    </h2>
                    <p className="mt-1 truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {selectedDisease
                        ? `${selectedDisease.disease_id} · ${selectedDisease.name_en ?? selectedDisease.name_zh ?? selectedDisease.disease_id}`
                        : t(lang, "knowledge_no_selection")}
                    </p>
                  </div>
                  {selectedDiseaseId ? (
                    <Link
                      href={taskLogsHref}
                      className="inline-flex items-center gap-1 rounded-tremor-default border border-blue-300/70 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/25 dark:text-blue-300"
                    >
                      <ListChecks className="h-3.5 w-3.5" />
                      {t(lang, "knowledge_view_task_logs")}
                    </Link>
                  ) : null}
                </div>
              </div>

              {selectedDisease ? (
                <>
                  <div className="space-y-3 border-b border-tremor-border px-4 py-3 dark:border-dark-tremor-border">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge color={statusColor(selectedDisease.knowledge_status)}>
                          {selectedDisease.knowledge_status}
                        </Badge>
                        <Badge color="slate">{selectedDisease.disease_id}</Badge>
                        <Badge color="blue" size="xs">
                          {selectedDisease.source_count} {lang === "zh" ? "条来源" : "sources"}
                        </Badge>
                      </div>
                      <ActionButton
                        tone="primary"
                        onClick={handleSingleRefresh}
                        disabled={refreshPending}
                        icon={refreshPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
                        className="h-8 px-2.5 text-xs"
                      >
                        {lang === "zh" ? "刷新" : "Refresh"}
                      </ActionButton>
                    </div>

                    <div role="tablist" className="grid grid-cols-3 rounded-tremor-default bg-tremor-background-muted p-1 dark:bg-dark-tremor-background-muted">
                      {detailTabs.map((tab) => {
                        const active = detailTab === tab.key;
                        return (
                          <button
                            key={tab.key}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            onClick={() => setDetailTab(tab.key)}
                            className={cn(
                              "inline-flex h-8 items-center justify-center gap-1.5 rounded-[4px] px-2 text-xs font-medium transition",
                              active
                                ? "bg-tremor-background text-tremor-content-strong shadow-sm dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                                : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong",
                            )}
                          >
                            {tab.label}
                            <span className="text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                              {tab.count}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div className="max-h-[72vh] overflow-y-auto px-4 py-4 lg:max-h-[calc(100vh-14rem)]">
                    {detailLoading ? (
                      <DetailSkeleton />
                    ) : detail ? (
                      <div className="space-y-4">
                        {detailTab === "briefs" ? (
                          <div className="space-y-3">
                            <div className="rounded-tremor-default border border-dashed border-tremor-border px-3 py-3 dark:border-dark-tremor-border">
                              <div className="flex items-center gap-2">
                                <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                                <p className="text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                  {lang === "zh" ? "目录简介" : "Catalogue brief"}
                                </p>
                              </div>
                              <p className="mt-2 line-clamp-5 whitespace-pre-line text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                {selectedDisease.description || (lang === "zh" ? "暂无目录简介" : "No catalogue description")}
                              </p>
                            </div>

                            {availableBriefLanguages.length > 1 ? (
                              <div className="flex flex-wrap gap-2">
                                {availableBriefLanguages.map((language) => {
                                  const active = language === (selectedBrief?.language ?? briefLanguage);
                                  return (
                                    <button
                                      key={language}
                                      type="button"
                                      onClick={() => setBriefLanguage(language)}
                                      className={cn(
                                        "h-8 rounded-tremor-default border px-3 text-xs font-medium transition",
                                        active
                                          ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted"
                                          : "border-tremor-border bg-tremor-background text-tremor-content-emphasis hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle",
                                      )}
                                    >
                                      {language.toUpperCase()}
                                    </button>
                                  );
                                })}
                              </div>
                            ) : null}

                            {selectedBrief ? (
                              <BriefCard key={selectedBrief.language} brief={selectedBrief} lang={lang} />
                            ) : (
                              <div className="rounded-tremor-default border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                                <p className="text-sm text-tremor-content dark:text-dark-tremor-content">
                                  {lang === "zh" ? "当前没有可显示的简介。" : "No brief available yet."}
                                </p>
                              </div>
                            )}
                          </div>
                        ) : null}

                        {detailTab === "sources" ? (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <BookOpen className="h-4 w-4 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                                <p className="text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                  {lang === "zh" ? "来源追踪" : "Source trace"}
                                </p>
                              </div>
                              <Badge color={detailSources.length > 0 ? "blue" : "slate"}>
                                {detailSources.length}
                              </Badge>
                            </div>
                            {detailSources.length > 0 ? (
                              detailSources.map((source) => (
                                <SourceTraceItem key={source.id} source={source} lang={lang} />
                              ))
                            ) : (
                              <div className="rounded-tremor-default border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                                <p className="text-sm text-tremor-content dark:text-dark-tremor-content">
                                  {lang === "zh" ? "当前没有来源记录。" : "No source trace yet."}
                                </p>
                              </div>
                            )}
                          </div>
                        ) : null}

                        {detailTab === "meta" ? (
                          <div className="space-y-3">
                            {detail.summary ? (
                              <div className="grid grid-cols-2 gap-2 text-xs">
                                {[
                                  [lang === "zh" ? "简介数" : "Brief count", detail.summary.brief_count],
                                  [lang === "zh" ? "来源数" : "Source count", detail.summary.source_count],
                                  [lang === "zh" ? "已发布" : "Published", detail.summary.published_briefs],
                                  [lang === "zh" ? "待审核" : "Needs review", detail.summary.review_briefs],
                                ].map(([label, value]) => (
                                  <div key={String(label)} className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                                    <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                      {String(label)}
                                    </p>
                                    <p className="mt-1 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                      {fieldValue(value)}
                                    </p>
                                  </div>
                                ))}
                              </div>
                            ) : null}

                            <div className="rounded-tremor-default border border-tremor-border px-3 py-3 dark:border-dark-tremor-border">
                              <div className="grid grid-cols-2 gap-3 text-xs">
                                {[
                                  [lang === "zh" ? "英文名" : "English name", selectedDisease.name_en],
                                  [lang === "zh" ? "中文名" : "Chinese name", selectedDisease.name_zh],
                                  ["ICD-10", selectedDisease.icd_10],
                                  ["ICD-11", selectedDisease.icd_11],
                                  [lang === "zh" ? "类别" : "Category", selectedDisease.category],
                                  [lang === "zh" ? "更新时间" : "Updated", selectedDisease.knowledge_updated_at],
                                ].map(([label, value]) => (
                                  <div key={String(label)} className="min-w-0">
                                    <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                      {String(label)}
                                    </p>
                                    <p className="mt-1 break-words text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                      {fieldValue(value)}
                                    </p>
                                  </div>
                                ))}
                              </div>
                              <div className="mt-3 flex flex-wrap gap-2">
                                {selectedDisease.published_languages.length > 0 ? (
                                  selectedDisease.published_languages.map((language) => (
                                    <Badge key={language} color="emerald" size="xs">
                                      {language.toUpperCase()}
                                    </Badge>
                                  ))
                                ) : (
                                  <Badge color="slate" size="xs">
                                    {lang === "zh" ? "无已发布语言" : "No published language"}
                                  </Badge>
                                )}
                              </div>
                            </div>
                          </div>
                        ) : null}
                      </div>
                    ) : (
                      <div className="rounded-tremor-default border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                        <p className="text-sm text-tremor-content dark:text-dark-tremor-content">{t(lang, "knowledge_no_selection")}</p>
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className="px-4 py-10">
                  <EmptyState
                    icon={<BookOpen className="h-10 w-10" />}
                    title={t(lang, "knowledge_no_selection")}
                  />
                </div>
              )}
            </Panel>
          </div>
        </div>
    </div>
  );
}
