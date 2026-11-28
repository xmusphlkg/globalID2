"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Ban,
  BookOpen,
  CheckCircle2,
  Cpu,
  GitBranch,
  Loader2,
  MessageSquareText,
  Plus,
  Search,
  Settings2,
} from "lucide-react";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge as UiStatusBadge } from "@/components/ui/StatusBadge";
import { useSettings } from "@/features/admin/api";
import {
  useCancelTask,
  useExecuteTask,
  usePaginatedTasks,
  useTaskDetail,
  useTaskEventStream,
  useWorkerStatus,
} from "@/features/operations/tasks/api";
import { useReports } from "@/features/reports/api";
import { t } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";
import { CreateAITaskModal } from "./CreateAITaskModal";
import { CreateDiseaseKnowledgeTaskModal } from "./CreateDiseaseKnowledgeTaskModal";
import { SmtpAlertBanner } from "./components/SmtpAlertBanner";
import { TaskListSection } from "./components/TaskListSection";
import { TaskOverviewCharts } from "./components/TaskOverviewCharts";
import {
  AI_TASK_STATUS_OPTIONS,
  AI_TASK_TYPE_OPTIONS,
  AI_TYPES,
  TASK_PAGE_SIZE,
  buildReportUuidMap,
  buildStatusChartData,
  buildTypeChartData,
  computePaginationBounds,
  computeTaskSummary,
  resolveTaskReportUuid,
} from "./helpers";

function AIPageContent() {
  const { lang, countryId, countryCode } = useAppStore();
  const searchParams = useSearchParams();
  const searchParamsString = searchParams.toString();
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [expandedUuid, setExpandedUuid] = useState<string | null>(
    searchParams.get("task") ?? searchParams.get("task_uuid"),
  );
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [knowledgeModalOpen, setKnowledgeModalOpen] = useState(false);

  useTaskEventStream({
    extraQueryKeys: [
      ["reports"],
      ["report-runs"],
      ["ai-interactions"],
      ["ai-interactions-summary"],
      ["ai", "disease-knowledge", "catalogue"],
    ],
  });

  const offset = (page - 1) * TASK_PAGE_SIZE;
  const { data: taskPage, isLoading } = usePaginatedTasks(
    statusFilter || undefined,
    typeFilter || AI_TYPES,
    undefined,
    search || undefined,
    TASK_PAGE_SIZE,
    offset,
  );

  const tasks = taskPage?.items ?? [];
  const totalCount = taskPage?.totalCount ?? 0;
  const { totalPages, visibleStart, visibleEnd } = computePaginationBounds(
    page,
    totalCount,
    tasks.length,
    TASK_PAGE_SIZE,
  );

  const { data: taskDetail, isFetching: detailLoading } = useTaskDetail(expandedUuid);
  const { mutate: executeTask, isPending: executingTask } = useExecuteTask();
  const { mutate: cancelTask, isPending: cancellingTask } = useCancelTask();
  const { data: reports } = useReports(null, undefined, 200);
  const { data: settings } = useSettings();
  const { data: workerStatus } = useWorkerStatus();

  useEffect(() => {
    setStatusFilter(searchParams.get("status") ?? "");
    setTypeFilter(searchParams.get("task_type") ?? searchParams.get("type") ?? "");
    setSearch(
      searchParams.get("search") ?? searchParams.get("task") ?? searchParams.get("task_uuid") ?? "",
    );
    setExpandedUuid(searchParams.get("task") ?? searchParams.get("task_uuid") ?? null);
    setPage(1);
  }, [searchParamsString]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, typeFilter, search]);

  useEffect(() => {
    if (page > totalPages) {
      setPage(totalPages);
    }
  }, [page, totalPages]);

  const reportUuidById = useMemo(() => buildReportUuidMap(reports), [reports]);
  const activeTaskReportUuid = useMemo(
    () => resolveTaskReportUuid(taskDetail, reportUuidById),
    [reportUuidById, taskDetail],
  );
  const summary = useMemo(() => computeTaskSummary(tasks, totalCount), [tasks, totalCount]);
  const statusChartData = useMemo(() => buildStatusChartData(tasks), [tasks]);
  const typeChartData = useMemo(() => buildTypeChartData(tasks), [tasks]);

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_ai")}
        title={t(lang, "ai_tasks")}
        description={t(lang, "ai_tasks_subtitle")}
        meta={
          <>
            <UiStatusBadge tone={summary.failed > 0 ? "danger" : "success"}>
              {summary.failed > 0
                ? lang === "zh"
                  ? `${summary.failed} 个失败任务`
                  : `${summary.failed} failed`
                : lang === "zh"
                  ? "无失败任务"
                  : "No failures"}
            </UiStatusBadge>
            <UiStatusBadge tone={summary.running > 0 ? "warning" : "neutral"}>
              {summary.running > 0
                ? lang === "zh"
                  ? `${summary.running} 个运行中`
                  : `${summary.running} running`
                : lang === "zh"
                  ? "暂无运行任务"
                  : "Nothing running"}
            </UiStatusBadge>
          </>
        }
        actions={
          <>
            <button
              onClick={() => setCreateModalOpen(true)}
              disabled={!countryId}
              className="inline-flex h-10 items-center gap-2 rounded-tremor-default bg-tremor-brand px-3 text-sm font-medium text-tremor-brand-inverted transition hover:opacity-90 disabled:cursor-not-allowed disabled:bg-slate-400 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
            >
              <Plus className="h-4 w-4" />
              {lang === "zh" ? "新建 AI 任务" : "New AI Task"}
            </button>
            <button
              onClick={() => setKnowledgeModalOpen(true)}
              className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-emerald-300/70 bg-emerald-50 px-3 text-sm font-medium text-emerald-700 transition hover:bg-emerald-100 dark:border-emerald-900 dark:bg-emerald-950/25 dark:text-emerald-300"
            >
              <BookOpen className="h-4 w-4" />
              {lang === "zh" ? "更新疾病知识" : "Update Disease Knowledge"}
            </button>
            <Link
              href="/production/interactions"
              className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
            >
              <MessageSquareText className="h-4 w-4" />
              Open AI Interactions
            </Link>
            <Link
              href="/production/runs"
              className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
            >
              <GitBranch className="h-4 w-4" />
              {t(lang, "agent_runs")}
            </Link>
            <Link
              href="/settings/models"
              className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
            >
              <Settings2 className="h-4 w-4" />
              Open AI Models
            </Link>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={t(lang, "total_tasks")}
          value={summary.total}
          icon={<Cpu className="h-4 w-4" />}
          tone="neutral"
        />
        <MetricTile
          label={t(lang, "running_tasks")}
          value={summary.running}
          icon={<Loader2 className="h-4 w-4" />}
          tone="warning"
        />
        <MetricTile
          label={t(lang, "failed_tasks")}
          value={summary.failed}
          icon={<Ban className="h-4 w-4" />}
          tone={summary.failed > 0 ? "danger" : "success"}
        />
        <MetricTile
          label={t(lang, "avg_progress")}
          value={`${summary.avgProgress}%`}
          icon={<CheckCircle2 className="h-4 w-4" />}
          tone="primary"
        />
      </div>

      <SmtpAlertBanner lang={lang} alertingReady={settings?.smtp.alerting_ready} />

      {tasks.length > 0 && (
        <TaskOverviewCharts statusChartData={statusChartData} typeChartData={typeChartData} />
      )}

      <FilterToolbar>
        <div className="relative min-w-[200px] max-w-xs flex-1">
          <Search
            className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            style={{ color: "var(--color-tremor-content-subtle)" }}
          />
          <input
            type="text"
            placeholder="Search tasks..."
            className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          aria-label={lang === "zh" ? "任务状态筛选" : "Task status filter"}
          className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">All statuses</option>
          {AI_TASK_STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          aria-label={lang === "zh" ? "AI 任务类型筛选" : "AI task type filter"}
          className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="">All AI types</option>
          {AI_TASK_TYPE_OPTIONS.map((tp) => (
            <option key={tp} value={tp}>
              {tp}
            </option>
          ))}
        </select>
      </FilterToolbar>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-xs text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-subtle">
        <span>
          {lang === "zh"
            ? `显示第 ${visibleStart}-${visibleEnd} 条，共 ${totalCount} 条任务`
            : `Showing ${visibleStart}-${visibleEnd} of ${totalCount} tasks`}
        </span>
        <span>
          {lang === "zh" ? `当前页 ${page}/${totalPages}` : `Page ${page}/${totalPages}`}
        </span>
        {workerStatus && (
          <span>
            {lang === "zh"
              ? `Worker 并发 ${workerStatus.worker_concurrency}，AI ${workerStatus.ai_concurrency_current}/${workerStatus.ai_concurrency_max}${workerStatus.ai_concurrency_adaptive ? "（自适应）" : ""}，排队 ${workerStatus.queued_tasks}`
              : `Worker ${workerStatus.worker_concurrency}, AI ${workerStatus.ai_concurrency_current}/${workerStatus.ai_concurrency_max}${workerStatus.ai_concurrency_adaptive ? " (adaptive)" : ""}, queued ${workerStatus.queued_tasks}`}
          </span>
        )}
      </div>

      <TaskListSection
        lang={lang}
        isLoading={isLoading}
        tasks={tasks}
        totalCount={totalCount}
        page={page}
        totalPages={totalPages}
        visibleStart={visibleStart}
        visibleEnd={visibleEnd}
        expandedUuid={expandedUuid}
        taskDetail={taskDetail}
        detailLoading={detailLoading}
        activeTaskReportUuid={activeTaskReportUuid}
        executingTask={executingTask}
        cancellingTask={cancellingTask}
        onToggleExpand={(taskUuid) =>
          setExpandedUuid((current) => (current === taskUuid ? null : taskUuid))
        }
        onExecuteTask={executeTask}
        onCancelTask={cancelTask}
        onPreviousPage={() => setPage((current) => Math.max(1, current - 1))}
        onNextPage={() => setPage((current) => Math.min(totalPages, current + 1))}
      />

      {createModalOpen && countryId && countryCode && (
        <CreateAITaskModal
          open={true}
          countryCode={countryCode}
          lang={lang}
          onClose={() => setCreateModalOpen(false)}
        />
      )}

      {knowledgeModalOpen && (
        <CreateDiseaseKnowledgeTaskModal
          open={true}
          lang={lang}
          onClose={() => setKnowledgeModalOpen(false)}
        />
      )}
    </div>
  );
}

export default function AIPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-tremor-content-subtle">Loading...</div>}>
      <AIPageContent />
    </Suspense>
  );
}
