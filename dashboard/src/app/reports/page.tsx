"use client";

import { useEffect, useMemo, useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useReports, useReportDetail } from "@/lib/hooks/useReports";
import { formatDate } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import { ChevronDown, FileText, Gauge, Layers3, Send } from "lucide-react";
import { Badge, Card, Col, Color, Flex, Grid, ProgressBar, Text, Title } from "@tremor/react";

const statusBadge: Record<string, Color> = {
  pending: "slate",
  generating: "amber",
  completed: "emerald",
  failed: "rose",
  reviewing: "blue",
  approved: "emerald",
  published: "teal",
};

const statusLabelKey = {
  pending: "report_status_pending",
  generating: "report_status_generating",
  completed: "report_status_completed",
  failed: "report_status_failed",
  reviewing: "report_status_reviewing",
  approved: "report_status_approved",
  published: "report_status_published",
} as const;

type FlowGroupId = "queue" | "review" | "published";

type FlowGroup = {
  id: FlowGroupId;
  titleKey: "flow_queue" | "flow_review" | "flow_published";
  statuses: string[];
};

const FLOW_GROUPS: FlowGroup[] = [
  { id: "queue", titleKey: "flow_queue", statuses: ["pending", "generating", "failed"] },
  { id: "review", titleKey: "flow_review", statuses: ["reviewing"] },
  { id: "published", titleKey: "flow_published", statuses: ["completed", "approved", "published"] },
];

function statusLabel(lang: "en" | "zh", status: string) {
  const key = statusLabelKey[status as keyof typeof statusLabelKey];
  return key ? t(lang, key) : status;
}

function percent(score: number | null | undefined) {
  if (score == null) return "--";
  return `${Math.round(score * 100)}%`;
}

function extractSectionDisease(
  sectionTitle: string | null | undefined,
  fallback: string,
) {
  const title = (sectionTitle || "").trim();
  if (!title) return fallback;

  const separators = [" - ", " – ", " — ", ":", "："];
  for (const separator of separators) {
    const index = title.indexOf(separator);
    if (index > 0) {
      const candidate = title.slice(0, index).trim();
      if (candidate) return candidate;
    }
  }

  return fallback;
}

export default function ReportsPage() {
  const { lang, countryId } = useAppStore();
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const [flowFilter, setFlowFilter] = useState<FlowGroupId | "all">("all");

  const { data: reports, isLoading } = useReports(countryId);
  const { data: detail } = useReportDetail(selectedUuid);
  const list = reports ?? [];

  useEffect(() => {
    if (list.length === 0) {
      if (selectedUuid !== null) setSelectedUuid(null);
      return;
    }

    if (!selectedUuid || !list.some((item) => item.report_uuid === selectedUuid)) {
      setSelectedUuid(list[0].report_uuid);
    }
  }, [list, selectedUuid]);

  const groupedReports = useMemo(() => {
    const activeGroups =
      flowFilter === "all" ? FLOW_GROUPS : FLOW_GROUPS.filter((g) => g.id === flowFilter);

    return activeGroups.map((group) => ({
      ...group,
      items: list.filter((item) => group.statuses.includes(item.status)),
    }));
  }, [list, flowFilter]);

  const flowCounts = useMemo(() => {
    return {
      all: list.length,
      queue: list.filter((item) => FLOW_GROUPS[0].statuses.includes(item.status)).length,
      review: list.filter((item) => FLOW_GROUPS[1].statuses.includes(item.status)).length,
      published: list.filter((item) => FLOW_GROUPS[2].statuses.includes(item.status)).length,
    };
  }, [list]);

  const filteredCount = useMemo(
    () => groupedReports.reduce((total, group) => total + group.items.length, 0),
    [groupedReports],
  );

  const avgQuality = useMemo(() => {
    const scores = list
      .map((item) => item.quality_score)
      .filter((score): score is number => score != null);

    if (scores.length === 0) return null;
    return scores.reduce((sum, score) => sum + score, 0) / scores.length;
  }, [list]);

  const sectionsByDisease = useMemo(() => {
    if (!detail) return [];

    const grouped = new Map<string, typeof detail.sections>();
    detail.sections.forEach((section) => {
      const diseaseName = extractSectionDisease(
        section.title,
        t(lang, "report_disease_unknown"),
      );
      const current = grouped.get(diseaseName) ?? [];
      current.push(section);
      grouped.set(diseaseName, current);
    });

    return Array.from(grouped.entries())
      .map(([disease, sections]) => ({
        disease,
        sections: [...sections].sort((a, b) => a.section_order - b.section_order),
      }))
      .sort((a, b) => {
        const aOrder = a.sections[0]?.section_order ?? Number.MAX_SAFE_INTEGER;
        const bOrder = b.sections[0]?.section_order ?? Number.MAX_SAFE_INTEGER;
        if (aOrder !== bOrder) return aOrder - bOrder;
        return a.disease.localeCompare(b.disease);
      });
  }, [detail, lang]);

  const metrics = [
    {
      key: "total",
      title: t(lang, "report_overview_total"),
      value: String(flowCounts.all),
      icon: Layers3,
      panelClass:
        "border-sky-100 bg-sky-50/70 dark:border-sky-900/40 dark:bg-sky-950/20",
      iconClass: "text-sky-600 dark:text-sky-300",
    },
    {
      key: "filtered",
      title: t(lang, "report_overview_filtered"),
      value: String(filteredCount),
      icon: Send,
      panelClass:
        "border-amber-100 bg-amber-50/70 dark:border-amber-900/40 dark:bg-amber-950/20",
      iconClass: "text-amber-600 dark:text-amber-300",
    },
    {
      key: "published",
      title: t(lang, "report_overview_published"),
      value: String(flowCounts.published),
      icon: FileText,
      panelClass:
        "border-emerald-100 bg-emerald-50/70 dark:border-emerald-900/40 dark:bg-emerald-950/20",
      iconClass: "text-emerald-600 dark:text-emerald-300",
    },
    {
      key: "quality",
      title: t(lang, "report_overview_quality"),
      value: percent(avgQuality),
      icon: Gauge,
      panelClass:
        "border-indigo-100 bg-indigo-50/70 dark:border-indigo-900/40 dark:bg-indigo-950/20",
      iconClass: "text-indigo-600 dark:text-indigo-300",
    },
  ];

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <Card className="overflow-hidden p-0">
        <div className="border-b border-tremor-border bg-gradient-to-br from-sky-50 via-white to-emerald-50 px-4 py-5 sm:px-6 dark:border-dark-tremor-border dark:from-slate-950 dark:via-slate-900 dark:to-slate-950">
          <Badge color="teal" className="w-fit">{t(lang, "mod_results")}</Badge>
          <Title className="mt-2 text-2xl">{t(lang, "reports")}</Title>
          <Text className="mt-1">{t(lang, "publication_flow")}</Text>

          <Grid numItems={2} numItemsLg={4} className="mt-5 gap-3">
            {metrics.map((metric) => {
              const Icon = metric.icon;
              return (
                <div
                  key={metric.key}
                  className={`rounded-xl border px-3 py-3 ${metric.panelClass}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <Text className="text-xs font-medium uppercase tracking-wide">{metric.title}</Text>
                    <Icon className={`h-4 w-4 ${metric.iconClass}`} />
                  </div>
                  <div className="mt-2 text-xl font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    {metric.value}
                  </div>
                </div>
              );
            })}
          </Grid>
        </div>

        <div className="px-4 py-4 sm:px-6">
          <div className="flex flex-wrap gap-2">
            <button
              className={`inline-flex items-center gap-2 rounded-tremor-full border px-3 py-1.5 text-sm font-medium transition ${flowFilter === "all" ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted dark:border-dark-tremor-brand dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted" : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:border-tremor-brand-muted hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:border-dark-tremor-brand-muted dark:hover:bg-dark-tremor-background-subtle"}`}
              onClick={() => setFlowFilter("all")}
            >
              {t(lang, "flow_all")}
              <span className="rounded-full bg-black/10 px-2 py-0.5 text-xs dark:bg-white/10">{flowCounts.all}</span>
            </button>
            {FLOW_GROUPS.map((group) => (
              <button
                key={group.id}
                className={`inline-flex items-center gap-2 rounded-tremor-full border px-3 py-1.5 text-sm font-medium transition ${flowFilter === group.id ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted dark:border-dark-tremor-brand dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted" : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:border-tremor-brand-muted hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:border-dark-tremor-brand-muted dark:hover:bg-dark-tremor-background-subtle"}`}
                onClick={() => setFlowFilter(group.id)}
              >
                {t(lang, group.titleKey)}
                <span className="rounded-full bg-black/10 px-2 py-0.5 text-xs dark:bg-white/10">
                  {flowCounts[group.id]}
                </span>
              </button>
            ))}
          </div>
        </div>
      </Card>

      {isLoading ? (
        <Grid numItems={1} numItemsLg={12} className="gap-6">
          <Col numColSpan={1} numColSpanLg={4} className="space-y-4">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-28 w-full animate-pulse rounded-xl bg-tremor-background-muted dark:bg-dark-tremor-background-muted"
              />
            ))}
          </Col>
          <Col numColSpan={1} numColSpanLg={8}>
            <div className="h-[34rem] w-full animate-pulse rounded-xl bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          </Col>
        </Grid>
      ) : (
        <Grid numItems={1} numItemsLg={12} className="gap-6">
          <Col numColSpan={1} numColSpanLg={4} className="space-y-4">
            {groupedReports.map((group) => (
              <Card key={group.id} className="p-3">
                <Flex className="mb-2" justifyContent="between" alignItems="center">
                  <Title className="text-base">{t(lang, group.titleKey)}</Title>
                  <Badge color="slate">{group.items.length}</Badge>
                </Flex>

                {group.items.length > 0 ? (
                  <div className="max-h-[28rem] space-y-2.5 overflow-y-auto pr-1">
                    {group.items.map((r) => (
                      <button
                        key={r.report_uuid}
                        className={`w-full rounded-xl border px-3 py-3 text-left transition-all ${selectedUuid === r.report_uuid ? "border-tremor-brand bg-sky-50/80 shadow-sm dark:border-dark-tremor-brand dark:bg-sky-950/20" : "border-tremor-border bg-tremor-background hover:border-tremor-brand-muted hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:border-dark-tremor-brand-muted dark:hover:bg-dark-tremor-background-subtle"}`}
                        onClick={() => setSelectedUuid(r.report_uuid)}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <span className="line-clamp-2 text-sm font-semibold leading-5 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                            {r.title}
                          </span>
                          <Badge color={statusBadge[r.status] ?? "slate"}>{statusLabel(lang, r.status)}</Badge>
                        </div>

                        <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          <span>{formatDate(r.created_at)}</span>
                          <span>{r.section_count} {t(lang, "sections")}</span>
                        </div>

                        {r.quality_score != null && (
                          <div className="mt-2 space-y-1">
                            <div className="flex items-center justify-between text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                              <span>{t(lang, "quality_score")}</span>
                              <span className="font-medium">{percent(r.quality_score)}</span>
                            </div>
                            <ProgressBar
                              value={Math.round(r.quality_score * 100)}
                              color={r.quality_score >= 0.8 ? "emerald" : r.quality_score >= 0.5 ? "amber" : "rose"}
                            />
                          </div>
                        )}
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="rounded-xl bg-tremor-background-subtle px-3 py-4 text-center text-xs text-tremor-content-subtle dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
                    {t(lang, "flow_empty")}
                  </div>
                )}
              </Card>
            ))}
          </Col>

          <Col numColSpan={1} numColSpanLg={8}>
            {detail ? (
              <Card className="overflow-hidden p-0 lg:h-[calc(100vh-9rem)]">
                <div className="flex h-full flex-col">
                <div className="border-b border-tremor-border bg-tremor-background-subtle p-6 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <Title className="text-xl leading-7">{detail.title}</Title>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <Badge color={statusBadge[detail.status] ?? "slate"}>{statusLabel(lang, detail.status)}</Badge>
                        <Badge color="slate">{detail.report_type}</Badge>
                        {detail.ai_model_used && (
                          <Badge color="blue">{detail.ai_model_used}</Badge>
                        )}
                      </div>
                    </div>

                    <div className="min-w-[200px] rounded-xl border border-tremor-border bg-white px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                      <Text className="text-xs">{t(lang, "period")}</Text>
                      <Text className="mt-1 text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {formatDate(detail.period_start)} - {formatDate(detail.period_end)}
                      </Text>
                    </div>
                  </div>

                  <Grid numItems={1} numItemsSm={2} numItemsLg={3} className="mt-4 gap-3">
                    <div className="rounded-xl border border-tremor-border bg-white px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                      <Text className="text-xs">{t(lang, "report_meta_quality")}</Text>
                      <Text className="mt-1 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {percent(detail.quality_score)}
                      </Text>
                    </div>

                    <div className="rounded-xl border border-tremor-border bg-white px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                      <Text className="text-xs">{t(lang, "report_meta_generation_time")}</Text>
                      <Text className="mt-1 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {detail.generation_time != null ? `${detail.generation_time.toFixed(1)}s` : "--"}
                      </Text>
                    </div>

                    <div className="rounded-xl border border-tremor-border bg-white px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                      <Text className="text-xs">{t(lang, "sections")}</Text>
                      <Text className="mt-1 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {detail.sections.length}
                      </Text>
                    </div>
                  </Grid>
                </div>

                <div className="min-h-0 space-y-5 overflow-y-auto p-6">
                  {detail.summary && (
                    <div className="rounded-xl bg-tremor-background-subtle p-4 text-sm leading-6 text-tremor-content-strong dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-strong">
                      {detail.summary}
                    </div>
                  )}

                  {(detail.key_findings?.length ?? 0) > 0 && (
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-4 dark:border-emerald-900/30 dark:bg-emerald-950/20">
                      <Text className="mb-2 text-sm font-semibold text-emerald-800 dark:text-emerald-200">
                        {t(lang, "report_key_findings")}
                      </Text>
                      <ul className="list-disc space-y-1 pl-5 text-sm text-emerald-900 dark:text-emerald-100">
                        {detail.key_findings.map((finding, index) => (
                          <li key={`${finding}-${index}`}>{finding}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {(detail.recommendations?.length ?? 0) > 0 && (
                    <div className="rounded-xl border border-indigo-200 bg-indigo-50/70 p-4 dark:border-indigo-900/30 dark:bg-indigo-950/20">
                      <Text className="mb-2 text-sm font-semibold text-indigo-800 dark:text-indigo-200">
                        {t(lang, "report_recommendations")}
                      </Text>
                      <ul className="list-disc space-y-1 pl-5 text-sm text-indigo-900 dark:text-indigo-100">
                        {detail.recommendations.map((item, index) => (
                          <li key={`${item}-${index}`}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {detail.error_message && (
                    <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-900/30 dark:bg-rose-950/30 dark:text-rose-300">
                      {detail.error_message}
                    </div>
                  )}

                  <div className="space-y-4">
                    {detail.sections.length > 0 ? (
                      sectionsByDisease.map((group) => (
                        <div
                          key={group.disease}
                          className="rounded-xl border border-tremor-border bg-tremor-background-subtle/60 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/50"
                        >
                          <div className="mb-3 flex items-center justify-between gap-2 px-1">
                            <Text className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                              {group.disease}
                            </Text>
                            <Badge color="slate" size="xs">{group.sections.length}</Badge>
                          </div>

                          <div className="space-y-3">
                            {group.sections.map((sec) => (
                              <details key={sec.id} className="group overflow-hidden rounded-xl border border-tremor-border bg-white/80 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
                                <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-tremor-content-strong transition-colors hover:bg-tremor-background-subtle dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle [&::-webkit-details-marker]:hidden">
                                  <ChevronDown className="h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform group-open:rotate-180 dark:text-dark-tremor-content-subtle" />
                                  <span className="truncate">
                                    {sec.section_order}. {sec.title || sec.section_type || "Section"}
                                  </span>
                                </summary>

                                <div className="prose max-w-none border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border dark:prose-invert">
                                  <ReactMarkdown>{sec.content ?? ""}</ReactMarkdown>
                                </div>

                                {(sec.ai_model || sec.generation_time != null) && (
                                  <div className="border-t border-tremor-border bg-tremor-background-subtle px-4 py-2 text-[11px] text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
                                    {sec.ai_model ? `Model: ${sec.ai_model}` : "Model: --"}
                                    {sec.generation_time != null && ` · ${sec.generation_time.toFixed(1)}s`}
                                  </div>
                                )}
                              </details>
                            ))}
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-xl border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                        <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {t(lang, "report_no_sections")}
                        </Text>
                      </div>
                    )}
                  </div>
                </div>
                </div>
              </Card>
            ) : (
              <Card className="border-dashed">
                <div className="flex flex-col items-center justify-center py-14 text-center">
                  <FileText className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                  <Text className="mt-3 max-w-md text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {t(lang, "report_select_hint")}
                  </Text>
                </div>
              </Card>
            )}
          </Col>
        </Grid>
      )}
    </div>
  );
}
