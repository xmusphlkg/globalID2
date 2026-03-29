"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useCancelTask, useTaskDetail, useTasks, useTaskWebSocket, useWorkerStatus } from "@/lib/hooks/useTasks";
import { formatDate } from "@/lib/utils";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { Activity, Ban, ChevronDown, Cpu, Download, Search } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  Color,
  Grid,
  Metric,
  ProgressBar,
  Text,
} from "@tremor/react";

const statusBadge: Record<string, Color> = {
  pending: "slate",
  queued: "blue",
  running: "yellow",
  completed: "emerald",
  failed: "rose",
  cancelled: "slate",
  retrying: "yellow",
};

const taskTypeOptions = [
  { value: "crawl_data", label: "Crawl only" },
  { value: "process_data", label: "Processing" },
  { value: "export_data", label: "Exports" },
];

const cancellableStatuses = new Set(["pending", "queued", "running", "retrying"]);

function taskTypeLabel(taskType: string, lang: "en" | "zh"): string | null {
  if (taskType === "crawl_data") return null;
  if (taskType === "process_data") return lang === "zh" ? "处理" : "Processing";
  if (taskType === "export_data") return lang === "zh" ? "导出" : "Export";
  return taskType;
}

function CrawlTasksPageContent() {
  const { lang, countryId } = useAppStore();
  const searchParams = useSearchParams();
  const searchParamsString = searchParams.toString();
  const [scopeMode, setScopeMode] = useState<"selected" | "all">(
    searchParams.get("scope") === "all" ? "all" : "selected",
  );
  const [statusFilter, setStatusFilter] = useState(searchParams.get("status") ?? "");
  const [taskTypeFilter, setTaskTypeFilter] = useState(searchParams.get("task_type") ?? "crawl_data");
  const [search, setSearch] = useState(searchParams.get("search") ?? searchParams.get("task") ?? "");
  const [expandedUuid, setExpandedUuid] = useState<string | null>(null);
  const effectiveCountryId = scopeMode === "all" ? null : countryId;

  useTaskWebSocket({ extraQueryKeys: [["sources-automation"], ["sources-flow"]] });

  const { data: tasks, isLoading } = useTasks(
    statusFilter || undefined,
    taskTypeFilter || undefined,
    effectiveCountryId,
    search || undefined,
    200,
  );
  const { data: taskDetail, isFetching: detailLoading } = useTaskDetail(expandedUuid);
  const { data: workerStatus } = useWorkerStatus();
  const cancelTask = useCancelTask();

  useEffect(() => {
    setScopeMode(searchParams.get("scope") === "all" ? "all" : "selected");
    setStatusFilter(searchParams.get("status") ?? "");
    setTaskTypeFilter(searchParams.get("task_type") ?? "crawl_data");
    setSearch(searchParams.get("search") ?? searchParams.get("task") ?? "");
  }, [searchParamsString]);

  const summary = useMemo(() => {
    const total = tasks?.length ?? 0;
    const running = (tasks ?? []).filter((t) => t.status === "running").length;
    const failed = (tasks ?? []).filter((t) => t.status === "failed").length;
    const completed = (tasks ?? []).filter((t) => t.status === "completed").length;
    return { total, running, failed, completed };
  }, [tasks]);

  const handleCancel = async (taskUuid: string) => {
    const ok = window.confirm(
      lang === "zh"
        ? "确认取消这个任务吗？"
        : "Cancel this task?",
    );
    if (!ok) return;
    await cancelTask.mutateAsync(taskUuid);
  };

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="teal" className="w-fit">{t(lang, "mod_sources")}</Badge>
        <h1 className="text-3xl font-semibold tracking-tight text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {t(lang, "crawl_tasks")}
        </h1>
        <Text>
          {lang === "zh"
            ? "查看数据采集相关任务，筛选状态，并在任务卡住时直接取消。"
            : "Review source-ingestion tasks, filter by status, and cancel stuck runs directly from this page."}
        </Text>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-tremor-border bg-tremor-background p-1.5 shadow-sm">
        <button
          onClick={() => setScopeMode("selected")}
          className={`rounded-xl px-4 py-2 text-sm font-medium transition ${scopeMode === "selected" ? "bg-tremor-brand text-tremor-brand-inverted" : "text-tremor-content-strong"}`}
        >
          {lang === "zh" ? "当前国家" : "Selected country"}
        </button>
        <button
          onClick={() => setScopeMode("all")}
          className={`rounded-xl px-4 py-2 text-sm font-medium transition ${scopeMode === "all" ? "bg-tremor-brand text-tremor-brand-inverted" : "text-tremor-content-strong"}`}
        >
          {lang === "zh" ? "全部国家" : "All countries"}
        </button>
      </div>

      <Grid numItemsSm={2} numItemsLg={4} className="gap-4">
        <Card decoration="top" decorationColor="blue">
          <Text>{t(lang, "total_tasks")}</Text>
          <Metric>{summary.total}</Metric>
        </Card>
        <Card decoration="top" decorationColor="amber">
          <Text>{t(lang, "running_tasks")}</Text>
          <Metric>{summary.running}</Metric>
        </Card>
        <Card decoration="top" decorationColor="emerald">
          <Text>Completed</Text>
          <Metric>{summary.completed}</Metric>
        </Card>
        <Card decoration="top" decorationColor="rose">
          <Text>{t(lang, "failed_tasks")}</Text>
          <Metric>{summary.failed}</Metric>
        </Card>
      </Grid>

      <Grid numItemsSm={2} numItemsLg={4} className="gap-4">
        <Card className="border border-tremor-border">
          <div className="flex items-center justify-between gap-3">
            <div>
              <Text>{lang === "zh" ? "Worker 进程" : "Worker process"}</Text>
              <Metric>{workerStatus?.worker_process_running ? (lang === "zh" ? "运行中" : "Running") : (lang === "zh" ? "未运行" : "Stopped")}</Metric>
            </div>
            <Cpu className={`h-8 w-8 ${workerStatus?.worker_process_running ? "text-emerald-500" : "text-rose-500"}`} />
          </div>
          <Text className="mt-2 text-xs">
            {workerStatus?.worker_pid ? `PID ${workerStatus.worker_pid}` : (lang === "zh" ? "未检测到 worker pid" : "No worker pid detected")}
          </Text>
        </Card>
        <Card className="border border-tremor-border">
          <Text>{lang === "zh" ? "等待队列" : "Queued backlog"}</Text>
          <Metric>{workerStatus?.queued_tasks ?? 0}</Metric>
          <Text className="mt-2 text-xs">{lang === "zh" ? "等待 worker 拉取执行" : "Waiting for worker pickup"}</Text>
        </Card>
        <Card className="border border-tremor-border">
          <Text>{lang === "zh" ? "活跃任务" : "Active tasks"}</Text>
          <Metric>{workerStatus?.active_tasks ?? 0}</Metric>
          <Text className="mt-2 text-xs">{lang === "zh" ? "运行中 + 重试中" : "Running + retrying"}</Text>
        </Card>
        <Card className="border border-tremor-border">
          <Text>{lang === "zh" ? "最近启动" : "Latest start"}</Text>
          <Metric className="text-xl">{workerStatus?.latest_started_at ? formatDate(workerStatus.latest_started_at) : "-"}</Metric>
          <Text className="mt-2 text-xs">{lang === "zh" ? "用于判断 worker 最近是否在消费任务" : "Useful to confirm recent worker activity"}</Text>
        </Card>
      </Grid>

      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[220px] flex-1">
            <Search
              className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle"
              aria-hidden="true"
            />
            <input
              type="text"
              placeholder={lang === "zh" ? "搜索采集任务..." : "Search source tasks..."}
              className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-emphasis shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis dark:focus:border-dark-tremor-brand-subtle dark:focus:ring-dark-tremor-brand-muted"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select
            className="min-w-[170px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis dark:focus:border-dark-tremor-brand-subtle dark:focus:ring-dark-tremor-brand-muted"
            value={taskTypeFilter}
            onChange={(e) => setTaskTypeFilter(e.target.value)}
          >
            {taskTypeOptions.map((option) => (
              <option key={option.value || "all"} value={option.value}>{option.label}</option>
            ))}
          </select>
          <select
            className="min-w-[170px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis dark:focus:border-dark-tremor-brand-subtle dark:focus:ring-dark-tremor-brand-muted"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">All statuses</option>
            {["pending", "queued", "running", "completed", "failed", "cancelled", "retrying"].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        <div className="mt-4 space-y-3">
          {isLoading ? (
            [1, 2, 3].map((i) => (
              <div key={i} className="h-16 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
            ))
          ) : !tasks || tasks.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-tremor-default border border-dashed border-tremor-border p-10 text-center dark:border-dark-tremor-border">
              <Download className="mb-3 h-10 w-10 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
              <Text>{t(lang, "no_data")}</Text>
            </div>
          ) : (
            tasks.map((task) => {
              const expanded = expandedUuid === task.task_uuid;
              const canCancel = cancellableStatuses.has(task.status) && !task.cancel_requested;
              const typeLabel = taskTypeLabel(task.task_type, lang);
              return (
                <Card key={task.task_uuid} className="p-0">
                  <div className="flex items-center gap-3 px-4 py-3">
                    <button
                      className="min-w-0 flex-1 rounded-tremor-default text-left transition hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-subtle"
                      onClick={() => setExpandedUuid(expanded ? null : task.task_uuid)}
                    >
                      <div className="flex min-w-0 items-center gap-3 rounded-tremor-default px-2 py-1.5">
                        <div className="flex shrink-0 flex-wrap items-center gap-2">
                          <Badge color={statusBadge[task.status] ?? "slate"}>{task.status}</Badge>
                          {typeLabel ? <Badge color="slate">{typeLabel}</Badge> : null}
                          {scopeMode === "all" ? (
                            <Badge color="teal">{task.country_code || task.country_name || "-"}</Badge>
                          ) : null}
                        </div>
                        <span className="min-w-0 flex-1 truncate text-sm font-medium leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                          {task.task_name}
                        </span>
                        <div className="hidden shrink-0 items-center gap-2 md:flex md:w-40">
                          <ProgressBar value={task.progress} color={task.progress === 100 ? "emerald" : "teal"} className="flex-1" />
                          <Text>{task.progress}%</Text>
                        </div>
                        <Text className="hidden shrink-0 md:block">{formatDate(task.created_at)}</Text>
                        <ChevronDown className={`h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform ${expanded ? "rotate-180" : ""}`} />
                      </div>
                    </button>
                    <div className="flex shrink-0 items-center">
                      <Button
                        size="xs"
                        color={canCancel ? "rose" : "slate"}
                        variant={canCancel ? "secondary" : "light"}
                        disabled={!canCancel || cancelTask.isPending}
                        icon={Ban}
                        className={canCancel ? "" : "opacity-55"}
                        onClick={() => handleCancel(task.task_uuid)}
                      >
                        {canCancel ? (task.cancel_requested ? "Cancelling" : "Cancel") : ""}
                      </Button>
                    </div>
                  </div>

                  {expanded && (
                    <div className="border-t border-tremor-border px-4 pb-4 pt-3 dark:border-dark-tremor-border">
                      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-tremor-content md:hidden">
                        <span>{task.progress}%</span>
                        <span>{formatDate(task.created_at)}</span>
                      </div>
                      {scopeMode === "all" ? (
                        <div className="mb-3 flex items-center gap-2 text-xs text-tremor-content">
                          <Activity className="h-4 w-4" />
                          <span>{lang === "zh" ? "国家" : "Country"}: {task.country_name || task.country_code || "-"}</span>
                        </div>
                      ) : null}
                      <TaskDetailPanel taskDetail={taskDetail} detailLoading={detailLoading} emptyMessage="Failed to load task details" />
                    </div>
                  )}
                </Card>
              );
            })
          )}
        </div>
      </Card>
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
