"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { KPICard } from "@/components/KPICard";
import {
  type DiseaseKnowledgeDetailBrief,
  type DiseaseKnowledgeDetailSource,
  type StartDiseaseKnowledgeTaskResult,
  useDiseaseKnowledgeCatalogue,
  useDiseaseKnowledgeDetail,
  useStartDiseaseKnowledgeTasks,
} from "@/lib/hooks/useAI";
import { useTaskWebSocket } from "@/lib/hooks/useTasks";
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CheckSquare2,
  ExternalLink,
  FlaskConical,
  Loader2,
  RefreshCcw,
  Search,
  ShieldCheck,
  Square,
  ListChecks,
} from "lucide-react";
import { Badge, Card, Color, Grid, Select, SelectItem, Text, TextInput, Title } from "@tremor/react";

type KnowledgeStatusFilter = "all" | "published" | "requires_review" | "fallback";
type RefreshPriority = "low" | "normal" | "high" | "urgent";
type RefreshGenerator = "ai" | "auto" | "template";
type SourceGroup = "who" | "wikidata" | "wikipedia" | "pubmed" | "msd";

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

function BriefField({
  label,
  value,
  compact = false,
}: {
  label: string;
  value?: string | null;
  compact?: boolean;
}) {
  const text = value?.trim() ?? "";
  if (!text) return null;

  return (
    <div className={compact ? "space-y-1" : "space-y-1.5"}>
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {label}
      </p>
      <p className="whitespace-pre-line text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
        {text}
      </p>
    </div>
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
    <details className="rounded-2xl border border-tremor-border bg-tremor-background/80 dark:border-dark-tremor-border dark:bg-dark-tremor-background/70">
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
                className="inline-flex items-center gap-1 rounded-lg border border-tremor-border px-2.5 py-1 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
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
          <div className="rounded-xl border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
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
          <div className="rounded-xl border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
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
          <div className="mt-3 rounded-xl border border-dashed border-tremor-border px-3 py-2 dark:border-dark-tremor-border">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "解析章节" : "Parsed sections"}
            </p>
            <p className="mt-1.5 text-xs text-tremor-content dark:text-dark-tremor-content">
              {sectionLabels.join(" · ")}
            </p>
          </div>
        ) : null}

        {source.raw_excerpt ? (
          <div className="mt-3 rounded-xl border border-dashed border-tremor-border bg-tremor-background-muted/40 px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/20">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "摘要" : "Excerpt"}
            </p>
            <p className="mt-1.5 whitespace-pre-line text-xs leading-6 text-tremor-content dark:text-dark-tremor-content">
              {source.raw_excerpt}
            </p>
          </div>
        ) : null}

        {source.content_text ? (
          <div className="mt-3 rounded-xl border border-dashed border-tremor-border bg-white px-3 py-2 dark:border-dark-tremor-border dark:bg-black/10">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
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

  const attributionItems = attribution
    .map((item) => {
      const record = asObject(item);
      const label =
        normalizeText(record?.source_name) ??
        normalizeText(record?.title) ??
        normalizeText(record?.name) ??
        (typeof record?.source_id === "number" ? `Source ${record.source_id}` : null);
      const url = normalizeText(record?.url);
      if (!label) return null;
      return { label, url };
    })
    .filter((item): item is { label: string; url: string | null } => !!item);

  const definition = brief.definition ?? brief.brief;
  const clinical = brief.clinical_features ?? brief.clinical_summary;

  return (
    <section className="rounded-2xl border border-tremor-border bg-tremor-background/90 px-4 py-4 shadow-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background/70">
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

      <div className="mt-4 space-y-4">
        <div className="rounded-xl border border-tremor-border bg-tremor-background px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh" ? "Brief" : "Brief"}
          </p>
          <p className="mt-2 whitespace-pre-line text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {brief.brief}
          </p>
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          <BriefField label={lang === "zh" ? "定义" : "Definition"} value={definition} />
          <BriefField label={lang === "zh" ? "临床特征" : "Clinical features"} value={clinical} />
          <BriefField label={lang === "zh" ? "流行病学" : "Epidemiology"} value={brief.epidemiology} />
          <BriefField label={lang === "zh" ? "传播途径" : "Transmission"} value={brief.transmission} />
          <BriefField label={lang === "zh" ? "预防" : "Prevention"} value={brief.prevention} />
          <BriefField label={lang === "zh" ? "监测备注" : "Surveillance note"} value={brief.surveillance_note} />
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          <BriefField label={lang === "zh" ? "重点人群" : "Risk groups"} value={brief.risk_groups} />
          <BriefField label={lang === "zh" ? "免责声明" : "Disclaimer"} value={brief.disclaimer} />
        </div>

        <div className="rounded-xl border border-dashed border-tremor-border px-3 py-3 dark:border-dark-tremor-border">
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh" ? "来源标注" : "Source attribution"}
          </p>
          {brief.source_ids && brief.source_ids.length > 0 ? (
            <p className="mt-1 text-xs text-tremor-content dark:text-dark-tremor-content">
              {lang === "zh" ? "来源 ID" : "Source IDs"}: {brief.source_ids.join(", ")}
            </p>
          ) : null}
          {attributionItems.length > 0 ? (
            <div className="mt-2 space-y-2">
              {attributionItems.map((entry) => (
                <div key={`${entry.label}-${entry.url ?? "no-url"}`} className="flex flex-wrap items-center gap-2 text-xs text-tremor-content dark:text-dark-tremor-content">
                  {entry.url ? (
                    <a
                      href={entry.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 rounded-lg border border-tremor-border px-2 py-1 font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      {entry.label}
                    </a>
                  ) : (
                    <span className="rounded-lg border border-tremor-border px-2 py-1 font-medium text-tremor-content-emphasis dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis">
                      {entry.label}
                    </span>
                  )}
                </div>
              ))}
            </div>
          ) : null}
        </div>
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
          className="h-24 animate-pulse rounded-2xl border border-tremor-border bg-tremor-background-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted"
        />
      ))}
    </div>
  );
}

export default function KnowledgePage() {
  const { lang } = useAppStore();
  const { data: catalogue, isLoading, isFetching } = useDiseaseKnowledgeCatalogue();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<KnowledgeStatusFilter>("all");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedDiseaseId, setSelectedDiseaseId] = useState<string | null>(null);
  const [refreshSources, setRefreshSources] = useState<SourceGroup[]>(["who", "wikidata", "wikipedia", "pubmed"]);
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
    if (visibleEntries.length === 0) {
      if (selectedDiseaseId !== null) {
        setSelectedDiseaseId(null);
      }
      return;
    }

    if (!selectedDiseaseId || !visibleEntries.some((item) => item.disease_id === selectedDiseaseId)) {
      setSelectedDiseaseId(visibleEntries[0].disease_id);
    }
  }, [selectedDiseaseId, visibleEntries]);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedCount = selectedIds.length;
  const visibleSelectedCount = visibleEntries.filter((item) => selectedSet.has(item.disease_id)).length;
  const visibleAllSelected = visibleEntries.length > 0 && visibleEntries.every((item) => selectedSet.has(item.disease_id));
  const selectedDisease = useMemo(
    () => visibleEntries.find((item) => item.disease_id === selectedDiseaseId) ?? catalogue?.find((item) => item.disease_id === selectedDiseaseId) ?? null,
    [catalogue, selectedDiseaseId, visibleEntries],
  );

  const totalDiseases = catalogue?.length ?? 0;
  const publishedBriefs = catalogue?.filter((item) => item.knowledge_status === "published").length ?? 0;
  const reviewQueue = catalogue?.filter((item) => item.knowledge_status === "requires_review").length ?? 0;
  const totalSourceRows = catalogue?.reduce((sum, item) => sum + (item.source_count || 0), 0) ?? 0;

  const taskLogsHref = selectedDiseaseId
    ? `/ai/tasks?task_type=update_disease_knowledge&search=${encodeURIComponent(selectedDiseaseId)}`
    : "/ai/tasks?task_type=update_disease_knowledge";

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

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="blue" className="w-fit">
          {t(lang, "mod_database")}
        </Badge>
        <Title className="text-2xl">{t(lang, "knowledge_base")}</Title>
        <Text>{t(lang, "knowledge_base_subtitle")}</Text>
      </div>

      <Grid numItems={1} numItemsSm={2} numItemsLg={4} className="gap-4">
        <KPICard
          title={lang === "zh" ? "监测疾病" : "Tracked diseases"}
          value={totalDiseases}
          icon={<BookOpen className="h-5 w-5" />}
          accent="primary"
        />
        <KPICard
          title={lang === "zh" ? "已发布简介" : "Published briefs"}
          value={publishedBriefs}
          icon={<ShieldCheck className="h-5 w-5" />}
          accent="success"
        />
        <KPICard
          title={lang === "zh" ? "待审核队列" : "Review queue"}
          value={reviewQueue}
          icon={<AlertTriangle className="h-5 w-5" />}
          accent="warning"
        />
        <KPICard
          title={lang === "zh" ? "来源记录" : "Source rows"}
          value={totalSourceRows}
          icon={<FlaskConical className="h-5 w-5" />}
          accent="info"
        />
      </Grid>

      <Grid numItems={1} numItemsLg={12} className="gap-6">
        <div className="space-y-6 lg:col-span-8">
          <Card>
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <div className="relative flex-1 min-w-[220px]">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                  <TextInput
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder={t(lang, "knowledge_search_placeholder")}
                    className="pl-9"
                  />
                </div>
                <Select
                  value={statusFilter}
                  onValueChange={(value) => setStatusFilter(value as KnowledgeStatusFilter)}
                  className="w-full sm:w-44"
                >
                  <SelectItem value="all">{lang === "zh" ? "全部状态" : "All statuses"}</SelectItem>
                  <SelectItem value="published">{lang === "zh" ? "已发布" : "Published"}</SelectItem>
                  <SelectItem value="requires_review">{lang === "zh" ? "待审核" : "Needs review"}</SelectItem>
                  <SelectItem value="fallback">{lang === "zh" ? "回退简介" : "Fallback"}</SelectItem>
                </Select>
                <Select
                  value={generator}
                  onValueChange={(value) => setGenerator(value as RefreshGenerator)}
                  className="w-full sm:w-36"
                >
                  <SelectItem value="ai">AI</SelectItem>
                  <SelectItem value="auto">Auto</SelectItem>
                  <SelectItem value="template">Template</SelectItem>
                </Select>
                <Select
                  value={priority}
                  onValueChange={(value) => setPriority(value as RefreshPriority)}
                  className="w-full sm:w-36"
                >
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="normal">Normal</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                  <SelectItem value="urgent">Urgent</SelectItem>
                </Select>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={toggleVisibleSelection}
                  className="inline-flex items-center gap-2 rounded-lg border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
                >
                  <CheckSquare2 className="h-4 w-4" />
                  {t(lang, "knowledge_select_all_visible")}
                </button>
                <button
                  type="button"
                  onClick={clearSelection}
                  className="inline-flex items-center gap-2 rounded-lg border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
                >
                  <Square className="h-4 w-4" />
                  {lang === "zh" ? "清空选择" : "Clear selection"}
                </button>
                <button
                  type="button"
                  onClick={handleBatchRefresh}
                  disabled={selectedCount === 0 || refreshPending}
                  className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-400"
                >
                  {refreshPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
                  {t(lang, "knowledge_refresh_selected")}
                </button>
                <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle">
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

              <div className="rounded-2xl border border-dashed border-tremor-border bg-tremor-background-muted/30 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/20">
                <div className="flex flex-wrap items-center gap-2">
                  <ListChecks className="h-4 w-4 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {lang === "zh" ? "可信刷新源" : "Trusted refresh sources"}
                  </p>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {SOURCE_GROUPS.map((source) => {
                    const checked = refreshSources.includes(source.value);
                    return (
                      <label
                        key={source.value}
                        className="flex cursor-pointer items-center gap-2 rounded-full border border-tremor-border bg-white px-3 py-2 text-xs text-tremor-content-emphasis shadow-sm transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis"
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
                    ? "WHO 与 WHO DON 作为首选权威来源，Wikidata/Wikipedia 用于结构化补充，MSD 仅保留元数据和链接。"
                    : "WHO and WHO DON are the primary authoritative sources, Wikidata and Wikipedia provide structured supplements, and MSD is retained as metadata plus links only."}
                </p>
              </div>

              {refreshError ? (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-900/40 dark:bg-rose-950/25 dark:text-rose-300">
                  {refreshError}
                </div>
              ) : null}

              {refreshResult ? (
                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-900 dark:border-emerald-900/40 dark:bg-emerald-950/25 dark:text-emerald-200">
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
                          className="flex flex-wrap items-center gap-2 rounded-xl border border-emerald-200 bg-white px-3 py-2 text-xs dark:border-emerald-900/40 dark:bg-black/10"
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
                            className="inline-flex items-center gap-1 rounded-lg border border-emerald-200 px-2 py-1 font-medium text-emerald-800 transition hover:bg-emerald-100 dark:border-emerald-900/40 dark:text-emerald-300 dark:hover:bg-emerald-950/30"
                          >
                            {t(lang, "knowledge_view_task_logs")}
                            <ArrowRight className="h-3.5 w-3.5" />
                          </Link>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {refreshResult.skipped.length > 0 ? (
                    <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/25 dark:text-amber-200">
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
          </Card>

          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <Title className="text-base">
                  {lang === "zh" ? "疾病目录" : "Disease catalogue"}
                </Title>
                <Text className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {lang === "zh"
                    ? "点击疾病名称查看详情；勾选复选框可以批量刷新知识库，并行创建任务。"
                    : "Click a disease name to inspect details, or tick rows to batch refresh the knowledge base in parallel."}
                </Text>
              </div>
              <Badge color={isFetching ? "amber" : "slate"}>
                {isFetching ? (lang === "zh" ? "刷新中" : "Refreshing") : `${visibleEntries.length}`}
              </Badge>
            </div>

            <div className="mt-4 overflow-hidden rounded-2xl border border-tremor-border dark:border-dark-tremor-border">
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-tremor-border dark:divide-dark-tremor-border">
                  <thead className="bg-tremor-background-subtle dark:bg-dark-tremor-background-subtle">
                    <tr>
                      <th className="w-10 px-3 py-2 text-left text-xs font-semibold uppercase tracking-[0.14em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        <input
                          type="checkbox"
                          checked={visibleAllSelected}
                          onChange={toggleVisibleSelection}
                          className="h-4 w-4 rounded border-tremor-border text-blue-600 focus:ring-blue-500"
                        />
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-[0.14em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "病种" : "Disease"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-[0.14em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "简介" : "Profile"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-[0.14em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "状态" : "Status"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-[0.14em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "语言" : "Languages"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-[0.14em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "来源" : "Sources"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-[0.14em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "更新时间" : "Updated"}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-tremor-border bg-white dark:divide-dark-tremor-border dark:bg-dark-tremor-background">
                    {isLoading ? (
                      [1, 2, 3, 4, 5].map((index) => (
                        <tr key={index}>
                          <td colSpan={7} className="px-3 py-4">
                            <div className="h-10 animate-pulse rounded-xl bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
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
                                onClick={() => setSelectedDiseaseId(item.disease_id)}
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
                                  <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                    —
                                  </Text>
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
                          </tr>
                        );
                      })
                    ) : (
                      <tr>
                        <td colSpan={7} className="px-3 py-12">
                          <div className="flex flex-col items-center justify-center text-center">
                            <BookOpen className="h-10 w-10 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                            <Text className="mt-3">
                              {t(lang, "knowledge_empty")}
                            </Text>
                          </div>
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </Card>
        </div>

        <div className="space-y-6 lg:col-span-4">
          <Card className="lg:sticky lg:top-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <Title className="text-base">
                  {lang === "zh" ? "疾病详情" : "Disease detail"}
                </Title>
                <Text className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {selectedDisease
                    ? `${selectedDisease.disease_id} · ${selectedDisease.name_en ?? selectedDisease.name_zh ?? selectedDisease.disease_id}`
                    : t(lang, "knowledge_no_selection")}
                </Text>
              </div>
              {selectedDiseaseId ? (
                <Link
                  href={taskLogsHref}
                  className="inline-flex items-center gap-1 rounded-lg border border-blue-300/70 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/25 dark:text-blue-300"
                >
                  <ListChecks className="h-3.5 w-3.5" />
                  {t(lang, "knowledge_view_task_logs")}
                </Link>
              ) : null}
            </div>

            {selectedDisease ? (
              <div className="mt-4 space-y-4">
                <div className="rounded-2xl border border-tremor-border bg-tremor-background/80 px-4 py-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background/70">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge color={statusColor(selectedDisease.knowledge_status)}>
                      {selectedDisease.knowledge_status}
                    </Badge>
                    <Badge color="slate">{selectedDisease.disease_id}</Badge>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "英文名" : "English name"}
                      </p>
                      <p className="mt-1 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {fieldValue(selectedDisease.name_en)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "中文名" : "Chinese name"}
                      </p>
                      <p className="mt-1 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {fieldValue(selectedDisease.name_zh)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        ICD-10
                      </p>
                      <p className="mt-1 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {fieldValue(selectedDisease.icd_10)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        ICD-11
                      </p>
                      <p className="mt-1 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {fieldValue(selectedDisease.icd_11)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "类别" : "Category"}
                      </p>
                      <p className="mt-1 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {fieldValue(selectedDisease.category)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "更新时间" : "Updated"}
                      </p>
                      <p className="mt-1 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {fieldValue(selectedDisease.knowledge_updated_at)}
                      </p>
                    </div>
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
                    <Badge color="blue" size="xs">
                      {selectedDisease.source_count} {lang === "zh" ? "条来源" : "sources"}
                    </Badge>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={handleSingleRefresh}
                    disabled={refreshPending}
                    className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-400"
                  >
                    {refreshPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
                    {lang === "zh" ? "刷新当前疾病" : "Refresh this disease"}
                  </button>
                  <Link
                    href={taskLogsHref}
                    className="inline-flex items-center gap-2 rounded-lg border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
                  >
                    <ListChecks className="h-4 w-4" />
                    {t(lang, "knowledge_view_task_logs")}
                  </Link>
                </div>

                <div className="rounded-2xl border border-dashed border-tremor-border px-4 py-3 dark:border-dark-tremor-border">
                  <div className="flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                    <p className="text-xs font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {lang === "zh" ? "知识简介" : "Official brief"}
                    </p>
                  </div>
                  <p className="mt-2 whitespace-pre-line text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    {selectedDisease.description || (lang === "zh" ? "暂无目录简介" : "No catalogue description")}
                  </p>
                </div>

                {detailLoading ? (
                  <DetailSkeleton />
                ) : detail ? (
                  <div className="space-y-4">
                    <div className="space-y-3">
                      {detail.summary ? (
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div className="rounded-xl border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                              {lang === "zh" ? "简介数" : "Brief count"}
                            </p>
                            <p className="mt-1 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                              {fieldValue(detail.summary.brief_count)}
                            </p>
                          </div>
                          <div className="rounded-xl border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                              {lang === "zh" ? "来源数" : "Source count"}
                            </p>
                            <p className="mt-1 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                              {fieldValue(detail.summary.source_count)}
                            </p>
                          </div>
                          <div className="rounded-xl border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                              {lang === "zh" ? "已发布" : "Published"}
                            </p>
                            <p className="mt-1 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                              {fieldValue(detail.summary.published_briefs)}
                            </p>
                          </div>
                          <div className="rounded-xl border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                              {lang === "zh" ? "待审核" : "Needs review"}
                            </p>
                            <p className="mt-1 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                              {fieldValue(detail.summary.review_briefs)}
                            </p>
                          </div>
                        </div>
                      ) : null}
                    </div>

                    {Array.isArray(detail.briefs) && detail.briefs.length > 0 ? (
                      <div className="space-y-3">
                        {detail.briefs.map((brief) => (
                          <BriefCard key={brief.language} brief={brief} lang={lang} />
                        ))}
                      </div>
                    ) : (
                      <div className="rounded-2xl border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                        <Text>{lang === "zh" ? "当前没有可显示的简介。" : "No brief available yet."}</Text>
                      </div>
                    )}

                    {Array.isArray(detail.sources) && detail.sources.length > 0 ? (
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <BookOpen className="h-4 w-4 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                            {lang === "zh" ? "来源追踪" : "Source trace"}
                          </p>
                        </div>
                        {detail.sources.map((source) => (
                          <SourceTraceItem key={source.id} source={source} lang={lang} />
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="rounded-2xl border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                    <Text>{t(lang, "knowledge_no_selection")}</Text>
                  </div>
                )}
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                <Text>{t(lang, "knowledge_no_selection")}</Text>
              </div>
            )}
          </Card>
        </div>
      </Grid>
    </div>
  );
}
