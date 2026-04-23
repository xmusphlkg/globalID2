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
} from "@/lib/hooks/useTasks";
import {
  useDiseaseKnowledgeCatalogue,
  useStartAITask,
  useStartDiseaseKnowledgeTasks,
  type StartDiseaseKnowledgeTaskResult,
} from "@/lib/hooks/useAI";
import { useReports } from "@/lib/hooks/useReports";
import { formatDate } from "@/lib/utils";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { Chart } from "@/components/charts/Chart";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import {
  Cpu,
  ChevronDown,
  Search,
  MessageSquareText,
  Plus,
  X,
  Loader2,
  CheckCircle2,
  Settings2,
  Ban,
  BookOpen,
  CheckSquare2,
  RefreshCcw,
  Mail,
} from "lucide-react";
import { Badge, Card, Grid, Text, Title, Color } from "@tremor/react";
import { useSettings } from "@/lib/hooks/useSettings";

const AI_TYPES = "process_data,generate_report,generate_section,review_section,update_disease_knowledge";
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
  const [reportLanguage, setReportLanguage] = useState<"en" | "zh">("en");
  const [priority, setPriority] = useState<"low" | "normal" | "high" | "urgent">("normal");
  const [days, setDays] = useState(365);
  const [enableReview, setEnableReview] = useState(true);
  const [sendEmail, setSendEmail] = useState(false);
  const [reuseFromFailed, setReuseFromFailed] = useState(true);
  const [taskName, setTaskName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [createdTaskUuid, setCreatedTaskUuid] = useState<string | null>(null);
  const { mutate: startAITask, isPending, isSuccess } = useStartAITask();
  const { data: settings } = useSettings();

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
        language: reportLanguage,
        days,
        enable_review: enableReview,
        send_email: sendEmail,
        reuse_from_failed: reuseFromFailed,
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
              <label className={labelCls}>{lang === "zh" ? "报告语言" : "Report Language"}</label>
              <select
                value={reportLanguage}
                onChange={(e) => setReportLanguage(e.target.value as "en" | "zh")}
                className={inputCls}
              >
                <option value="en">English</option>
                <option value="zh">中文</option>
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
              <Text className="pl-6 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {settings?.smtp.alerting_ready
                  ? (lang === "zh" ? "SMTP 和收件人已就绪，完成邮件会发送到设置中心维护的邮箱列表。" : "SMTP and recipients are ready, so completion mail will go to the addresses managed in Settings.")
                  : (lang === "zh"
                    ? "SMTP 告警未配置，先去设置中心补齐凭据。"
                    : "SMTP alerts are not configured yet. Open Settings to finish setup.")}
              </Text>
              <label className="flex cursor-pointer items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                <input
                  type="checkbox"
                  checked={reuseFromFailed}
                  onChange={(e) => setReuseFromFailed(e.target.checked)}
                  className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted"
                />
                {lang === "zh" ? "从失败任务中复用已生成内容" : "Reuse generated content from failed tasks"}
              </label>
              <Text className="pl-6 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh"
                  ? "关闭后将强制重新生成，不继承失败/中断任务的中间结果。"
                  : "Turn off to force a fresh run without resuming failed/interrupted partial output."}
              </Text>
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

function CreateDiseaseKnowledgeTaskModal({
  open,
  lang,
  onClose,
}: {
  open: boolean;
  lang: "en" | "zh";
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedSources, setSelectedSources] = useState<string[]>(["who", "wikidata", "wikipedia"]);
  const [forceRefresh, setForceRefresh] = useState(true);
  const [generator, setGenerator] = useState<"ai" | "auto" | "template">("ai");
  const [priority, setPriority] = useState<"low" | "normal" | "high" | "urgent">("normal");
  const [taskName, setTaskName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [successResult, setSuccessResult] = useState<StartDiseaseKnowledgeTaskResult | null>(null);
  const { data: catalogue, isLoading } = useDiseaseKnowledgeCatalogue();
  const { mutate: startTasks, isPending, isSuccess } = useStartDiseaseKnowledgeTasks();

  const sourceOptions = [
    { value: "who", label: "WHO" },
    { value: "wikidata", label: "Wikidata" },
    { value: "wikipedia", label: "Wikipedia" },
    { value: "msd", label: "MSD metadata" },
  ] as const;

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return catalogue ?? [];
    return (catalogue ?? []).filter((item) => {
      return [
        item.disease_id,
        item.name_en,
        item.name_zh,
        item.category,
        item.description,
        item.slug,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    });
  }, [catalogue, search]);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedCount = selectedIds.length;
  const visibleSelectedCount = filtered.filter((item) => selectedSet.has(item.disease_id)).length;

  const toggleDisease = (diseaseId: string) => {
    setSelectedIds((current) =>
      current.includes(diseaseId) ? current.filter((id) => id !== diseaseId) : [...current, diseaseId],
    );
  };

  const toggleSource = (source: string) => {
    setSelectedSources((current) =>
      current.includes(source) ? current.filter((item) => item !== source) : [...current, source],
    );
  };

  const selectVisible = () => {
    setSelectedIds((current) => {
      const next = new Set(current);
      filtered.forEach((item) => next.add(item.disease_id));
      return Array.from(next);
    });
  };

  const clearSelection = () => setSelectedIds([]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccessResult(null);

    if (selectedIds.length === 0) {
      setError(lang === "zh" ? "请至少选择一个疾病。" : "Select at least one disease.");
      return;
    }

    startTasks(
      {
        disease_ids: selectedIds,
        source: selectedSources,
        force: forceRefresh,
        generator,
        priority,
        task_name: taskName.trim() || undefined,
        description: description.trim() || undefined,
      },
      {
        onSuccess: (result) => {
          setSuccessResult(result);
          window.setTimeout(onClose, 1800);
        },
        onError: (err: unknown) => {
          const msg = err instanceof Error ? err.message : String(err);
          setError(msg);
        },
      },
    );
  };

  if (!open) return null;

  const inputCls =
    "w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis";
  const labelCls =
    "mb-1 block text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 backdrop-blur-sm">
      <div className="relative w-full max-w-6xl rounded-tremor-default bg-tremor-background p-6 shadow-xl dark:bg-dark-tremor-background">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
        >
          <X className="h-5 w-5" />
        </button>

        <div className="mb-5 flex items-start gap-3">
          <div className="rounded-tremor-default border border-violet-200 bg-violet-50 p-2 text-violet-600 dark:border-violet-900/40 dark:bg-violet-950/30 dark:text-violet-300">
            <BookOpen className="h-5 w-5" />
          </div>
          <div>
            <Title className="text-xl">
              {lang === "zh" ? "新建疾病知识更新任务" : "New disease knowledge update task"}
            </Title>
            <Text className="mt-1">
              {lang === "zh"
                ? "选择一个或多个疾病，批量刷新知识库，并在任务列表里查看每个疾病的日志。"
                : "Select one or more diseases, queue them in batch, and inspect each disease task log from the task list."}
            </Text>
          </div>
        </div>

        {isSuccess && successResult ? (
          <div className="flex flex-col items-center gap-3 py-8 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-9 w-9" />
            <div className="text-center">
              <div className="text-sm font-medium">
                {lang === "zh"
                  ? `已创建 ${successResult.created_tasks.length} 个疾病知识任务`
                  : `Created ${successResult.created_tasks.length} disease knowledge task(s)`}
              </div>
              <div className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh"
                  ? "任务已进入队列，worker 会并行处理。"
                  : "Tasks are queued and the worker will process them in parallel."}
              </div>
            </div>
            <div className="w-full max-w-xl space-y-3">
              <div className="max-h-32 overflow-auto rounded-tremor-default border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">
                {successResult.created_tasks.map((task) => (
                  <div key={task.task_uuid} className="break-all font-mono">
                    {task.task_uuid}
                  </div>
                ))}
              </div>
              {successResult.skipped.length > 0 && (
                <div className="rounded-tremor-default border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
                  <div className="font-medium">
                    {lang === "zh" ? "跳过项" : "Skipped items"}
                  </div>
                  <div className="mt-2 space-y-1.5">
                    {successResult.skipped.map((item) => (
                      <div key={item.disease_id} className="flex flex-wrap gap-x-2 gap-y-1">
                        <span className="font-mono">{item.disease_id}</span>
                        <span>•</span>
                        <span>{item.reason}</span>
                        {item.existing_task_uuid && (
                          <>
                            <span>•</span>
                            <span className="font-mono">{item.existing_task_uuid}</span>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.95fr)]">
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative flex-1 min-w-[220px]">
                    <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
                    <input
                      type="search"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      className={`${inputCls} pl-9`}
                      placeholder={lang === "zh" ? "搜索疾病名称、编码或分类" : "Search disease name, code, or category"}
                    />
                  </div>
                  <button
                    type="button"
                    onClick={selectVisible}
                    className="inline-flex items-center gap-1 rounded-tremor-default border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
                  >
                    <CheckSquare2 className="h-4 w-4" />
                    {lang === "zh" ? "选择当前结果" : "Select filtered"}
                  </button>
                  <button
                    type="button"
                    onClick={clearSelection}
                    className="inline-flex items-center gap-1 rounded-tremor-default border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
                  >
                    <RefreshCcw className="h-4 w-4" />
                    {lang === "zh" ? "清空" : "Clear"}
                  </button>
                </div>

                <div className="rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
                  <div className="flex items-center justify-between gap-2 border-b border-tremor-border px-3 py-2 text-xs text-tremor-content-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle">
                    <span>
                      {lang === "zh"
                        ? `已选 ${selectedCount} / ${catalogue?.length ?? 0}，当前筛选命中 ${filtered.length}`
                        : `Selected ${selectedCount} / ${catalogue?.length ?? 0}, filtered ${filtered.length}`}
                    </span>
                    <span>
                      {lang === "zh"
                        ? `筛选已选 ${visibleSelectedCount}`
                        : `Selected in view ${visibleSelectedCount}`}
                    </span>
                  </div>
                  <div className="max-h-[31rem] overflow-auto">
                    {isLoading ? (
                      <div className="space-y-3 p-3">
                        {[1, 2, 3, 4].map((row) => (
                          <div key={row} className="h-16 animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
                        ))}
                      </div>
                    ) : filtered.length === 0 ? (
                      <div className="px-4 py-10 text-center text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "没有匹配的疾病。" : "No matching diseases."}
                      </div>
                    ) : (
                      <div className="divide-y divide-tremor-border dark:divide-dark-tremor-border">
                        {filtered.map((item) => {
                          const checked = selectedSet.has(item.disease_id);
                          const statusColor =
                            item.knowledge_status === "published"
                              ? "emerald"
                              : item.knowledge_status === "requires_review"
                                ? "amber"
                                : "slate";
                          return (
                            <label
                              key={item.disease_id}
                              className={`flex cursor-pointer items-start gap-3 px-4 py-3 transition-colors hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-subtle ${checked ? "bg-violet-50/60 dark:bg-violet-950/20" : ""}`}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleDisease(item.disease_id)}
                                className="mt-1 h-4 w-4 rounded border-tremor-border text-violet-600 focus:ring-violet-500"
                              />
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <div className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                    {item.name_en ?? item.disease_id}
                                  </div>
                                  <Badge color={statusColor as Color}>{item.knowledge_status}</Badge>
                                  <Badge color="slate">{item.disease_id}</Badge>
                                  {item.category && <Badge color="blue">{item.category}</Badge>}
                                  <Badge color="violet">{item.source_count} sources</Badge>
                                </div>
                                <div className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                  {item.name_zh ? `${item.name_zh} · ` : ""}
                                  {item.description ?? ""}
                                </div>
                                <div className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
                                  {item.published_languages.map((language) => (
                                    <span
                                      key={language}
                                      className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300"
                                    >
                                      {language.toUpperCase()}
                                    </span>
                                  ))}
                                  {item.knowledge_updated_at && (
                                    <span className="rounded-full border border-tremor-border px-2 py-0.5 text-tremor-content-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle">
                                      {item.knowledge_updated_at}
                                    </span>
                                  )}
                                </div>
                              </div>
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <div className="space-y-4">
                <div>
                  <label className={labelCls}>{lang === "zh" ? "批量任务名称（可选）" : "Batch task name (optional)"}</label>
                  <input
                    type="text"
                    value={taskName}
                    onChange={(e) => setTaskName(e.target.value)}
                    className={inputCls}
                    placeholder={lang === "zh" ? "例如：刷新重点疾病知识库" : "e.g. Refresh key disease knowledge"}
                  />
                </div>

                <div>
                  <label className={labelCls}>{lang === "zh" ? "任务说明（可选）" : "Description (optional)"}</label>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    rows={4}
                    className={inputCls}
                    placeholder={lang === "zh" ? "说明这批任务的范围或目标" : "Describe the purpose or scope of this batch"}
                  />
                </div>

                <div>
                  <label className={labelCls}>{lang === "zh" ? "生成器" : "Generator"}</label>
                  <select value={generator} onChange={(e) => setGenerator(e.target.value as typeof generator)} className={inputCls}>
                    <option value="ai">ai</option>
                    <option value="auto">auto</option>
                    <option value="template">template</option>
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

                <div className="space-y-2 rounded-tremor-default border border-tremor-border p-3 dark:border-dark-tremor-border">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-xs font-semibold uppercase tracking-[0.12em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {lang === "zh" ? "来源组" : "Source groups"}
                    </div>
                    <Badge color={selectedSources.length > 0 ? "emerald" : "slate"}>
                      {selectedSources.length}
                    </Badge>
                  </div>
                  <div className="space-y-2">
                    {sourceOptions.map((option) => (
                      <label key={option.value} className="flex cursor-pointer items-center gap-2 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                        <input
                          type="checkbox"
                          checked={selectedSources.includes(option.value)}
                          onChange={() => toggleSource(option.value)}
                          className="h-4 w-4 rounded border-tremor-border text-violet-600 focus:ring-violet-500"
                        />
                        {option.label}
                      </label>
                    ))}
                  </div>
                  <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {lang === "zh"
                      ? "WHO / Wikidata / Wikipedia 会优先用于公开 brief，MSD 只保存元数据和复核标记。"
                      : "WHO / Wikidata / Wikipedia are prioritized for public briefs; MSD stays metadata-only and review-gated."}
                  </Text>
                </div>

                <label className="flex cursor-pointer items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                  <input
                    type="checkbox"
                    checked={forceRefresh}
                    onChange={(e) => setForceRefresh(e.target.checked)}
                    className="h-4 w-4 rounded border-tremor-border text-violet-600 focus:ring-violet-500"
                  />
                  {lang === "zh" ? "强制刷新来源并重建 brief" : "Force refresh sources and rebuild briefs"}
                </label>

                {error && (
                  <div className="rounded-tremor-default border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
                    {error}
                  </div>
                )}

                <div className="rounded-tremor-default border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700 dark:border-slate-800 dark:bg-slate-950/30 dark:text-slate-300">
                  <div className="font-medium">
                    {lang === "zh" ? "并行更新说明" : "Parallel update note"}
                  </div>
                  <div className="mt-1">
                    {lang === "zh"
                      ? "任务创建后会进入统一队列，worker 会按并行度同时处理多个疾病任务。"
                      : "Once queued, the worker processes multiple disease tasks concurrently according to its configured parallelism."}
                  </div>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap justify-end gap-3 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="rounded-tremor-default border border-tremor-border px-4 py-2 text-sm text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle"
              >
                {lang === "zh" ? "取消" : "Cancel"}
              </button>
              <button
                type="submit"
                disabled={isPending || selectedIds.length === 0}
                className="flex items-center gap-2 rounded-tremor-default bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {lang === "zh" ? "排队更新" : "Queue Updates"}
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
          <button
            onClick={() => setKnowledgeModalOpen(true)}
            className="inline-flex items-center gap-1 rounded-lg border border-emerald-300/70 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 transition hover:bg-emerald-100 dark:border-emerald-900 dark:bg-emerald-950/25 dark:text-emerald-300"
          >
            <BookOpen className="h-3.5 w-3.5" />
            {lang === "zh" ? "更新疾病知识" : "Update Disease Knowledge"}
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

      <Card className={`border ${settings?.smtp.alerting_ready ? "border-emerald-200 dark:border-emerald-900/40" : "border-amber-200 dark:border-amber-900/40"}`}>
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-start gap-3">
            <div className={`rounded-xl p-2 ${settings?.smtp.alerting_ready ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/30 dark:text-emerald-300" : "bg-amber-50 text-amber-600 dark:bg-amber-950/30 dark:text-amber-300"}`}>
              <Mail className="h-5 w-5" />
            </div>
            <div>
              <Text className="text-xs font-semibold uppercase tracking-[0.18em] text-tremor-content-subtle">
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
            className="inline-flex items-center gap-2 rounded-full bg-tremor-brand px-4 py-2 text-sm font-semibold text-tremor-brand-inverted transition hover:opacity-90"
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
            {["process_data", "generate_report", "generate_section", "review_section", "update_disease_knowledge"].map(
              (tp) => (<option key={tp} value={tp}>{tp}</option>),
            )}
          </select>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
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
      </Card>

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
                className="rounded-lg border border-tremor-border px-3 py-1.5 text-sm text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              >
                {lang === "zh" ? "上一页" : "Previous"}
              </button>
              <span className="min-w-[96px] text-center text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {page} / {totalPages}
              </span>
              <button
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                disabled={page >= totalPages}
                className="rounded-lg border border-tremor-border px-3 py-1.5 text-sm text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
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
