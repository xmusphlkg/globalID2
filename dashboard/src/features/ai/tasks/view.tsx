"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import {
  usePaginatedTasks,
  useTaskDetail,
  useTaskWebSocket,
  useExecuteTask,
  useCancelTask,
  useWorkerStatus,
} from "@/features/operations/tasks/api";
import { useReports } from "@/features/reports/api";
import { formatDateTime } from "@/lib/utils";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { Chart } from "@/components/charts/Chart";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge as UiStatusBadge } from "@/components/ui/StatusBadge";
import {
  Cpu,
  ChevronDown,
  Search,
  MessageSquareText,
  Plus,
  Loader2,
  CheckCircle2,
  Settings2,
  Ban,
  BookOpen,
  Mail,
  GitBranch,
} from "lucide-react";
import { Badge, Card, Grid, Text, Title, type Color } from "@/components/ui/tremor";
import { useSettings } from "@/features/admin/api";
import { CreateAITaskModal } from "./CreateAITaskModal";
import { CreateDiseaseKnowledgeTaskModal } from "./CreateDiseaseKnowledgeTaskModal";

const AI_TYPES = "process_data,generate_report,generate_section,review_section,update_disease_knowledge,agent_workflow";
const TASK_PAGE_SIZE = 100;

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

function parseReportUuid(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function AIPageContent() {
  const { lang, countryId } = useAppStore();
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

  useTaskWebSocket({
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
  const totalPages = Math.max(1, Math.ceil(totalCount / TASK_PAGE_SIZE));
  const visibleStart = totalCount === 0 ? 0 : offset + 1;
  const visibleEnd = totalCount === 0 ? 0 : offset + tasks.length;

  const { data: taskDetail, isFetching: detailLoading } = useTaskDetail(expandedUuid);
  const { mutate: executeTask, isPending: executingTask } = useExecuteTask();
  const { mutate: cancelTask, isPending: cancellingTask } = useCancelTask();
  const { data: reports } = useReports(null, undefined, 200);
  const { data: settings } = useSettings();
  const { data: workerStatus } = useWorkerStatus();

  useEffect(() => {
    setStatusFilter(searchParams.get("status") ?? "");
    setTypeFilter(searchParams.get("task_type") ?? searchParams.get("type") ?? "");
    setSearch(searchParams.get("search") ?? searchParams.get("task") ?? searchParams.get("task_uuid") ?? "");
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
    const reportUuid = parseReportUuid(outputData?.report_uuid);
    if (reportUuid) {
      return reportUuid;
    }

    const reportId = parseReportId(taskDetail.report_id) ?? parseReportId(outputData?.report_id);
    if (!reportId) return null;

    return reportUuidById.get(reportId) ?? null;
  }, [reportUuidById, taskDetail]);

  const summary = useMemo(() => {
    const total = totalCount;
    const running = (tasks ?? []).filter((t) => t.status === "running").length;
    const failed = (tasks ?? []).filter((t) => t.status === "failed").length;
    const avgProgress = total > 0
      ? Math.round((tasks ?? []).reduce((acc, t) => acc + t.progress, 0) / Math.max(tasks.length, 1))
      : 0;
    return { total, running, failed, avgProgress };
  }, [tasks, totalCount]);

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
            href="/ai/interactions"
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
          >
            <MessageSquareText className="h-4 w-4" />
            Open AI Interactions
          </Link>
          <Link
            href="/ai/agent-runs"
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
          >
            <GitBranch className="h-4 w-4" />
            {t(lang, "agent_runs")}
          </Link>
          <Link
            href="/ai/models"
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

      <Card className={`border ${settings?.smtp.alerting_ready ? "border-emerald-200 dark:border-emerald-900/40" : "border-amber-200 dark:border-amber-900/40"}`}>
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-start gap-3">
            <div className={`rounded-tremor-default p-2 ${settings?.smtp.alerting_ready ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/30 dark:text-emerald-300" : "bg-amber-50 text-amber-600 dark:bg-amber-950/30 dark:text-amber-300"}`}>
              <Mail className="h-5 w-5" />
            </div>
            <div>
              <Text className="text-xs font-semibold uppercase text-tremor-content-subtle">
                {lang === "zh" ? "SMTP 提醒" : "SMTP Alerts"}
              </Text>
              <Title className="mt-1 text-xl">
                {settings?.smtp.alerting_ready
                  ? (lang === "zh" ? "邮件提醒已就绪" : "Email alerts are ready")
                  : (lang === "zh" ? "需要补齐设置" : "Needs setup")}
              </Title>
              <Text className="mt-2 text-sm">
                {settings?.smtp.alerting_ready
                  ? (lang === "zh"
                    ? "AI 报告完成邮件，以及任务失败、取消等提醒，都会通过 SMTP 发送到设置中心维护的邮箱。"
                    : "AI report completion mail, plus failure and cancellation alerts, all go through SMTP to the Settings recipient list.")
                  : (lang === "zh"
                    ? "先到设置中心配置 SMTP 主机、密码和收件人。"
                    : "Open Settings to configure SMTP host, password, and recipients.")}
              </Text>
            </div>
          </div>
          <Link
            href="/setting"
            className="inline-flex items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 py-2 text-sm font-semibold text-tremor-brand-inverted transition hover:opacity-90"
          >
            {lang === "zh" ? "打开设置中心" : "Open Settings"}
            <Mail className="h-4 w-4" />
          </Link>
        </div>
      </Card>

      {tasks.length > 0 && (
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

      <FilterToolbar>
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
            {["process_data", "generate_report", "generate_section", "review_section", "update_disease_knowledge", "agent_workflow"].map(
              (tp) => (<option key={tp} value={tp}>{tp}</option>),
            )}
          </select>
      </FilterToolbar>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-xs text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-subtle">
          <span>
            {lang === "zh"
              ? `显示第 ${visibleStart}-${visibleEnd} 条，共 ${totalCount} 条任务`
              : `Showing ${visibleStart}-${visibleEnd} of ${totalCount} tasks`}
          </span>
          <span>
            {lang === "zh"
              ? `当前页 ${page}/${totalPages}`
              : `Page ${page}/${totalPages}`}
          </span>
          {workerStatus && (
            <span>
              {lang === "zh"
                ? `Worker 并发 ${workerStatus.worker_concurrency}，排队 ${workerStatus.queued_tasks}`
              : `Worker concurrency ${workerStatus.worker_concurrency}, queued ${workerStatus.queued_tasks}`}
            </span>
          )}
      </div>

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          ))}
        </div>
      ) : tasks.length === 0 ? (
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
                {task.cancel_requested && task.status === "running" && <Badge color="amber">cancelling</Badge>}
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{task.task_name}</div>
                  <div className="truncate font-mono text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    UUID: {task.task_uuid}
                  </div>
                </div>
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
                  style={{ color: "var(--color-tremor-content-subtle)" }}>{formatDateTime(task.created_at)}</span>
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform ${expandedUuid === task.task_uuid ? "rotate-180" : ""}`}
                  style={{ color: "var(--color-tremor-content-subtle)" }}
                />
              </button>

              {expandedUuid === task.task_uuid && (
                <div className="border-t border-tremor-border px-4 py-3 text-[13px] dark:border-dark-tremor-border">
                  {expandedUuid === task.task_uuid && (
                    <div className="mb-3">
                      <Link
                        href={`/ai/interactions?task=${encodeURIComponent(task.task_uuid)}${activeTaskReportUuid ? `&uuid=${encodeURIComponent(activeTaskReportUuid)}` : ""}`}
                        className="inline-flex items-center gap-1 rounded-tremor-default border border-blue-300/70 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/25 dark:text-blue-300"
                      >
                        <MessageSquareText className="h-3.5 w-3.5" />
                        View this task in chat workflow
                      </Link>
                    </div>
                  )}
                  <div className="mb-3 flex flex-wrap gap-2">
                    {expandedUuid === task.task_uuid && task.task_type === "agent_workflow" && (
                      <Link
                        href={`/ai/agent-runs?task_uuid=${encodeURIComponent(task.task_uuid)}`}
                        className="inline-flex items-center gap-1 rounded-tremor-default border border-cyan-300/70 bg-cyan-50 px-3 py-1.5 text-xs font-medium text-cyan-700 transition hover:bg-cyan-100 dark:border-cyan-900 dark:bg-cyan-950/25 dark:text-cyan-300"
                      >
                        <GitBranch className="h-3.5 w-3.5" />
                        {lang === "zh" ? "查看 Agent Run" : "View Agent Run"}
                      </Link>
                    )}
                    {expandedUuid === task.task_uuid && ["pending", "queued", "failed", "cancelled"].includes(task.status) && (
                      <button
                        onClick={() => executeTask(task.task_uuid)}
                        disabled={executingTask}
                        className="inline-flex items-center gap-1 rounded-tremor-default border border-amber-300/70 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-300"
                      >
                        {executingTask ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Cpu className="h-3.5 w-3.5" />}
                        {task.status === "cancelled" ? (lang === "zh" ? "从中断点继续" : "Resume Task") : (lang === "zh" ? "执行任务" : "Execute Task")}
                      </button>
                    )}
                    {expandedUuid === task.task_uuid && ["pending", "queued", "running"].includes(task.status) && (
                      <button
                        onClick={() => cancelTask(task.task_uuid)}
                        disabled={cancellingTask || task.cancel_requested}
                        className="inline-flex items-center gap-1 rounded-tremor-default border border-rose-300/70 bg-rose-50 px-3 py-1.5 text-xs font-medium text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-rose-900 dark:bg-rose-950/25 dark:text-rose-300"
                      >
                        {cancellingTask ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Ban className="h-3.5 w-3.5" />}
                        {task.cancel_requested
                          ? (lang === "zh" ? "取消请求已发送" : "Cancellation Requested")
                          : (lang === "zh" ? "取消任务" : "Cancel Task")}
                      </button>
                    )}
                  </div>
                  <TaskDetailPanel taskDetail={taskDetail} detailLoading={detailLoading} emptyMessage="Task detail unavailable." />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      {totalCount > TASK_PAGE_SIZE && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh"
                ? `每页 ${TASK_PAGE_SIZE} 条，当前显示 ${visibleStart}-${visibleEnd}`
                : `${TASK_PAGE_SIZE} per page, currently showing ${visibleStart}-${visibleEnd}`}
            </Text>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                disabled={page <= 1}
                className="rounded-tremor-default border border-tremor-border px-3 py-1.5 text-sm text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              >
                {lang === "zh" ? "上一页" : "Previous"}
              </button>
              <span className="min-w-[96px] text-center text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {page} / {totalPages}
              </span>
              <button
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                disabled={page >= totalPages}
                className="rounded-tremor-default border border-tremor-border px-3 py-1.5 text-sm text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              >
                {lang === "zh" ? "下一页" : "Next"}
              </button>
            </div>
          </div>
        </Card>
      )}

      {createModalOpen && countryId && (
        <CreateAITaskModal
          open={true}
          countryId={countryId}
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
