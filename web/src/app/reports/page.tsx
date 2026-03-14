"use client";

import { useMemo, useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useReports, useReportDetail } from "@/lib/hooks/useReports";
import { formatDate } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import { FileText, ChevronDown } from "lucide-react";
import { Card, Title, Text, Badge, Grid, Col, Flex, ProgressBar, Color } from "@tremor/react";

const statusBadge: Record<string, Color> = {
  pending: "slate",
  generating: "amber",
  completed: "emerald",
  failed: "rose",
  reviewing: "blue",
  approved: "emerald",
  published: "teal",
};

type FlowGroupId = "queue" | "review" | "published";

type FlowGroup = {
  id: FlowGroupId;
  titleKey: "flow_queue" | "flow_review" | "flow_published";
  statuses: string[];
};

const FLOW_GROUPS: FlowGroup[] = [
  { id: "queue", titleKey: "flow_queue", statuses: ["pending", "generating", "completed", "failed"] },
  { id: "review", titleKey: "flow_review", statuses: ["reviewing", "approved"] },
  { id: "published", titleKey: "flow_published", statuses: ["published"] },
];

export default function ReportsPage() {
  const { lang, countryId } = useAppStore();
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const [flowFilter, setFlowFilter] = useState<FlowGroupId | "all">("all");

  const { data: reports, isLoading } = useReports(countryId);
  const { data: detail } = useReportDetail(selectedUuid);

  const groupedReports = useMemo(() => {
    const list = reports ?? [];
    const activeGroups =
      flowFilter === "all" ? FLOW_GROUPS : FLOW_GROUPS.filter((g) => g.id === flowFilter);

    return activeGroups.map((group) => ({
      ...group,
      items: list.filter((item) => group.statuses.includes(item.status)),
    }));
  }, [reports, flowFilter]);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="teal" className="w-fit">{t(lang, "mod_results")}</Badge>
        <Title className="text-2xl">{t(lang, "reports")}</Title>
        <Text>{t(lang, "publication_flow")}</Text>
      </div>

      <Card>
        <div className="flex flex-wrap gap-2">
          <button
            className={`rounded-tremor-full px-3 py-1.5 text-sm transition ${flowFilter === "all" ? "bg-tremor-brand text-tremor-brand-inverted dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted" : "bg-tremor-background-muted text-tremor-content hover:bg-tremor-border dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content dark:hover:bg-dark-tremor-border"}`}
            onClick={() => setFlowFilter("all")}
          >
            {t(lang, "flow_all")}
          </button>
          {FLOW_GROUPS.map((group) => (
            <button
              key={group.id}
              className={`rounded-tremor-full px-3 py-1.5 text-sm transition ${flowFilter === group.id ? "bg-tremor-brand text-tremor-brand-inverted dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted" : "bg-tremor-background-muted text-tremor-content hover:bg-tremor-border dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content dark:hover:bg-dark-tremor-border"}`}
              onClick={() => setFlowFilter(group.id)}
            >
              {t(lang, group.titleKey)}
            </button>
          ))}
        </div>
      </Card>

      {isLoading ? (
        <Grid numItems={1} numItemsLg={3} className="gap-4">
          <div className="space-y-4 lg:col-span-1">
            {[1, 2, 3].map((i) => <div key={i} className="h-24 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />)}
          </div>
          <div className="lg:col-span-2">
            <div className="h-96 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          </div>
        </Grid>
      ) : (
        <Grid numItems={1} numItemsLg={3} className="gap-6">
          <div className="space-y-4 lg:col-span-1">
            {groupedReports.map((group) => (
              <Card key={group.id} className="p-3">
                <Flex className="mb-2" justifyContent="between" alignItems="center">
                  <Title>{t(lang, group.titleKey)}</Title>
                  <Badge color="slate">{group.items.length}</Badge>
                </Flex>

                {group.items.length > 0 ? (
                  <div className="space-y-4">
                    {group.items.map((r) => (
                      <button
                        key={r.report_uuid}
                        className="w-full rounded-tremor-default border p-3 text-left transition-all"
                        style={{
                          borderColor: selectedUuid === r.report_uuid ? "var(--color-tremor-brand)" : "var(--color-tremor-border)",
                          boxShadow: selectedUuid === r.report_uuid ? "0 0 0 1px var(--color-tremor-brand)" : undefined,
                        }}
                        onClick={() => setSelectedUuid(r.report_uuid)}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <span className="text-sm font-semibold leading-5 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                            {r.title}
                          </span>
                          <Badge color={statusBadge[r.status] ?? "slate"}>{r.status}</Badge>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          <span>{formatDate(r.created_at)}</span>
                          <span>{r.section_count} {t(lang, "sections")}</span>
                        </div>
                        {r.quality_score != null && (
                          <ProgressBar
                            className="mt-2"
                            value={Math.round(r.quality_score * 100)}
                            color={r.quality_score >= 0.8 ? "emerald" : r.quality_score >= 0.5 ? "amber" : "rose"}
                          />
                        )}
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="rounded-lg bg-tremor-background-subtle px-3 py-4 text-center text-xs text-tremor-content-subtle dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
                    {t(lang, "flow_empty")}
                  </div>
                )}
              </Card>
            ))}
          </div>

          <div className="lg:col-span-2">
            {detail ? (
              <Card className="space-y-5 p-6">
                <div>
                  <Title>{detail.title}</Title>
                  <div className="mt-2 flex flex-wrap gap-3">
                    <Badge color="slate">{detail.report_type}</Badge>
                    <Text className="text-xs">
                      {formatDate(detail.period_start)} – {formatDate(detail.period_end)}
                    </Text>
                    {detail.quality_score != null && (
                      <Badge color="emerald">
                        Quality: {(detail.quality_score * 100).toFixed(0)}%
                      </Badge>
                    )}
                    {detail.generation_time != null && (
                      <Text className="text-xs">
                        {detail.generation_time.toFixed(1)}s
                      </Text>
                    )}
                    {detail.ai_model_used && (
                      <Badge color="blue">{detail.ai_model_used}</Badge>
                    )}
                  </div>
                </div>

                {detail.summary && (
                  <div className="rounded-lg bg-tremor-background-subtle p-4 text-sm text-tremor-content-strong dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-strong">
                    {detail.summary}
                  </div>
                )}

                {detail.error_message && (
                  <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-900/30 dark:bg-rose-950/30 dark:text-rose-300">
                    {detail.error_message}
                  </div>
                )}

                <div className="space-y-4">
                  {detail.sections.map((sec) => (
                    <details key={sec.id} className="group overflow-hidden rounded-lg border border-tremor-border dark:border-dark-tremor-border">
                      <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 text-sm font-semibold transition-colors"
                        style={{ color: "var(--color-tremor-content-strong)" }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--color-tremor-background-subtle)")}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
                        <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180"
                          style={{ color: "var(--color-tremor-content-subtle)" }} />
                        <span>{sec.section_order}. {sec.title || sec.section_type || "Section"}</span>
                      </summary>
                      <div className="prose border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border dark:prose-invert">
                        <ReactMarkdown>{sec.content ?? ""}</ReactMarkdown>
                      </div>
                      {sec.ai_model && (
                        <div className="border-t border-tremor-border bg-tremor-background-subtle px-4 py-2 text-[11px] text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
                          Model: {sec.ai_model}
                          {sec.generation_time != null && ` · ${sec.generation_time.toFixed(1)}s`}
                        </div>
                      )}
                    </details>
                  ))}
                </div>
              </Card>
            ) : (
              <Card>
                <div className="flex flex-col items-center justify-center py-14 text-center">
                  <FileText className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                  <Text className="mt-3">Select a report to view details</Text>
                </div>
              </Card>
            )}
          </div>
        </Grid>
      )}
    </div>
  );
}
