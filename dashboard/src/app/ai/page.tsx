"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useTasks, useTaskDetail, useTaskWebSocket } from "@/lib/hooks/useTasks";
import { useReports } from "@/lib/hooks/useReports";
import { formatDate } from "@/lib/utils";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { Chart } from "@/components/charts/Chart";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { Cpu, ChevronDown, Search, MessageSquareText } from "lucide-react";
import { Badge, Card, Grid, Text, Title, Color } from "@tremor/react";

const AI_TYPES = "process_data,generate_report,generate_section,review_section";

const statusBadge: Record<string, Color> = {
  pending: "slate",
  queued: "blue",
  running: "amber",
  completed: "emerald",
  failed: "rose",
  cancelled: "slate",
  retrying: "amber",
};

const statusColors: Record<string, string> = {
  pending: CHART_TOKENS.neutral,
  queued: CHART_TOKENS.info,
  running: CHART_TOKENS.warning,
  completed: CHART_TOKENS.success,
  failed: CHART_TOKENS.destructive,
  cancelled: CHART_TOKENS.neutral,
  retrying: "#f97316",
};

function parseReportId(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === "string") {
    const num = Number(value);
    if (Number.isFinite(num)) {
      return num;
    }
  }

  return null;
}

export default function AIPage() {
  const { lang } = useAppStore();
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [expandedUuid, setExpandedUuid] = useState<string | null>(null);

  useTaskWebSocket();

  const { data: tasks, isLoading } = useTasks(
    statusFilter || undefined,
    typeFilter || AI_TYPES,
    search || undefined,
    100,
  );

  const { data: taskDetail, isFetching: detailLoading } = useTaskDetail(expandedUuid);
  const { data: reports } = useReports(null, undefined, 200);

  const reportUuidById = useMemo(() => {
    const mapping = new Map<number, string>();
    (reports ?? []).forEach((report) => {
      mapping.set(report.id, report.report_uuid);
    });
    return mapping;
  }, [reports]);

  const activeTaskReportUuid = useMemo(() => {
    if (!taskDetail) return null;

    const outputData = taskDetail.output_data as Record<string, unknown> | null;
    const reportId = parseReportId(taskDetail.report_id) ?? parseReportId(outputData?.report_id);
    if (!reportId) return null;

    return reportUuidById.get(reportId) ?? null;
  }, [reportUuidById, taskDetail]);

  const summary = useMemo(() => {
    const total = tasks?.length ?? 0;
    const running = (tasks ?? []).filter((t) => t.status === "running").length;
    const failed = (tasks ?? []).filter((t) => t.status === "failed").length;
    const avgProgress = total > 0
      ? Math.round((tasks ?? []).reduce((acc, t) => acc + t.progress, 0) / total)
      : 0;
    return { total, running, failed, avgProgress };
  }, [tasks]);

  const statusChartData = useMemo(() => {
    const rows = new Map<string, number>();
    (tasks ?? []).forEach((task) => {
      rows.set(task.status, (rows.get(task.status) ?? 0) + 1);
    });
    return Array.from(rows.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);
  }, [tasks]);

  const typeChartData = useMemo(() => {
    const rows = new Map<string, number>();
    (tasks ?? []).forEach((task) => {
      rows.set(task.task_type, (rows.get(task.task_type) ?? 0) + 1);
    });
    return Array.from(rows.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);
  }, [tasks]);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="violet" className="w-fit">{t(lang, "mod_ai")}</Badge>
        <Title className="text-2xl">{t(lang, "ai_tasks")}</Title>
        <Text>{t(lang, "ai_tasks_subtitle")}</Text>
        <div className="pt-1">
          <Link
            href="/ai/interactions"
            className="inline-flex items-center gap-1 rounded-lg border border-violet-300/70 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:bg-violet-100 dark:border-violet-800 dark:bg-violet-950/25 dark:text-violet-300"
          >
            <MessageSquareText className="h-3.5 w-3.5" />
            Open AI Interactions
          </Link>
        </div>
      </div>

      <Grid numItems={1} numItemsSm={2} numItemsLg={4} className="gap-4">
        <Card>
          <Text>{t(lang, "total_tasks")}</Text>
          <Title>{summary.total}</Title>
        </Card>
        <Card>
          <Text>{t(lang, "running_tasks")}</Text>
          <Title className="text-amber-600 dark:text-amber-500">{summary.running}</Title>
        </Card>
        <Card>
          <Text>{t(lang, "failed_tasks")}</Text>
          <Title className="text-rose-600 dark:text-rose-500">{summary.failed}</Title>
        </Card>
        <Card>
          <Text>{t(lang, "avg_progress")}</Text>
          <Title>{summary.avgProgress}%</Title>
        </Card>
      </Grid>

      {tasks && tasks.length > 0 && (
        <Grid numItems={1} numItemsLg={2} className="gap-4">
          <Card>
            <Title className="mb-2">Status Distribution</Title>
            <Chart
              height={240}
              option={{
                tooltip: { trigger: "item" },
                series: [{
                  type: "pie",
                  radius: ["40%", "70%"],
                  center: ["50%", "50%"],
                  label: { formatter: "{b}: {d}%", color: CHART_TOKENS.text, fontSize: 11 },
                  data: statusChartData.map((row) => ({
                    ...row,
                    itemStyle: { color: statusColors[row.name] ?? "#9ca3af" },
                  })),
                }],
              }}
            />
          </Card>
          <Card>
            <Title className="mb-2">Task Types</Title>
            <Chart
              height={240}
              option={{
                tooltip: { trigger: "axis" },
                grid: { left: 120, right: 20, bottom: 20, top: 10 },
                xAxis: { type: "value", splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } } },
                yAxis: {
                  type: "category",
                  data: typeChartData.map((r) => r.name).reverse(),
                  axisLabel: { fontSize: 11 },
                },
                series: [{
                  type: "bar",
                  data: typeChartData.map((r) => r.value).reverse(),
                  barMaxWidth: 20,
                  itemStyle: { borderRadius: [0, 4, 4, 0], color: CHART_TOKENS.info },
                }],
              }}
            />
          </Card>
        </Grid>
      )}

      <Card>
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative flex-1 min-w-[200px] max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4"
              style={{ color: "var(--color-tremor-content-subtle)" }} />
            <input
              type="text"
              placeholder="Search tasks..."
              className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select
            className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">All statuses</option>
            {["pending", "queued", "running", "completed", "failed", "cancelled"].map(
              (s) => (<option key={s} value={s}>{s}</option>),
            )}
          </select>
          <select
            className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            <option value="">All AI types</option>
            {["process_data", "generate_report", "generate_section", "review_section"].map(
              (tp) => (<option key={tp} value={tp}>{tp}</option>),
            )}
          </select>
        </div>
      </Card>

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          ))}
        </div>
      ) : !tasks || tasks.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <Cpu className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Text className="mt-3">{t(lang, "no_data")}</Text>
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {tasks.map((task) => (
            <Card key={task.task_uuid} className="overflow-hidden p-0">
              <button
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-[13px] transition-colors"
                style={{ background: expandedUuid === task.task_uuid ? "var(--color-tremor-background-subtle)" : "transparent" }}
                onClick={() =>
                  setExpandedUuid(expandedUuid === task.task_uuid ? null : task.task_uuid)
                }
              >
                <Badge color={statusBadge[task.status] ?? "slate"}>{task.status}</Badge>
                <span className="flex-1 truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{task.task_name}</span>
                <Badge color="slate">{task.task_type}</Badge>
                <div className="w-24">
                  <div className="h-2 overflow-hidden rounded-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${task.progress}%`,
                        background: task.progress === 100 ? CHART_TOKENS.success : CHART_TOKENS.primary,
                      }}
                    />
                  </div>
                </div>
                <span className="w-9 text-right text-[11px] font-medium"
                  style={{ color: "var(--color-tremor-content-subtle)" }}>{task.progress}%</span>
                <span className="hidden lg:block w-24 text-right text-[11px]"
                  style={{ color: "var(--color-tremor-content-subtle)" }}>{formatDate(task.created_at)}</span>
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform ${expandedUuid === task.task_uuid ? "rotate-180" : ""}`}
                  style={{ color: "var(--color-tremor-content-subtle)" }}
                />
              </button>

              {expandedUuid === task.task_uuid && (
                <div className="border-t border-tremor-border px-4 py-3 text-[13px] dark:border-dark-tremor-border">
                  {activeTaskReportUuid && expandedUuid === task.task_uuid && (
                    <div className="mb-3">
                      <Link
                        href={`/ai/interactions?uuid=${encodeURIComponent(activeTaskReportUuid)}`}
                        className="inline-flex items-center gap-1 rounded-lg border border-blue-300/70 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/25 dark:text-blue-300"
                      >
                        <MessageSquareText className="h-3.5 w-3.5" />
                        View this report in chat workflow
                      </Link>
                    </div>
                  )}
                  <TaskDetailPanel taskDetail={taskDetail} detailLoading={detailLoading} emptyMessage="Task detail unavailable." />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
