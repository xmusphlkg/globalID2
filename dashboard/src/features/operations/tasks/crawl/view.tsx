"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  Ban,
  CheckCircle2,
  Cpu,
  Download,
  ListTodo,
  Search,
  SlidersHorizontal,
  TimerReset,
} from "lucide-react";

import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  type TaskItem,
  useCancelTask,
  useTaskDetail,
  useTasks,
  useTaskEventStream,
  useWorkerStatus,
} from "@/features/operations/tasks/api";
import { t } from "@/lib/i18n";
import { formatDate, formatDateTime } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

const statusOptions = ["pending", "queued", "running", "completed", "failed", "cancelled", "retrying"];

const taskTypeOptions = [
  { value: "crawl_data", label: "Crawl only" },
  { value: "process_data", label: "Processing" },
  { value: "export_data", label: "Exports" },
];

const cancellableStatuses = new Set(["pending", "queued", "running", "retrying"]);

function taskTypeLabel(taskType: string, lang: "en" | "zh"): string {
  if (taskType === "crawl_data") return lang === "zh" ? "采集" : "Crawl";
  if (taskType === "process_data") return lang === "zh" ? "处理" : "Processing";
  if (taskType === "export_data") return lang === "zh" ? "导出" : "Export";
  return taskType;
}

function ProgressCell({ value, status }: { value: number; status: string }) {
  const clampedValue = Math.min(100, Math.max(0, value));
  const tone =
    status === "failed"
      ? "bg-rose-500"
      : clampedValue >= 100
        ? "bg-emerald-500"
        : "bg-teal-500";

  return (
    <div className="flex min-w-[140px] items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-tremor-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
        <div className={`h-full rounded-tremor-full ${tone}`} style={{ width: `${clampedValue}%` }} />
      </div>
      <span className="w-10 text-right text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {value}%
      </span>
    </div>
  );
}

function TaskNameCell({ task }: { task: TaskItem }) {
  return (
    <div className="min-w-[260px] max-w-[520px]">
      <p className="line-clamp-2 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
        {task.task_name}
      </p>
      <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {task.task_uuid}
      </p>
    </div>
  );
}

function CrawlTasksPageContent() {
  const { lang, countryId, countryCode } = useAppStore();
  const searchParams = useSearchParams();
  const searchParamsString = searchParams.toString();
  const [scopeMode, setScopeMode] = useState<"selected" | "all">(
    searchParams.get("scope") === "all" ? "all" : "selected",
  );
  const [statusFilter, setStatusFilter] = useState(searchParams.get("status") ?? "");
  const [taskTypeFilter, setTaskTypeFilter] = useState(searchParams.get("task_type") ?? "crawl_data");
  const [search, setSearch] = useState(searchParams.get("search") ?? searchParams.get("task") ?? "");
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const effectiveCountryCode = scopeMode === "all" ? null : countryCode;

  useTaskEventStream({ extraQueryKeys: [["sources-automation"], ["sources-flow"]] });

  const { data: tasks, isLoading } = useTasks(
    statusFilter || undefined,
    taskTypeFilter || undefined,
    effectiveCountryCode,
    search || undefined,
    200,
  );
  const { data: taskDetail, isFetching: detailLoading } = useTaskDetail(selectedUuid);
  const { data: workerStatus } = useWorkerStatus();
  const cancelTask = useCancelTask();

  useEffect(() => {
    setScopeMode(searchParams.get("scope") === "all" ? "all" : "selected");
    setStatusFilter(searchParams.get("status") ?? "");
    setTaskTypeFilter(searchParams.get("task_type") ?? "crawl_data");
    setSearch(searchParams.get("search") ?? searchParams.get("task") ?? "");
  }, [searchParamsString]);

  useEffect(() => {
    if (selectedUuid && tasks && !tasks.some((task) => task.task_uuid === selectedUuid)) {
      setSelectedUuid(null);
    }
  }, [selectedUuid, tasks]);

  const summary = useMemo(() => {
    const list = tasks ?? [];
    const total = list.length;
    const running = list.filter((task) => task.status === "running").length;
    const failed = list.filter((task) => task.status === "failed").length;
    const completed = list.filter((task) => task.status === "completed").length;
    return { total, running, failed, completed };
  }, [tasks]);

  const selectedTask = useMemo(
    () => tasks?.find((task) => task.task_uuid === selectedUuid) ?? null,
    [selectedUuid, tasks],
  );

  const hasFilters = Boolean(search || statusFilter || taskTypeFilter !== "crawl_data" || scopeMode !== "selected");

  const handleCancel = async (taskUuid: string) => {
    const ok = window.confirm(lang === "zh" ? "确认取消这个任务吗？" : "Cancel this task?");
    if (!ok) return;
    await cancelTask.mutateAsync(taskUuid);
  };

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
            {taskTypeLabel(task.task_type, lang)}
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
        key: "created",
        header: lang === "zh" ? "创建时间" : "Created",
        render: (task) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDateTime(task.created_at)}
          </span>
        ),
      },
      {
        key: "actions",
        header: "",
        className: "text-right",
        render: (task) => {
          const canCancel = cancellableStatuses.has(task.status) && !task.cancel_requested;
          return (
            <button
              type="button"
              className={`inline-flex h-8 items-center gap-2 rounded-tremor-default border px-2.5 text-xs font-medium transition ${
                canCancel
                  ? "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300 dark:hover:bg-rose-950/50"
                  : "cursor-not-allowed border-tremor-border bg-tremor-background-subtle text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle"
              }`}
              disabled={!canCancel || cancelTask.isPending}
              onClick={(event) => {
                event.stopPropagation();
                void handleCancel(task.task_uuid);
              }}
            >
              <Ban className="h-3.5 w-3.5" />
              {canCancel ? (task.cancel_requested ? "Cancelling" : "Cancel") : lang === "zh" ? "不可取消" : "Locked"}
            </button>
          );
        },
      },
    ],
    [cancelTask.isPending, lang],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_sources")}
        title={t(lang, "crawl_tasks")}
        description={
          lang === "zh"
            ? "集中查看数据采集任务、Worker 状态和异常队列，卡住的任务可以直接取消。"
            : "Monitor source ingestion tasks, worker health, and backlog; cancel stuck runs directly."
        }
        meta={
          <>
            <StatusBadge tone={workerStatus?.worker_process_running ? "success" : "danger"}>
              {workerStatus?.worker_process_running
                ? lang === "zh"
                  ? "Worker 运行中"
                  : "Worker running"
                : lang === "zh"
                  ? "Worker 未运行"
                  : "Worker stopped"}
            </StatusBadge>
            <StatusBadge tone={summary.failed > 0 ? "danger" : "neutral"}>
              {lang === "zh" ? `失败 ${summary.failed}` : `${summary.failed} failed`}
            </StatusBadge>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={t(lang, "total_tasks")}
          value={isLoading ? "-" : summary.total}
          icon={<ListTodo className="h-4 w-4" />}
          tone="neutral"
          hint={scopeMode === "all" ? (lang === "zh" ? "全部国家 / 地区" : "All countries / regions") : (lang === "zh" ? "当前国家 / 地区" : "Selected country / region")}
        />
        <MetricTile
          label={t(lang, "running_tasks")}
          value={isLoading ? "-" : summary.running}
          icon={<Activity className="h-4 w-4" />}
          tone="warning"
          hint={lang === "zh" ? "正在执行" : "In execution"}
        />
        <MetricTile
          label="Completed"
          value={isLoading ? "-" : summary.completed}
          icon={<CheckCircle2 className="h-4 w-4" />}
          tone="success"
          hint={lang === "zh" ? "采集已完成" : "Finished ingestion runs"}
        />
        <MetricTile
          label={t(lang, "failed_tasks")}
          value={isLoading ? "-" : summary.failed}
          icon={<AlertTriangle className="h-4 w-4" />}
          tone={summary.failed > 0 ? "danger" : "success"}
          hint={lang === "zh" ? "需要排查" : "Needs triage"}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={lang === "zh" ? "Worker 进程" : "Worker Process"}
          value={workerStatus?.worker_process_running ? (lang === "zh" ? "运行中" : "Running") : (lang === "zh" ? "未运行" : "Stopped")}
          icon={<Cpu className="h-4 w-4" />}
          tone={workerStatus?.worker_process_running ? "success" : "danger"}
          hint={workerStatus?.worker_pid ? `PID ${workerStatus.worker_pid}` : lang === "zh" ? "未检测到 PID" : "No PID detected"}
        />
        <MetricTile
          label={lang === "zh" ? "等待队列" : "Queued Backlog"}
          value={workerStatus?.queued_tasks ?? 0}
          tone="info"
          hint={lang === "zh" ? "等待 Worker 拉取" : "Waiting for worker pickup"}
        />
        <MetricTile
          label={lang === "zh" ? "活跃任务" : "Active Tasks"}
          value={workerStatus?.active_tasks ?? 0}
          tone="warning"
          hint={lang === "zh" ? "运行中 + 重试中" : "Running + retrying"}
        />
        <MetricTile
          label={lang === "zh" ? "最近启动" : "Latest Start"}
          value={workerStatus?.latest_started_at ? formatDate(workerStatus.latest_started_at) : "-"}
          tone="neutral"
          hint={lang === "zh" ? `并发度 ${workerStatus?.worker_concurrency ?? 1}` : `Concurrency ${workerStatus?.worker_concurrency ?? 1}`}
        />
      </div>

      <FilterToolbar>
        <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
          <input
            type="search"
            placeholder={lang === "zh" ? "搜索采集任务" : "Search source tasks"}
            className="h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <div className="flex items-center gap-1 rounded-tremor-default border border-tremor-border bg-tremor-background-subtle p-1 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <button
            type="button"
            onClick={() => setScopeMode("selected")}
            className={`h-8 rounded-tremor-default px-3 text-sm font-medium transition ${
              scopeMode === "selected"
                ? "bg-tremor-background text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
            }`}
          >
            {lang === "zh" ? "当前国家" : "Selected"}
          </button>
          <button
            type="button"
            onClick={() => setScopeMode("all")}
            className={`h-8 rounded-tremor-default px-3 text-sm font-medium transition ${
              scopeMode === "all"
                ? "bg-tremor-background text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
            }`}
          >
            {lang === "zh" ? "全部国家" : "All"}
          </button>
        </div>

        <div className="flex items-center gap-2 text-tremor-content-subtle">
          <SlidersHorizontal className="h-4 w-4" />
          <select
            className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={taskTypeFilter}
            onChange={(event) => setTaskTypeFilter(event.target.value)}
            aria-label={lang === "zh" ? "按任务类型筛选" : "Filter by task type"}
          >
            {taskTypeOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <select
          className="h-10 min-w-[150px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
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

        {hasFilters ? (
          <button
            type="button"
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            onClick={() => {
              setSearch("");
              setStatusFilter("");
              setTaskTypeFilter("crawl_data");
              setScopeMode("selected");
            }}
          >
            <TimerReset className="h-4 w-4" />
            {lang === "zh" ? "重置" : "Reset"}
          </button>
        ) : null}
      </FilterToolbar>

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
              icon={<Download className="h-10 w-10" />}
              title={t(lang, "no_data")}
              description={
                hasFilters
                  ? lang === "zh"
                    ? "当前筛选条件下没有采集任务。"
                    : "No source tasks match the current filters."
                  : lang === "zh"
                    ? "当前国家暂时没有采集任务。"
                    : "There are no source tasks for the selected country or region."
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
        <TaskDetailPanel
          taskDetail={taskDetail}
          detailLoading={detailLoading}
          emptyMessage={lang === "zh" ? "任务详情加载失败" : "Failed to load task details"}
        />
      </DetailDrawer>
    </div>
  );
}

export default function CrawlTasksPage() {
  return (
    <Suspense fallback={<div className="min-h-[40vh]" />}>
      <CrawlTasksPageContent />
    </Suspense>
  );
}
