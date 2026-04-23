"use client";

import { useMemo, useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useTasks, useTaskDetail, useTaskWebSocket } from "@/lib/hooks/useTasks";
import { formatDate } from "@/lib/utils";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { ListTodo, Search, ChevronDown } from "lucide-react";
import { Chart } from "@/components/charts/Chart";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { Card, Grid, Title, Text, Badge, Flex, Color } from "@tremor/react";

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

export default function TasksPage() {
  const { lang, countryId } = useAppStore();
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [expandedUuid, setExpandedUuid] = useState<string | null>(null);

  useTaskWebSocket();

  const { data: tasks, isLoading } = useTasks(
    statusFilter || undefined,
    typeFilter || undefined,
    countryId,
    search || undefined,
    100,
  );

  const { data: taskDetail, isFetching: detailLoading } = useTaskDetail(expandedUuid);

  const statusChartData = useMemo(() => {
    const rows = new Map<string, number>();
    (tasks ?? []).forEach((task) => {
      rows.set(task.status, (rows.get(task.status) ?? 0) + 1);
    });
    return Array.from(rows.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);
  }, [tasks]);

  const progressDistribution = useMemo(() => {
    const bins = [0, 0, 0, 0, 0];
    (tasks ?? []).forEach((task) => {
      const p = task.progress;
      if (p >= 100) bins[4] += 1;
      else if (p >= 75) bins[3] += 1;
      else if (p >= 50) bins[2] += 1;
      else if (p >= 25) bins[1] += 1;
      else bins[0] += 1;
    });
    return bins;
  }, [tasks]);

  const summary = useMemo(() => {
    const total = tasks?.length ?? 0;
    if (total === 0) {
      return {
        total,
        running: 0,
        failed: 0,
        avgProgress: 0,
      };
    }

    const running = (tasks ?? []).filter((task) => task.status === "running").length;
    const failed = (tasks ?? []).filter((task) => task.status === "failed").length;
    const avgProgress = Math.round(
      (tasks ?? []).reduce((acc, task) => acc + task.progress, 0) / total,
    );

    return { total, running, failed, avgProgress };
  }, [tasks]);

  const statusOption = useMemo(
    () => ({
      tooltip: { trigger: "item" },
      legend: {
        top: 0,
        textStyle: { color: CHART_TOKENS.axisText, fontSize: 11 },
      },
      series: [
        {
          type: "pie",
          radius: ["42%", "72%"],
          center: ["50%", "44%"],
          label: {
            formatter: "{b}: {d}%",
            color: CHART_TOKENS.text,
            fontSize: 11,
          },
          labelLine: { length: 12, length2: 8 },
          data: statusChartData.map((row) => ({
            ...row,
            itemStyle: { color: statusColors[row.name] ?? "#9ca3af" },
          })),
        },
      ],
    }),
    [statusChartData],
  );

  const progressOption = useMemo(
    () => ({
      tooltip: { trigger: "axis" },
      grid: { left: 40, right: 20, top: 20, bottom: 36 },
      xAxis: {
        type: "category",
        data: ["0-24%", "25-49%", "50-74%", "75-99%", "100%"],
        axisLabel: { color: CHART_TOKENS.axisText, fontSize: 11 },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        axisLabel: { color: CHART_TOKENS.axisText, fontSize: 11 },
        splitLine: { lineStyle: { color: CHART_TOKENS.neutralSoft } },
      },
      series: [
        {
          type: "bar",
          barWidth: 30,
          data: progressDistribution,
          itemStyle: {
            borderRadius: [6, 6, 0, 0],
            color: CHART_TOKENS.primary,
          },
        },
      ],
    }),
    [progressDistribution],
  );

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="blue" className="w-fit">Operations</Badge>
        <Title className="text-2xl">{t(lang, "tasks")}</Title>
        <Text>Monitor and manage background tasks</Text>
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-3">
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
              (s) => (
                <option key={s} value={s}>{s}</option>
              ),
            )}
          </select>
          <select
            className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            <option value="">All types</option>
            {[
              "crawl_data",
              "process_data",
              "generate_report",
              "generate_section",
              "review_section",
              "export_data",
              "send_email",
              "update_disease_knowledge",
            ].map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
      </Card>

      {!isLoading && tasks && tasks.length > 0 && (
        <>
          <Grid numItems={1} numItemsSm={2} numItemsLg={4} className="gap-4">
            <Card>
              <Text>Total Tasks</Text>
              <Title>{summary.total}</Title>
            </Card>
            <Card>
              <Text>Running</Text>
              <Title className="text-amber-600 dark:text-amber-500">{summary.running}</Title>
            </Card>
            <Card>
              <Text>Failed</Text>
              <Title className="text-rose-600 dark:text-rose-500">{summary.failed}</Title>
            </Card>
            <Card>
              <Text>Avg Progress</Text>
              <Title>{summary.avgProgress}%</Title>
            </Card>
          </Grid>

          <Grid numItems={1} numItemsLg={2} className="gap-4">
            <Card>
              <Title className="mb-2">Task Status Distribution</Title>
              <Chart option={statusOption} height={280} />
            </Card>
            <Card>
              <Title className="mb-2">Progress Distribution</Title>
              <Chart option={progressOption} height={280} />
            </Card>
          </Grid>
        </>
      )}

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          ))}
        </div>
      ) : !tasks || tasks.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <ListTodo className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Text className="mt-3">{t(lang, "no_data")}</Text>
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {tasks.map((task) => (
            <Card key={task.task_uuid} className="p-0 overflow-hidden">
              <button
                className="flex w-full items-center gap-4 px-5 py-3.5 text-left text-sm transition-colors"
                style={{ background: expandedUuid === task.task_uuid ? "var(--color-tremor-background-subtle)" : "transparent" }}
                onClick={() =>
                  setExpandedUuid(
                    expandedUuid === task.task_uuid ? null : task.task_uuid,
                  )
                }
              >
                <Badge color={statusBadge[task.status] ?? "slate"}>{task.status}</Badge>
                <span className="flex-1 truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {task.task_name}
                </span>
                <Badge color="slate">{task.task_type}</Badge>

                <div className="w-28">
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
                <span className="w-10 text-right text-xs font-medium"
                  style={{ color: "var(--color-tremor-content-subtle)" }}>
                  {task.progress}%
                </span>
                <span className="w-28 text-right text-xs"
                  style={{ color: "var(--color-tremor-content-subtle)" }}>
                  {formatDate(task.created_at)}
                </span>
                <ChevronDown className={`h-4 w-4 transition-transform ${expandedUuid === task.task_uuid ? "rotate-180" : ""}`}
                  style={{ color: "var(--color-tremor-content-subtle)" }} />
              </button>

              {expandedUuid === task.task_uuid && (
                <div className="border-t border-tremor-border px-5 py-4 text-sm dark:border-dark-tremor-border">
                  <TaskDetailPanel taskDetail={taskDetail} detailLoading={detailLoading} />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
