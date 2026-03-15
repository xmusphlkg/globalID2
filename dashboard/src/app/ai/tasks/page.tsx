"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useTasks, useTaskDetail, useTaskWebSocket, useExecuteTask, useCancelTask } from "@/lib/hooks/useTasks";
import { useStartAITask } from "@/lib/hooks/useAI";
import { useReports } from "@/lib/hooks/useReports";
import { formatDate } from "@/lib/utils";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { Chart } from "@/components/charts/Chart";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { Cpu, ChevronDown, Search, MessageSquareText, Plus, X, Loader2, CheckCircle2, Settings2, Ban } from "lucide-react";
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

function CreateAITaskModal({
  open,
  countryId,
  lang,
  onClose,
}: {
  open: boolean;
  countryId: number;
  lang: "en" | "zh";
  onClose: () => void;
}) {
  const [reportType, setReportType] = useState<"daily" | "weekly" | "monthly" | "special">("monthly");
  const [priority, setPriority] = useState<"low" | "normal" | "high" | "urgent">("normal");
  const [days, setDays] = useState(365);
  const [enableReview, setEnableReview] = useState(true);
  const [sendEmail, setSendEmail] = useState(false);
  const [taskName, setTaskName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [createdTaskUuid, setCreatedTaskUuid] = useState<string | null>(null);
  const { mutate: startAITask, isPending, isSuccess } = useStartAITask();

  const inputCls =
    "w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis";
  const labelCls =
    "mb-1 block text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis";

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setCreatedTaskUuid(null);

    startAITask(
      {
        country_id: countryId,
        report_type: reportType,
        days,
        enable_review: enableReview,
        send_email: sendEmail,
        priority,
        task_name: taskName.trim() || undefined,
        description: description.trim() || undefined,
      },
      {
        onSuccess: (result) => {
          setCreatedTaskUuid(result.task_uuid);
          setTimeout(onClose, 1200);
        },
        onError: (err: unknown) => {
          const msg = err instanceof Error ? err.message : String(err);
          setError(msg);
        },
      },
    );
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="relative w-full max-w-md rounded-tremor-default bg-tremor-background p-6 shadow-xl dark:bg-dark-tremor-background">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
        >
          <X className="h-5 w-5" />
        </button>

        <Title className="mb-4">{lang === "zh" ? "新建 AI 任务" : "New AI Task"}</Title>

        {isSuccess ? (
          <div className="flex flex-col items-center gap-2 py-4 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-8 w-8" />
            <span className="text-sm font-medium">
              {lang === "zh" ? "任务创建成功并已开始执行" : "Task created and started"}
            </span>
            <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "可在下方任务列表实时追踪" : "Track progress in the task list below"}
            </span>
            {createdTaskUuid && (
              <div className="rounded-tremor-default border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">
                <div className="font-medium">{lang === "zh" ? "任务 UUID" : "Task UUID"}</div>
                <div className="mt-1 break-all font-mono">{createdTaskUuid}</div>
              </div>
            )}
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className={labelCls}>{lang === "zh" ? "报告类型" : "Report Type"}</label>
              <select value={reportType} onChange={(e) => setReportType(e.target.value as typeof reportType)} className={inputCls}>
                <option value="daily">daily</option>
                <option value="weekly">weekly</option>
                <option value="monthly">monthly</option>
                <option value="special">special</option>
              </select>
            </div>

            <div>
              <label className={labelCls}>{t(lang, "priority")}</label>
              <select value={priority} onChange={(e) => setPriority(e.target.value as typeof priority)} className={inputCls}>
                <option value="low">low</option>
                <option value="normal">normal</option>
                <option value="high">high</option>
                <option value="urgent">urgent</option>
              </select>
            </div>

            <div>
              <label className={labelCls}>{lang === "zh" ? "回溯天数" : "Lookback Days"}</label>
              <input
                type="number"
                min={1}
                max={3650}
                value={days}
                onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))}
                className={inputCls}
              />
            </div>

            <div>
              <label className={labelCls}>{lang === "zh" ? "任务名称（可选）" : "Task Name (optional)"}</label>
              <input
                type="text"
                value={taskName}
                onChange={(e) => setTaskName(e.target.value)}
                placeholder={lang === "zh" ? "例如：生成中国月报" : "e.g. Generate CN monthly report"}
                className={inputCls}
              />
            </div>

            <div>
              <label className={labelCls}>{lang === "zh" ? "描述（可选）" : "Description (optional)"}</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                className={inputCls}
                placeholder={lang === "zh" ? "输入任务说明" : "Describe this task"}
              />
            </div>

            <div className="space-y-2.5">
              <label className="flex cursor-pointer items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                <input
                  type="checkbox"
                  checked={enableReview}
                  onChange={(e) => setEnableReview(e.target.checked)}
                  className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted"
                />
                {lang === "zh" ? "启用 AI 审核" : "Enable AI review"}
              </label>
              <label className="flex cursor-pointer items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                <input
                  type="checkbox"
                  checked={sendEmail}
                  onChange={(e) => setSendEmail(e.target.checked)}
                  className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted"
                />
                {lang === "zh" ? "完成后发送邮件" : "Send email after completion"}
              </label>
            </div>

            {error && (
              <div className="rounded-tremor-default border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
                {error}
              </div>
            )}

            <div className="flex justify-end gap-3 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="rounded-tremor-default border border-tremor-border px-4 py-2 text-sm text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
              >
                {lang === "zh" ? "取消" : "Cancel"}
              </button>
              <button
                type="submit"
                disabled={isPending}
                className="flex items-center gap-2 rounded-tremor-default bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:opacity-60"
              >
                {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {lang === "zh" ? "创建并执行" : "Create & Run"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

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

export default function AIPage() {
  const { lang, countryId } = useAppStore();
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [expandedUuid, setExpandedUuid] = useState<string | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);

  useTaskWebSocket({
    extraQueryKeys: [["reports"], ["report-runs"], ["ai-interactions"], ["ai-interactions-summary"]],
  });

  const { data: tasks, isLoading } = useTasks(
    statusFilter || undefined,
    typeFilter || AI_TYPES,
    search || undefined,
    100,
  );

  const { data: taskDetail, isFetching: detailLoading } = useTaskDetail(expandedUuid);
  const { mutate: executeTask, isPending: executingTask } = useExecuteTask();
  const { mutate: cancelTask, isPending: cancellingTask } = useCancelTask();
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
    const reportUuid = parseReportUuid(outputData?.report_uuid);
    if (reportUuid) {
      return reportUuid;
    }

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
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button
            onClick={() => setCreateModalOpen(true)}
            disabled={!countryId}
            className="inline-flex items-center gap-1 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            <Plus className="h-3.5 w-3.5" />
            {lang === "zh" ? "新建 AI 任务" : "New AI Task"}
          </button>
          <Link
            href="/ai/interactions"
            className="inline-flex items-center gap-1 rounded-lg border border-violet-300/70 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:bg-violet-100 dark:border-violet-800 dark:bg-violet-950/25 dark:text-violet-300"
          >
            <MessageSquareText className="h-3.5 w-3.5" />
            Open AI Interactions
          </Link>
          <Link
            href="/ai/models"
            className="inline-flex items-center gap-1 rounded-lg border border-sky-300/70 bg-sky-50 px-3 py-1.5 text-xs font-medium text-sky-700 transition hover:bg-sky-100 dark:border-sky-900 dark:bg-sky-950/25 dark:text-sky-300"
          >
            <Settings2 className="h-3.5 w-3.5" />
            Open AI Models
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
                  style={{ color: "var(--color-tremor-content-subtle)" }}>{formatDate(task.created_at)}</span>
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
                        className="inline-flex items-center gap-1 rounded-lg border border-blue-300/70 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/25 dark:text-blue-300"
                      >
                        <MessageSquareText className="h-3.5 w-3.5" />
                        View this task in chat workflow
                      </Link>
                    </div>
                  )}
                  <div className="mb-3 flex flex-wrap gap-2">
                    {expandedUuid === task.task_uuid && ["pending", "queued", "failed", "cancelled"].includes(task.status) && (
                      <button
                        onClick={() => executeTask(task.task_uuid)}
                        disabled={executingTask}
                        className="inline-flex items-center gap-1 rounded-lg border border-amber-300/70 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-300"
                      >
                        {executingTask ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Cpu className="h-3.5 w-3.5" />}
                        {task.status === "cancelled" ? (lang === "zh" ? "从中断点继续" : "Resume Task") : (lang === "zh" ? "执行任务" : "Execute Task")}
                      </button>
                    )}
                    {expandedUuid === task.task_uuid && ["pending", "queued", "running"].includes(task.status) && (
                      <button
                        onClick={() => cancelTask(task.task_uuid)}
                        disabled={cancellingTask || task.cancel_requested}
                        className="inline-flex items-center gap-1 rounded-lg border border-rose-300/70 bg-rose-50 px-3 py-1.5 text-xs font-medium text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-rose-900 dark:bg-rose-950/25 dark:text-rose-300"
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

      {createModalOpen && countryId && (
        <CreateAITaskModal
          open={true}
          countryId={countryId}
          lang={lang}
          onClose={() => setCreateModalOpen(false)}
        />
      )}
    </div>
  );
}
