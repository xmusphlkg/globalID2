"use client";

import { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  ListTodo,
  Search,
  SlidersHorizontal,
  TimerReset,
} from "lucide-react";

import { Chart } from "@/components/charts/Chart";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { type TaskItem, useTaskDetail, useTasks, useTaskWebSocket } from "@/features/operations/tasks/api";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

const statusOptions = ["pending", "queued", "running", "retrying", "completed", "failed", "cancelled"];

const typeOptions = [
  "crawl_data",
  "process_data",
  "generate_report",
  "generate_section",
  "review_section",
  "export_data",
  "send_email",
  "update_disease_knowledge",
];

const statusColors: Record<string, string> = {
  pending: CHART_TOKENS.neutral,
  queued: CHART_TOKENS.info,
  running: CHART_TOKENS.warning,
  completed: CHART_TOKENS.success,
  failed: CHART_TOKENS.destructive,
  cancelled: CHART_TOKENS.neutral,
  retrying: "#f97316",
};

function formatDuration(seconds?: number | null): string {
  if (!seconds && seconds !== 0) return "-";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  if (minutes < 60) return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

function ProgressCell({ value, status }: { value: number; status: string }) {
  const clampedValue = Math.min(100, Math.max(0, value));
  const tone =
    status === "failed"
      ? CHART_TOKENS.destructive
      : clampedValue >= 100
        ? CHART_TOKENS.success
        : CHART_TOKENS.primary;

  return (
    <div className="flex min-w-[140px] items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-tremor-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
        <div className="h-full rounded-tremor-full" style={{ width: `${clampedValue}%`, background: tone }} />
      </div>
      <span className="w-10 text-right text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {value}%
      </span>
    </div>
  );
}

function TaskNameCell({ task }: { task: TaskItem }) {
  return (
    <div className="min-w-[240px] max-w-[420px]">
      <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
        {task.task_name}
      </p>
      <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {task.task_uuid}
      </p>
    </div>
  );
}

export default function TasksPage() {
  const { lang, countryId } = useAppStore();
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);

  useTaskWebSocket();

  const { data: tasks, isLoading } = useTasks(
    statusFilter || undefined,
    typeFilter || undefined,
    countryId,
    search || undefined,
    100,
  );

  const selectedTask = useMemo(
    () => tasks?.find((task) => task.task_uuid === selectedUuid) ?? null,
    [selectedUuid, tasks],
  );
  const { data: taskDetail, isFetching: detailLoading } = useTaskDetail(selectedUuid);

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
      const progress = task.progress;
      if (progress >= 100) bins[4] += 1;
      else if (progress >= 75) bins[3] += 1;
      else if (progress >= 50) bins[2] += 1;
      else if (progress >= 25) bins[1] += 1;
      else bins[0] += 1;
    });
    return bins;
  }, [tasks]);

  const summary = useMemo(() => {
    const list = tasks ?? [];
    const total = list.length;
    const running = list.filter((task) => task.status === "running").length;
    const failed = list.filter((task) => task.status === "failed").length;
    const completed = list.filter((task) => task.status === "completed").length;
    const avgProgress = total
      ? Math.round(list.reduce((acc, task) => acc + task.progress, 0) / total)
      : 0;

    return { total, running, failed, completed, avgProgress };
  }, [tasks]);

  const hasFilters = Boolean(statusFilter || typeFilter || search);

  const columns = useMemo<DataTableColumn<TaskItem>[]>(
    () => [
      {
        key: "status",
        header: lang === "zh" ? "状态" : "Status",
        render: (task) => <StatusBadge status={task.status}>{task.status}</StatusBadge>,
      },
      {
        key: "task",
        header: lang === "zh" ? "任务" : "Task",
        render: (task) => <TaskNameCell task={task} />,
      },
      {
        key: "type",
        header: lang === "zh" ? "类型" : "Type",
        render: (task) => (
          <span className="whitespace-nowrap font-mono text-xs text-tremor-content dark:text-dark-tremor-content">
            {task.task_type}
          </span>
        ),
      },
      {
        key: "progress",
        header: lang === "zh" ? "进度" : "Progress",
        render: (task) => <ProgressCell value={task.progress} status={task.status} />,
      },
      {
        key: "country",
        header: lang === "zh" ? "国家" : "Country",
        render: (task) => (
          <div className="whitespace-nowrap">
            <span className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {task.country_code || "-"}
            </span>
            {task.country_name ? (
              <span className="ml-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {task.country_name}
              </span>
            ) : null}
          </div>
        ),
      },
      {
        key: "duration",
        header: lang === "zh" ? "耗时" : "Duration",
        render: (task) => (
          <span className="whitespace-nowrap text-tremor-content dark:text-dark-tremor-content">
            {formatDuration(task.actual_duration)}
          </span>
        ),
      },
      {
        key: "created",
        header: lang === "zh" ? "创建时间" : "Created",
        render: (task) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDateTime(task.created_at)}
          </span>
        ),
      },
    ],
    [lang],
  );

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
          radius: ["48%", "72%"],
          center: ["50%", "52%"],
          label: { show: false },
          data: statusChartData.map((row) => ({
            ...row,
            itemStyle: { color: statusColors[row.name] ?? CHART_TOKENS.neutral },
          })),
        },
      ],
    }),
    [statusChartData],
  );

  const progressOption = useMemo(
    () => ({
      tooltip: { trigger: "axis" },
      grid: { left: 36, right: 16, top: 18, bottom: 32 },
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
          barWidth: 28,
          data: progressDistribution,
          itemStyle: {
            borderRadius: [4, 4, 0, 0],
            color: CHART_TOKENS.primary,
          },
        },
      ],
    }),
    [progressDistribution],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={lang === "zh" ? "运维" : "Operations"}
        title={t(lang, "tasks")}
        description={
          lang === "zh"
            ? "集中查看后台任务状态、进度、失败原因和执行明细。"
            : "Monitor task status, progress, failures, and execution details from one console."
        }
        meta={
          <>
            <StatusBadge tone={summary.failed > 0 ? "danger" : "success"}>
              {summary.failed > 0
                ? lang === "zh"
                  ? `${summary.failed} 个失败任务`
                  : `${summary.failed} failed`
                : lang === "zh"
                  ? "无失败任务"
                  : "No failures"}
            </StatusBadge>
            <StatusBadge tone={summary.running > 0 ? "warning" : "neutral"}>
              {summary.running > 0
                ? lang === "zh"
                  ? `${summary.running} 个运行中`
                  : `${summary.running} running`
                : lang === "zh"
                  ? "暂无运行任务"
                  : "Nothing running"}
            </StatusBadge>
          </>
        }
      />

      <FilterToolbar>
        <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
          <input
            type="search"
            placeholder={lang === "zh" ? "搜索任务名称或 ID" : "Search task name or ID"}
            className="h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <div className="flex items-center gap-2 text-tremor-content-subtle">
          <SlidersHorizontal className="h-4 w-4" />
          <select
            className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            aria-label={lang === "zh" ? "按状态筛选" : "Filter by status"}
          >
            <option value="">{lang === "zh" ? "全部状态" : "All statuses"}</option>
            {statusOptions.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </div>

        <select
          className="h-10 min-w-[180px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          value={typeFilter}
          onChange={(event) => setTypeFilter(event.target.value)}
          aria-label={lang === "zh" ? "按任务类型筛选" : "Filter by task type"}
        >
          <option value="">{lang === "zh" ? "全部类型" : "All types"}</option>
          {typeOptions.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>

        {hasFilters ? (
          <button
            type="button"
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            onClick={() => {
              setSearch("");
              setStatusFilter("");
              setTypeFilter("");
            }}
          >
            <TimerReset className="h-4 w-4" />
            {lang === "zh" ? "重置" : "Reset"}
          </button>
        ) : null}
      </FilterToolbar>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={t(lang, "total_tasks")}
          value={isLoading ? "-" : summary.total}
          icon={<ListTodo className="h-4 w-4" />}
          tone="neutral"
          hint={lang === "zh" ? "当前筛选结果" : "Current result set"}
        />
        <MetricTile
          label={t(lang, "running_tasks")}
          value={isLoading ? "-" : summary.running}
          icon={<Activity className="h-4 w-4" />}
          tone="warning"
          hint={lang === "zh" ? "正在执行" : "In execution"}
        />
        <MetricTile
          label={t(lang, "failed_tasks")}
          value={isLoading ? "-" : summary.failed}
          icon={<AlertTriangle className="h-4 w-4" />}
          tone={summary.failed > 0 ? "danger" : "success"}
          hint={lang === "zh" ? "需要优先处理" : "Needs attention"}
        />
        <MetricTile
          label={lang === "zh" ? "平均进度" : "Average Progress"}
          value={isLoading ? "-" : `${summary.avgProgress}%`}
          icon={<CheckCircle2 className="h-4 w-4" />}
          tone="primary"
          hint={
            lang === "zh"
              ? `${summary.completed} 个已完成`
              : `${summary.completed} completed`
          }
        />
      </div>

      {!isLoading && tasks && tasks.length > 0 ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="app-panel p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {lang === "zh" ? "状态分布" : "Status Distribution"}
                </h2>
                <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {lang === "zh" ? "按任务状态聚合" : "Grouped by task status"}
                </p>
              </div>
              <Clock3 className="h-4 w-4 text-tremor-content-subtle" />
            </div>
            <Chart option={statusOption} height={220} />
          </section>

          <section className="app-panel p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {lang === "zh" ? "进度分布" : "Progress Distribution"}
                </h2>
                <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {lang === "zh" ? "按完成百分比分桶" : "Grouped by completion percentage"}
                </p>
              </div>
              <Activity className="h-4 w-4 text-tremor-content-subtle" />
            </div>
            <Chart option={progressOption} height={220} />
          </section>
        </div>
      ) : null}

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((item) => (
            <div
              key={item}
              className="h-16 animate-pulse rounded-tremor-default border border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background"
            />
          ))}
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={tasks ?? []}
          getRowKey={(task) => task.task_uuid}
          selectedRowKey={selectedUuid}
          onRowClick={(task) => setSelectedUuid(task.task_uuid)}
          emptyState={
            <EmptyState
              icon={<ListTodo className="h-10 w-10" />}
              title={t(lang, "no_data")}
              description={
                hasFilters
                  ? lang === "zh"
                    ? "当前筛选条件下没有任务。"
                    : "No tasks match the current filters."
                  : lang === "zh"
                    ? "当前国家暂时没有后台任务。"
                    : "There are no background tasks for the current country."
              }
            />
          }
        />
      )}

      <DetailDrawer
        open={Boolean(selectedUuid)}
        title={selectedTask?.task_name ?? (lang === "zh" ? "任务详情" : "Task Detail")}
        subtitle={
          selectedTask ? (
            <span className="flex min-w-0 items-center gap-2">
              <StatusBadge status={selectedTask.status}>{selectedTask.status}</StatusBadge>
              <span className="truncate font-mono text-xs">{selectedTask.task_uuid}</span>
              <span>{formatDateTime(selectedTask.created_at)}</span>
            </span>
          ) : null
        }
        onClose={() => setSelectedUuid(null)}
      >
        <TaskDetailPanel taskDetail={taskDetail} detailLoading={detailLoading} />
      </DetailDrawer>
    </div>
  );
}
