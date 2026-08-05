import { ExternalLink } from "lucide-react";
import { Badge, type Color } from "@/components/ui/tremor";
import type { DiseaseKnowledgeDetailSource } from "@/features/ai/api";
import { formatDateTime } from "./shared";

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

export function SourceTraceItem({
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
