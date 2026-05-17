"use client";

import { Suspense, useEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Badge, Card, Grid, Text, Title, type Color } from "@tremor/react";
import {
  Ban,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Cpu,
  Database,
  ExternalLink,
  FileText,
  Loader2,
  MessageSquareText,
  RefreshCcw,
  Search,
} from "lucide-react";

import { ApiError } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { useTaskWebSocket } from "@/lib/hooks/useTasks";
import {
  useAgentRuns,
  useAgentRunDetail,
  useCancelAgentRun,
  useResumeAgentRun,
  type AgentRunSummary,
  type AgentWorkflowConversation,
  type AgentWorkflowEvidence,
  type AgentWorkflowMemory,
  type AgentWorkflowStep,
} from "@/lib/hooks/useAgentRuns";

type Copy = {
  title: string;
  subtitle: string;
  refresh: string;
  openTasks: string;
  searchPlaceholder: string;
  statusAll: string;
  statusFilters: string[];
  listTitle: string;
  listHint: string;
  noRuns: string;
  noRunsHint: string;
  noSelection: string;
  taskLogs: string;
  start: string;
  resume: string;
  cancel: string;
  summary: string;
  objective: string;
  runDigest: string;
  plan: string;
  steps: string;
  evidence: string;
  findings: string;
  citations: string;
  actions: string;
  artifacts: string;
  openQuestions: string;
  conversations: string;
  memories: string;
  auditTrail: string;
  rawJson: string;
  taskAudit: string;
  taskSummary: string;
  inputData: string;
  outputData: string;
  taskUuid: string;
  taskName: string;
  status: string;
  priority: string;
  country: string;
  mode: string;
  outputFormat: string;
  risk: string;
  budget: string;
  evidenceCount: string;
  findingCount: string;
  stepCount: string;
  confidence: string;
  tokenUsage: string;
  started: string;
  ended: string;
  prompt: string;
  summaryFallback: string;
  emptyHint: string;
  details: string;
  viewTaskDetail: string;
  loading: string;
  currentScope: string;
};

const STATUS_FILTERS = ["pending", "queued", "running", "completed", "failed", "cancelled"];
const LIST_LIMIT = 30;

const STATUS_BADGE: Record<string, Color> = {
  pending: "slate",
  queued: "blue",
  running: "amber",
  completed: "emerald",
  failed: "rose",
  cancelled: "slate",
  retrying: "amber",
};

const RISK_BADGE: Record<string, Color> = {
  low: "emerald",
  medium: "amber",
  high: "rose",
  critical: "rose",
};

const STEP_BADGE: Record<string, Color> = {
  web_search: "blue",
  db_lookup: "cyan",
  memory_lookup: "violet",
  analysis: "indigo",
  internal_action: "amber",
  review: "teal",
  finalize: "emerald",
};

const EVIDENCE_BADGE: Record<string, Color> = {
  web: "blue",
  db: "cyan",
  memory: "violet",
  action: "amber",
};

const EN_COPY: Copy = {
  title: "Agent Runs",
  subtitle: "Inspect the multi-expert workflow graph, evidence trail, and audit logs for each run.",
  refresh: "Refresh",
  openTasks: "Open AI Tasks",
  searchPlaceholder: "Search task name, prompt, UUID, summary, or evidence...",
  statusAll: "All",
  statusFilters: ["Pending", "Queued", "Running", "Completed", "Failed", "Cancelled"],
  listTitle: "Recent runs",
  listHint: "Pick a run to inspect its plan, steps, evidence, and workbook trail.",
  noRuns: "No agent runs found.",
  noRunsHint: "Create an AGENT_WORKFLOW task first, then return here to inspect it.",
  noSelection: "Select a run from the left panel to view the full audit trail.",
  taskLogs: "Task logs",
  start: "Start",
  resume: "Resume",
  cancel: "Cancel",
  summary: "Summary",
  objective: "Objective",
  runDigest: "Run digest",
  plan: "Plan",
  steps: "Steps",
  evidence: "Evidence",
  findings: "Findings",
  citations: "Citations",
  actions: "Actions",
  artifacts: "Artifacts",
  openQuestions: "Open questions",
  conversations: "Conversations",
  memories: "Memories",
  auditTrail: "Audit trail",
  rawJson: "Raw JSON",
  taskAudit: "Task audit",
  taskSummary: "Task summary",
  inputData: "Input data",
  outputData: "Output data",
  taskUuid: "Task UUID",
  taskName: "Task name",
  status: "Status",
  priority: "Priority",
  country: "Country",
  mode: "Mode",
  outputFormat: "Output format",
  risk: "Risk",
  budget: "Token budget",
  evidenceCount: "Evidence",
  findingCount: "Findings",
  stepCount: "Steps",
  confidence: "Confidence",
  tokenUsage: "Token usage",
  started: "Started",
  ended: "Ended",
  prompt: "Prompt",
  summaryFallback: "No summary yet.",
  emptyHint: "No run detail available.",
  details: "Details",
  viewTaskDetail: "View task logs",
  loading: "Loading agent runs...",
  currentScope: "Current scope",
};

const ZH_COPY: Copy = {
  title: "Agent Run",
  subtitle: "查看多专家工作流的计划、证据链、步骤和审计日志。",
  refresh: "刷新",
  openTasks: "打开 AI 任务",
  searchPlaceholder: "搜索任务名、提示词、UUID、摘要或证据...",
  statusAll: "全部",
  statusFilters: ["待处理", "排队中", "运行中", "已完成", "失败", "已取消"],
  listTitle: "最近运行",
  listHint: "选择一个 run，查看它的计划、步骤、证据和工作簿轨迹。",
  noRuns: "暂无 Agent Run。",
  noRunsHint: "先创建一个 AGENT_WORKFLOW 任务，再回来查看。",
  noSelection: "请从左侧选择一个 run 查看完整审计链。",
  taskLogs: "任务日志",
  start: "启动",
  resume: "继续",
  cancel: "取消",
  summary: "摘要",
  objective: "目标",
  runDigest: "运行摘要",
  plan: "计划",
  steps: "步骤",
  evidence: "证据",
  findings: "结论",
  citations: "引用",
  actions: "动作",
  artifacts: "产物",
  openQuestions: "待解问题",
  conversations: "对话",
  memories: "记忆",
  auditTrail: "审计轨迹",
  rawJson: "原始 JSON",
  taskAudit: "任务审计",
  taskSummary: "任务概览",
  inputData: "输入数据",
  outputData: "输出数据",
  taskUuid: "任务 UUID",
  taskName: "任务名称",
  status: "状态",
  priority: "优先级",
  country: "国家",
  mode: "模式",
  outputFormat: "输出格式",
  risk: "风险",
  budget: "Token 配额",
  evidenceCount: "证据数",
  findingCount: "结论数",
  stepCount: "步骤数",
  confidence: "置信度",
  tokenUsage: "Token 消耗",
  started: "开始时间",
  ended: "结束时间",
  prompt: "提示词",
  summaryFallback: "暂无摘要。",
  emptyHint: "暂无可用的运行详情。",
  details: "详情",
  viewTaskDetail: "查看任务日志",
  loading: "Agent Run 数据加载中...",
  currentScope: "当前范围",
};

function getCopy(lang: "en" | "zh"): Copy {
  return lang === "zh" ? ZH_COPY : EN_COPY;
}

function formatDateTime(value: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function shortUuid(value: string | null): string {
  if (!value) return "-";
  if (value.length <= 12) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

function textValue(value: unknown, fallback = "-"): string {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || fallback;
  }
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return fallback;
}

function compactText(value: unknown, max = 220, fallback = "-"): string {
  const text = textValue(value, "").trim();
  if (!text) return fallback;
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1))}…`;
}

function toArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

function colorForStatus(status: string): Color {
  return STATUS_BADGE[status.toLowerCase()] ?? "slate";
}

function colorForRisk(risk: string): Color {
  return RISK_BADGE[risk.toLowerCase()] ?? "slate";
}

function colorForStep(stepType: string): Color {
  return STEP_BADGE[stepType.toLowerCase()] ?? "slate";
}

function colorForEvidence(type: string): Color {
  return EVIDENCE_BADGE[type.toLowerCase()] ?? "slate";
}

function metricValue(value: number | null | undefined, suffix = ""): string {
  if (value == null || Number.isNaN(value)) return "-";
  return suffix ? `${value}${suffix}` : String(value);
}

function metadataText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function JsonDetails({
  title,
  value,
  empty = "-",
}: {
  title: string;
  value: unknown;
  empty?: string;
}) {
  const json = prettyJson(value);
  return (
    <details className="rounded-xl border border-tremor-border bg-tremor-background/80 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle [&::-webkit-details-marker]:hidden">
        {title}
      </summary>
      <pre className="max-h-80 overflow-auto border-t border-tremor-border px-4 py-3 font-mono text-xs leading-6 text-tremor-content-strong dark:border-dark-tremor-border dark:text-dark-tremor-content-strong">
        {json === "{}" ? empty : json}
      </pre>
    </details>
  );
}

function SectionCard({
  title,
  description,
  badge,
  children,
}: {
  title: string;
  description?: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="border-b border-tremor-border bg-tremor-background-subtle px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <Title className="text-base">{title}</Title>
            {description && <Text className="mt-1 text-sm">{description}</Text>}
          </div>
          {badge}
        </div>
      </div>
      <div className="space-y-3 px-4 py-4">{children}</div>
    </Card>
  );
}

function MetricTile({
  label,
  value,
  icon: Icon,
  accentClass,
}: {
  label: string;
  value: string;
  icon: ComponentType<{ className?: string }>;
  accentClass: string;
}) {
  return (
    <div className={`rounded-xl border px-3 py-3 ${accentClass}`}>
      <div className="flex items-center justify-between gap-3">
        <Text className="text-xs font-medium uppercase tracking-wide">{label}</Text>
        <Icon className="h-4 w-4 text-current opacity-80" />
      </div>
      <div className="mt-2 text-xl font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
        {value}
      </div>
    </div>
  );
}

function RunListItem({
  item,
  selected,
  onSelect,
  copy,
}: {
  item: AgentRunSummary;
  selected: boolean;
  onSelect: () => void;
  copy: Copy;
}) {
  const summary = compactText(item.run.summary ?? item.task.description ?? null, 160, copy.summaryFallback);
  const resultJson = item.run.result_json ?? {};
  const confidence = typeof resultJson.confidence === "number" ? resultJson.confidence : null;
  const evidenceCount = typeof resultJson.evidence_count === "number" ? resultJson.evidence_count : null;

  return (
    <button
      onClick={onSelect}
      className={`w-full rounded-2xl border px-3 py-3 text-left transition-all ${selected
        ? "border-tremor-brand bg-cyan-50/80 shadow-sm dark:border-dark-tremor-brand dark:bg-cyan-950/20"
        : "border-tremor-border bg-tremor-background hover:border-tremor-brand-muted hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:border-dark-tremor-brand-muted dark:hover:bg-dark-tremor-background-subtle"
        }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {item.task.task_name}
          </div>
          <div className="mt-1 truncate font-mono text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {shortUuid(item.task.task_uuid)}
          </div>
        </div>
        <Badge color={colorForStatus(item.run.status)}>{statusLabel(item.run.status)}</Badge>
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        <Badge color={colorForRisk(item.run.risk_level)}>{item.run.risk_level}</Badge>
        <Badge color="slate">{item.run.mode}</Badge>
        <Badge color="indigo">{item.run.output_format}</Badge>
        {item.country_code && <Badge color="cyan">{item.country_code.toUpperCase()}</Badge>}
      </div>

      <Text className="mt-2 line-clamp-2 text-xs text-tremor-content dark:text-dark-tremor-content">
        {summary}
      </Text>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        <span>{formatDateTime(item.task.created_at)}</span>
        <span>
          {item.run.step_count} {copy.stepCount.toLowerCase()} · {metricValue(evidenceCount ?? item.run.step_count)}
        </span>
      </div>

      {confidence != null && (
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
          <div
            className="h-full rounded-full bg-tremor-brand"
            style={{ width: `${Math.max(4, Math.min(100, confidence * 100))}%` }}
          />
        </div>
      )}
    </button>
  );
}

function AgentRunsPageContent() {
  const { lang, countryId } = useAppStore();
  const copy = useMemo(() => getCopy(lang), [lang]);
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchParamsString = searchParams.toString();
  const initialSelected = searchParams.get("task_uuid") ?? searchParams.get("task") ?? null;
  const [selectedUuid, setSelectedUuid] = useState<string | null>(initialSelected);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [search, setSearch] = useState("");
  const autoSelectedRef = useRef(false);

  useTaskWebSocket({
    extraQueryKeys: [["agent-runs"], ["agent-run"]],
  });

  const listQuery = useAgentRuns(countryId, statusFilter || undefined, search || undefined, LIST_LIMIT, 0);
  const detailQuery = useAgentRunDetail(selectedUuid);
  const resumeMutation = useResumeAgentRun();
  const cancelMutation = useCancelAgentRun();

  const runs = listQuery.data?.items ?? [];
  const detail = detailQuery.data ?? null;
  const detailTaskUuid = detail?.task.task_uuid ?? selectedUuid;

  useEffect(() => {
    const nextSelected = searchParams.get("task_uuid") ?? searchParams.get("task") ?? null;
    setSelectedUuid(nextSelected);
    autoSelectedRef.current = false;
  }, [searchParamsString]);

  useEffect(() => {
    if (selectedUuid) return;
    if (runs.length === 0) return;
    const next = runs[0]?.task.task_uuid ?? null;
    if (!next) return;
    setSelectedUuid(next);
    if (!autoSelectedRef.current) {
      autoSelectedRef.current = true;
      const params = new URLSearchParams(searchParamsString);
      params.set("task_uuid", next);
      router.replace(`/ai/agent-runs?${params.toString()}`, { scroll: false });
    }
  }, [router, runs, searchParamsString, selectedUuid]);

  const summary = useMemo(() => {
    const total = runs.length;
    const running = runs.filter((run) => run.run.status === "running").length;
    const failed = runs.filter((run) => run.run.status === "failed").length;
    const completed = runs.filter((run) => run.run.status === "completed").length;
    return { total, running, failed, completed };
  }, [runs]);

  const detailMetrics = useMemo(() => {
    const resultJson = detail?.run.result_json ?? {};
    const evidenceCount = detail ? detail.evidence.length : 0;
    const findingsCount = detail ? detail.run.findings.length : 0;
    const citationsCount = detail ? detail.run.citations.length : 0;
    const actionsCount = detail ? detail.run.actions_taken.length : 0;
    const artifactsCount = detail ? detail.run.artifacts.length : 0;
    const memoriesCount = detail ? detail.memories.length : 0;
    const confidence = typeof resultJson.confidence === "number" ? resultJson.confidence : null;
    const stepCount = detail ? detail.steps.length : 0;
    return {
      evidenceCount,
      findingsCount,
      citationsCount,
      actionsCount,
      artifactsCount,
      memoriesCount,
      confidence,
      stepCount,
    };
  }, [detail]);

  const listError = listQuery.error instanceof Error ? listQuery.error.message : null;
  const detailError =
    detailQuery.error instanceof ApiError
      ? `Request failed (${detailQuery.error.status}). ${detailQuery.error.message}`
      : detailQuery.error instanceof Error
        ? detailQuery.error.message
        : null;

  const canResume = !!detail && ["failed", "cancelled", "pending", "queued"].includes(detail.task.status);
  const canCancel = !!detail && ["pending", "queued", "running"].includes(detail.task.status);
  const resumeLabel = detail && ["pending", "queued"].includes(detail.task.status) ? copy.start : copy.resume;

  const handleSelect = (uuid: string) => {
    setSelectedUuid(uuid);
    const params = new URLSearchParams(searchParamsString);
    params.set("task_uuid", uuid);
    router.replace(`/ai/agent-runs?${params.toString()}`, { scroll: false });
  };

  const handleResume = async () => {
    if (!detailTaskUuid) return;
    await resumeMutation.mutateAsync(detailTaskUuid);
    await detailQuery.refetch();
    await listQuery.refetch();
  };

  const handleCancel = async () => {
    if (!detailTaskUuid) return;
    await cancelMutation.mutateAsync(detailTaskUuid);
    await detailQuery.refetch();
    await listQuery.refetch();
  };

  const workbookEntries = detail?.task.workbook_entries ?? [];
  const resultJson = detail?.run.result_json ?? {};
  const planNodes = detail?.run.plan_json ?? [];
  const findings = detail?.run.findings ?? [];
  const citations = detail?.run.citations ?? [];
  const actionsTaken = detail?.run.actions_taken ?? [];
  const artifacts = detail?.run.artifacts ?? [];
  const openQuestions = detail?.run.open_questions ?? [];

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <Card className="overflow-hidden p-0">
        <div className="border-b border-tremor-border bg-gradient-to-br from-cyan-50 via-white to-emerald-50 px-4 py-5 dark:border-dark-tremor-border dark:from-slate-950 dark:via-slate-900 dark:to-slate-950 sm:px-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <Badge color="cyan" className="w-fit">
                {copy.currentScope}
              </Badge>
              <Title className="mt-2 text-2xl">{copy.title}</Title>
              <Text className="mt-1 max-w-3xl">{copy.subtitle}</Text>
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => {
                  listQuery.refetch();
                  detailQuery.refetch();
                }}
                className="inline-flex items-center gap-1 rounded-lg border border-cyan-300/70 bg-cyan-50 px-3 py-1.5 text-xs font-medium text-cyan-700 transition hover:bg-cyan-100 dark:border-cyan-900 dark:bg-cyan-950/25 dark:text-cyan-300"
              >
                <RefreshCcw className="h-3.5 w-3.5" />
                {copy.refresh}
              </button>
              <Link
                href="/ai/tasks"
                className="inline-flex items-center gap-1 rounded-lg border border-violet-300/70 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:bg-violet-100 dark:border-violet-900 dark:bg-violet-950/25 dark:text-violet-300"
              >
                <MessageSquareText className="h-3.5 w-3.5" />
                {copy.openTasks}
              </Link>
            </div>
          </div>

          <Grid numItems={1} numItemsSm={2} numItemsLg={4} className="mt-5 gap-3">
            <MetricTile
              label={copy.listTitle}
              value={String(summary.total)}
              icon={FileText}
              accentClass="border-sky-100 bg-sky-50/70 dark:border-sky-900/40 dark:bg-sky-950/20"
            />
            <MetricTile
              label={copy.status}
              value={String(summary.running)}
              icon={Clock3}
              accentClass="border-amber-100 bg-amber-50/70 dark:border-amber-900/40 dark:bg-amber-950/20"
            />
            <MetricTile
              label={copy.findings}
              value={String(summary.completed)}
              icon={CheckCircle2}
              accentClass="border-emerald-100 bg-emerald-50/70 dark:border-emerald-900/40 dark:bg-emerald-950/20"
            />
            <MetricTile
              label={copy.emptyHint}
              value={String(summary.failed)}
              icon={Cpu}
              accentClass="border-rose-100 bg-rose-50/70 dark:border-rose-900/40 dark:bg-rose-950/20"
            />
          </Grid>
        </div>
      </Card>

      <Card className="p-4 sm:p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
          <div className="relative min-w-[240px] flex-1 max-w-2xl">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle"
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={copy.searchPlaceholder}
              className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setStatusFilter("")}
              className={`inline-flex items-center gap-2 rounded-tremor-full border px-3 py-1.5 text-sm font-medium transition ${statusFilter === ""
                ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted dark:border-dark-tremor-brand dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
                : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:border-tremor-brand-muted hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:border-dark-tremor-brand-muted dark:hover:bg-dark-tremor-background-subtle"
                }`}
            >
              {copy.statusAll}
            </button>
            {STATUS_FILTERS.map((status, index) => (
              <button
                key={status}
                onClick={() => setStatusFilter(status)}
                className={`inline-flex items-center gap-2 rounded-tremor-full border px-3 py-1.5 text-sm font-medium transition ${statusFilter === status
                  ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted dark:border-dark-tremor-brand dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
                  : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:border-tremor-brand-muted hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:border-dark-tremor-brand-muted dark:hover:bg-dark-tremor-background-subtle"
                  }`}
              >
                {copy.statusFilters[index]}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          <span>{copy.currentScope}: {countryId ?? "-"}</span>
          {statusFilter && <span>{copy.status}: {statusFilter}</span>}
          {search.trim() && <span>{copy.searchPlaceholder}: {search.trim()}</span>}
        </div>
      </Card>

      {listError && (
        <Card className="border-rose-200 bg-rose-50 px-4 py-3 dark:border-rose-900/30 dark:bg-rose-950/25">
          <Text className="text-sm font-medium text-rose-700 dark:text-rose-300">{listError}</Text>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.55fr)]">
        <div className="space-y-4">
          <Card className="p-4">
            <div className="flex items-center justify-between gap-2">
              <div>
                <Title className="text-base">{copy.listTitle}</Title>
                <Text className="mt-1 text-sm">{copy.listHint}</Text>
              </div>
              <Badge color="slate">{runs.length}</Badge>
            </div>
          </Card>

          <Card className="p-0">
            <div className="max-h-[calc(100vh-16rem)] overflow-y-auto p-3">
              {listQuery.isLoading ? (
                <div className="space-y-3">
                  {[1, 2, 3, 4].map((item) => (
                    <div
                      key={item}
                      className="h-28 animate-pulse rounded-2xl bg-tremor-background-muted dark:bg-dark-tremor-background-muted"
                    />
                  ))}
                </div>
              ) : runs.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <BookOpen className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                  <Text className="mt-3">{copy.noRuns}</Text>
                  <Text className="mt-2 max-w-xs text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {copy.noRunsHint}
                  </Text>
                </div>
              ) : (
                <div className="space-y-3">
                  {runs.map((item) => (
                    <RunListItem
                      key={item.task.task_uuid}
                      item={item}
                      selected={selectedUuid === item.task.task_uuid}
                      onSelect={() => handleSelect(item.task.task_uuid)}
                      copy={copy}
                    />
                  ))}
                </div>
              )}
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          {detailQuery.isLoading && !detail ? (
            <Card className="p-6">
              <div className="space-y-4">
                <div className="h-6 w-1/3 animate-pulse rounded bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
                <div className="h-24 w-full animate-pulse rounded-2xl bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {[1, 2, 3, 4].map((item) => (
                    <div
                      key={item}
                      className="h-20 animate-pulse rounded-2xl bg-tremor-background-muted dark:bg-dark-tremor-background-muted"
                    />
                  ))}
                </div>
              </div>
            </Card>
          ) : detailError ? (
            <Card className="border-rose-200 bg-rose-50 p-6 dark:border-rose-900/30 dark:bg-rose-950/25">
              <Title className="text-base">{copy.emptyHint}</Title>
              <Text className="mt-2 text-sm text-rose-700 dark:text-rose-300">{detailError}</Text>
            </Card>
          ) : !detail ? (
            <Card className="border-dashed p-10">
              <div className="flex flex-col items-center justify-center text-center">
                <FileText className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                <Text className="mt-3 max-w-md text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {copy.noSelection}
                </Text>
              </div>
            </Card>
          ) : (
            <Card className="overflow-hidden p-0">
              <div className="border-b border-tremor-border bg-gradient-to-br from-white via-cyan-50/80 to-emerald-50/80 px-5 py-5 dark:border-dark-tremor-border dark:from-dark-tremor-background dark:via-slate-950 dark:to-slate-950">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <Badge color="cyan" className="w-fit">
                      {copy.taskAudit}
                    </Badge>
                    <Title className="mt-2 text-2xl leading-8">{detail.task.task_name}</Title>
                    <Text className="mt-2 max-w-3xl text-sm text-tremor-content dark:text-dark-tremor-content">
                      {compactText(detail.run.summary ?? detail.task.description ?? copy.summaryFallback, 320, copy.summaryFallback)}
                    </Text>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <Badge color={colorForStatus(detail.task.status)}>{statusLabel(detail.task.status)}</Badge>
                      <Badge color={colorForRisk(detail.run.risk_level)}>{detail.run.risk_level}</Badge>
                      <Badge color="slate">{detail.run.mode}</Badge>
                      <Badge color="indigo">{detail.run.output_format}</Badge>
                      {detail.task.country_code && (
                        <Badge color="cyan">{detail.task.country_code.toUpperCase()}</Badge>
                      )}
                      {detail.task.parent_task_id && <Badge color="slate">parent #{detail.task.parent_task_id}</Badge>}
                    </div>
                  </div>

                  <div className="flex flex-col items-end gap-2">
                    <div className="rounded-2xl border border-tremor-border bg-white px-4 py-3 shadow-sm dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                      <Text className="text-[11px] uppercase tracking-[0.18em] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {copy.taskUuid}
                      </Text>
                      <div className="mt-1 max-w-[18rem] break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        {detail.task.task_uuid}
                      </div>
                    </div>

                    <div className="flex flex-wrap justify-end gap-2">
                      <Link
                        href={`/ai/tasks?task_uuid=${encodeURIComponent(detail.task.task_uuid)}`}
                        className="inline-flex items-center gap-1 rounded-lg border border-violet-300/70 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:bg-violet-100 dark:border-violet-900 dark:bg-violet-950/25 dark:text-violet-300"
                      >
                        <MessageSquareText className="h-3.5 w-3.5" />
                        {copy.viewTaskDetail}
                      </Link>
                      {canResume && (
                        <button
                          onClick={handleResume}
                          disabled={resumeMutation.isPending}
                          className="inline-flex items-center gap-1 rounded-lg border border-emerald-300/70 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 transition hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-emerald-900 dark:bg-emerald-950/25 dark:text-emerald-300"
                        >
                          {resumeMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                          {resumeLabel}
                        </button>
                      )}
                      {canCancel && (
                        <button
                          onClick={handleCancel}
                          disabled={cancelMutation.isPending}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-300/70 bg-rose-50 px-3 py-1.5 text-xs font-medium text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-rose-900 dark:bg-rose-950/25 dark:text-rose-300"
                        >
                          {cancelMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Ban className="h-3.5 w-3.5" />}
                          {copy.cancel}
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                <Grid numItems={1} numItemsSm={2} numItemsLg={4} className="mt-5 gap-3">
                  <MetricTile
                    label={copy.stepCount}
                    value={String(detailMetrics.stepCount)}
                    icon={Cpu}
                    accentClass="border-sky-100 bg-sky-50/70 dark:border-sky-900/40 dark:bg-sky-950/20"
                  />
                  <MetricTile
                    label={copy.evidenceCount}
                    value={String(detailMetrics.evidenceCount)}
                    icon={BookOpen}
                    accentClass="border-cyan-100 bg-cyan-50/70 dark:border-cyan-900/40 dark:bg-cyan-950/20"
                  />
                  <MetricTile
                    label={copy.findingCount}
                    value={String(detailMetrics.findingsCount)}
                    icon={FileText}
                    accentClass="border-emerald-100 bg-emerald-50/70 dark:border-emerald-900/40 dark:bg-emerald-950/20"
                  />
                  <MetricTile
                    label={copy.confidence}
                    value={
                      detailMetrics.confidence == null
                        ? "-"
                        : `${Math.round(detailMetrics.confidence * 100)}%`
                    }
                    icon={CheckCircle2}
                    accentClass="border-indigo-100 bg-indigo-50/70 dark:border-indigo-900/40 dark:bg-indigo-950/20"
                  />
                </Grid>
              </div>

              <div className="space-y-5 px-5 py-5">
                <SectionCard
                  title={copy.summary}
                  description={copy.objective}
                  badge={<Badge color="cyan">{copy.summary}</Badge>}
                >
                  <div className="rounded-2xl border border-tremor-border bg-tremor-background-subtle px-4 py-4 text-sm leading-7 text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-strong">
                    <p className="font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {copy.prompt}
                    </p>
                    <p className="mt-2 whitespace-pre-wrap">{detail.run.prompt}</p>
                    {detail.run.summary && (
                      <div className="mt-4">
                        <p className="font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {copy.summary}
                        </p>
                        <p className="mt-2 whitespace-pre-wrap">{detail.run.summary}</p>
                      </div>
                    )}
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <MetricTile
                      label={copy.started}
                      value={formatDateTime(detail.run.started_at)}
                      icon={Clock3}
                      accentClass="border-slate-100 bg-slate-50/70 dark:border-slate-800/80 dark:bg-slate-950/20"
                    />
                    <MetricTile
                      label={copy.ended}
                      value={formatDateTime(detail.run.ended_at)}
                      icon={Clock3}
                      accentClass="border-slate-100 bg-slate-50/70 dark:border-slate-800/80 dark:bg-slate-950/20"
                    />
                    <MetricTile
                      label={copy.budget}
                      value={metricValue(detail.run.budget_tokens_used)}
                      icon={Cpu}
                      accentClass="border-amber-100 bg-amber-50/70 dark:border-amber-900/40 dark:bg-amber-950/20"
                    />
                    <MetricTile
                      label={copy.taskName}
                      value={shortUuid(detail.task.task_uuid)}
                      icon={Database}
                      accentClass="border-cyan-100 bg-cyan-50/70 dark:border-cyan-900/40 dark:bg-cyan-950/20"
                    />
                  </div>

                  <div className="flex flex-wrap gap-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    <span>{copy.status}: {statusLabel(detail.task.status)}</span>
                    <span>{copy.priority}: {detail.task.priority}</span>
                    <span>{copy.country}: {detail.task.country_code ?? "-"}</span>
                    <span>{copy.mode}: {detail.run.mode}</span>
                    <span>{copy.outputFormat}: {detail.run.output_format}</span>
                  </div>
                </SectionCard>

                <SectionCard title={copy.runDigest} badge={<Badge color="slate">{copy.runDigest}</Badge>}>
                  <Text className="whitespace-pre-wrap text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    {detail.run.result_json?.run_log_digest ? String(detail.run.result_json.run_log_digest) : detail.run.summary ?? copy.summaryFallback}
                  </Text>
                </SectionCard>

                <SectionCard title={copy.plan} badge={<Badge color="indigo">{planNodes.length}</Badge>}>
                  <div className="space-y-3">
                    {planNodes.length > 0 ? (
                      planNodes.map((node, index) => {
                        const planNode = node as Record<string, unknown>;
                        const stepType = textValue(planNode.step_type, "analysis");
                        return (
                          <details
                            key={textValue(planNode.step_key, `node-${index}`)}
                            className="group rounded-2xl border border-tremor-border bg-tremor-background-subtle/70 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/60"
                          >
                            <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background [&::-webkit-details-marker]:hidden">
                              <ChevronDown className="h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform group-open:rotate-180 dark:text-dark-tremor-content-subtle" />
                              <Badge color={colorForStep(stepType)}>{stepType}</Badge>
                              <span className="truncate">{textValue(planNode.title, `Step ${index + 1}`)}</span>
                            </summary>
                            <div className="space-y-3 border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
                              {textValue(planNode.instruction, "").trim() && (
                                <Text className="text-sm leading-6 text-tremor-content dark:text-dark-tremor-content">
                                  {textValue(planNode.instruction)}
                                </Text>
                              )}
                              <div className="flex flex-wrap gap-2">
                                {textValue(planNode.action, "").trim() && (
                                  <Badge color="amber">{textValue(planNode.action)}</Badge>
                                )}
                                {toArray(planNode.depends_on).length > 0 && (
                                  <Badge color="slate">depends: {toArray(planNode.depends_on).length}</Badge>
                                )}
                                {toArray(planNode.search_queries).length > 0 && (
                                  <Badge color="blue">{toArray(planNode.search_queries).length} queries</Badge>
                                )}
                              </div>
                              <JsonDetails title={copy.details} value={planNode} empty="{}" />
                            </div>
                          </details>
                        );
                      })
                    ) : (
                      <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {copy.noSelection}
                      </Text>
                    )}
                  </div>
                </SectionCard>

                <SectionCard title={copy.steps} badge={<Badge color="emerald">{detail.steps.length}</Badge>}>
                  <div className="space-y-3">
                    {detail.steps.length > 0 ? (
                      detail.steps.map((step: AgentWorkflowStep) => (
                        <details
                          key={step.step_uuid}
                          className="group rounded-2xl border border-tremor-border bg-tremor-background-subtle/70 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/60"
                        >
                          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background [&::-webkit-details-marker]:hidden">
                            <ChevronDown className="h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform group-open:rotate-180 dark:text-dark-tremor-content-subtle" />
                            <Badge color={colorForStep(step.step_type)}>{step.step_type}</Badge>
                            <span className="truncate">
                              #{step.step_order} {step.step_name}
                            </span>
                            <Badge color={colorForStatus(step.status)}>{statusLabel(step.status)}</Badge>
                          </summary>
                          <div className="space-y-4 border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
                            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                              <MetricTile
                                label={copy.stepCount}
                                value={`#${step.step_order}`}
                                icon={Clock3}
                                accentClass="border-slate-100 bg-slate-50/70 dark:border-slate-800/80 dark:bg-slate-950/20"
                              />
                              <MetricTile
                                label={copy.tokenUsage}
                                value={textValue(step.tokens?.total, "0")}
                                icon={Cpu}
                                accentClass="border-sky-100 bg-sky-50/70 dark:border-sky-900/40 dark:bg-sky-950/20"
                              />
                              <MetricTile
                                label={copy.started}
                                value={formatDateTime(step.started_at)}
                                icon={Clock3}
                                accentClass="border-cyan-100 bg-cyan-50/70 dark:border-cyan-900/40 dark:bg-cyan-950/20"
                              />
                              <MetricTile
                                label={copy.ended}
                                value={formatDateTime(step.ended_at)}
                                icon={Clock3}
                                accentClass="border-emerald-100 bg-emerald-50/70 dark:border-emerald-900/40 dark:bg-emerald-950/20"
                              />
                            </div>

                            {(step.input_summary || step.output_summary || step.error_message) && (
                              <div className="grid gap-3 lg:grid-cols-2">
                                {step.input_summary && (
                                  <div className="rounded-2xl border border-tremor-border bg-white/80 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
                                    <Text className="text-xs font-medium uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                      {copy.inputData}
                                    </Text>
                                    <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                      {step.input_summary}
                                    </p>
                                  </div>
                                )}
                                {step.output_summary && (
                                  <div className="rounded-2xl border border-tremor-border bg-white/80 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
                                    <Text className="text-xs font-medium uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                      {copy.outputData}
                                    </Text>
                                    <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                      {step.output_summary}
                                    </p>
                                  </div>
                                )}
                              </div>
                            )}

                            {step.error_message && (
                              <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900/30 dark:bg-rose-950/25 dark:text-rose-300">
                                {step.error_message}
                              </div>
                            )}

                            <div className="grid gap-3 lg:grid-cols-2">
                              <JsonDetails title={`${copy.details} · payload`} value={step.input_payload} />
                              <JsonDetails title={`${copy.details} · result`} value={step.output_payload} />
                            </div>

                            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                              <MetricTile
                                label={copy.priority}
                                value={textValue(step.attempt)}
                                icon={CheckCircle2}
                                accentClass="border-slate-100 bg-slate-50/70 dark:border-slate-800/80 dark:bg-slate-950/20"
                              />
                              <MetricTile
                                label={copy.risk}
                                value={textValue(step.model, "-")}
                                icon={Cpu}
                                accentClass="border-violet-100 bg-violet-50/70 dark:border-violet-900/40 dark:bg-violet-950/20"
                              />
                              <MetricTile
                                label={copy.mode}
                                value={textValue(step.provider, "-")}
                                icon={Database}
                                accentClass="border-cyan-100 bg-cyan-50/70 dark:border-cyan-900/40 dark:bg-cyan-950/20"
                              />
                              <MetricTile
                                label={copy.confidence}
                                value={step.duration != null ? `${step.duration.toFixed(1)}s` : "-"}
                                icon={Clock3}
                                accentClass="border-emerald-100 bg-emerald-50/70 dark:border-emerald-900/40 dark:bg-emerald-950/20"
                              />
                            </div>
                          </div>
                        </details>
                      ))
                    ) : (
                      <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {copy.noSelection}
                      </Text>
                    )}
                  </div>
                </SectionCard>

                <SectionCard title={copy.evidence} badge={<Badge color="blue">{detail.evidence.length}</Badge>}>
                  <div className="space-y-3">
                    {detail.evidence.length > 0 ? (
                      detail.evidence.map((item: AgentWorkflowEvidence) => (
                        <details
                          key={item.evidence_uuid}
                          className="group rounded-2xl border border-tremor-border bg-tremor-background-subtle/70 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/60"
                        >
                          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background [&::-webkit-details-marker]:hidden">
                            <ChevronDown className="h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform group-open:rotate-180 dark:text-dark-tremor-content-subtle" />
                            <Badge color={colorForEvidence(item.evidence_type)}>{item.evidence_type}</Badge>
                            <span className="truncate">{item.title || item.source_name || item.source_type}</span>
                          </summary>
                          <div className="space-y-3 border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
                            <div className="flex flex-wrap gap-2">
                              <Badge color="slate">{item.source_type}</Badge>
                              {item.source_name && <Badge color="cyan">{item.source_name}</Badge>}
                              <Badge color="slate">confidence {(item.confidence * 100).toFixed(0)}%</Badge>
                              <Badge color="slate">weight {item.weight}</Badge>
                            </div>
                            {item.content_snippet && (
                              <p className="whitespace-pre-wrap text-sm leading-6 text-tremor-content dark:text-dark-tremor-content">
                                {item.content_snippet}
                              </p>
                            )}
                            <div className="grid gap-3 lg:grid-cols-2">
                              <JsonDetails title={`${copy.details} · evidence`} value={item} />
                              <div className="rounded-2xl border border-tremor-border bg-white/80 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
                                <Text className="text-xs font-medium uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                  URL
                                </Text>
                                {item.resolved_url || item.url ? (
                                  <a
                                    href={item.resolved_url || item.url || "#"}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="mt-2 inline-flex items-center gap-1 break-all text-sm font-medium text-cyan-700 hover:underline dark:text-cyan-300"
                                  >
                                    <ExternalLink className="h-3.5 w-3.5" />
                                    {item.resolved_url || item.url}
                                  </a>
                                ) : (
                                  <Text className="mt-2 text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                    -
                                  </Text>
                                )}
                              </div>
                            </div>
                          </div>
                        </details>
                      ))
                    ) : (
                      <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {copy.noSelection}
                      </Text>
                    )}
                  </div>
                </SectionCard>

                <div className="grid gap-5 xl:grid-cols-2">
                  <SectionCard title={copy.findings} badge={<Badge color="emerald">{findings.length}</Badge>}>
                    {findings.length > 0 ? (
                      <div className="space-y-3">
                        {findings.map((finding, index) => {
                          const record = finding as Record<string, unknown>;
                          const claim = textValue(record.claim ?? record.finding ?? record.summary, `Finding ${index + 1}`);
                          const evidenceRefs = toArray(record.supporting_evidence);
                          const confidence = typeof record.confidence === "number" ? record.confidence : null;
                          return (
                            <div
                              key={`${claim}-${index}`}
                              className="rounded-2xl border border-emerald-200 bg-emerald-50/70 p-3 dark:border-emerald-900/30 dark:bg-emerald-950/20"
                            >
                              <div className="flex flex-wrap items-start justify-between gap-2">
                                <p className="text-sm font-medium leading-6 text-emerald-900 dark:text-emerald-100">
                                  {claim}
                                </p>
                                {confidence != null && (
                                  <Badge color="emerald">{(confidence * 100).toFixed(0)}%</Badge>
                                )}
                              </div>
                              {evidenceRefs.length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {evidenceRefs.map((ref, refIndex) => (
                                    <Badge key={`${claim}-${refIndex}`} color="slate">
                                      {textValue(ref)}
                                    </Badge>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {copy.noSelection}
                      </Text>
                    )}
                  </SectionCard>

                  <SectionCard title={copy.citations} badge={<Badge color="violet">{citations.length}</Badge>}>
                    {citations.length > 0 ? (
                      <div className="space-y-3">
                        {citations.map((citation, index) => {
                          const record = citation as Record<string, unknown>;
                          return (
                            <div
                              key={`${textValue(record.content_hash, `citation-${index}`)}`}
                              className="rounded-2xl border border-violet-200 bg-violet-50/70 p-3 dark:border-violet-900/30 dark:bg-violet-950/20"
                            >
                              <div className="flex flex-wrap gap-2">
                                <Badge color="slate">{textValue(record.source_type ?? record.evidence_type, "citation")}</Badge>
                                {textValue(record.source_name, "").trim() && (
                                  <Badge color="cyan">{textValue(record.source_name)}</Badge>
                                )}
                                <Badge color="slate">{textValue(record.confidence, "0")}</Badge>
                              </div>
                              <p className="mt-2 text-sm leading-6 text-violet-900 dark:text-violet-100">
                                {compactText(record.title ?? record.content_snippet ?? record.claim, 200, copy.summaryFallback)}
                              </p>
                              {(record.url || record.resolved_url) ? (
                                <a
                                  href={textValue(record.resolved_url ?? record.url)}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-cyan-700 hover:underline dark:text-cyan-300"
                                >
                                  <ExternalLink className="h-3.5 w-3.5" />
                                  {textValue(record.resolved_url ?? record.url)}
                                </a>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {copy.noSelection}
                      </Text>
                    )}
                  </SectionCard>
                </div>

                <div className="grid gap-5 xl:grid-cols-2">
                  <SectionCard title={copy.actions} badge={<Badge color="amber">{actionsTaken.length}</Badge>}>
                    {actionsTaken.length > 0 ? (
                      <div className="space-y-3">
                        {actionsTaken.map((action, index) => {
                          const record = action as Record<string, unknown>;
                          return (
                            <div
                              key={`${textValue(record.action, `action-${index}`)}`}
                              className="rounded-2xl border border-amber-200 bg-amber-50/70 p-3 dark:border-amber-900/30 dark:bg-amber-950/20"
                            >
                              <div className="flex flex-wrap gap-2">
                                <Badge color="amber">{textValue(record.action, `action ${index + 1}`)}</Badge>
                                {textValue(record.success, "").trim() && (
                                  <Badge color="slate">{textValue(record.success)}</Badge>
                                )}
                              </div>
                              <p className="mt-2 text-sm leading-6 text-amber-900 dark:text-amber-100">
                                {compactText(record.summary ?? record.output ?? record.response, 220, copy.summaryFallback)}
                              </p>
                              <JsonDetails title={`${copy.details} · action`} value={record} />
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {copy.noSelection}
                      </Text>
                    )}
                  </SectionCard>

                  <SectionCard title={copy.artifacts} badge={<Badge color="slate">{artifacts.length}</Badge>}>
                    {artifacts.length > 0 ? (
                      <div className="space-y-3">
                        {artifacts.map((artifact, index) => {
                          const record = artifact as Record<string, unknown>;
                          return (
                            <div
                              key={`${textValue(record.path ?? record.url ?? record.name, `artifact-${index}`)}`}
                              className="rounded-2xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-800/80 dark:bg-slate-950/20"
                            >
                              <div className="flex flex-wrap gap-2">
                                <Badge color="slate">{textValue(record.name ?? record.type, `artifact ${index + 1}`)}</Badge>
                                {textValue(record.kind, "").trim() && <Badge color="cyan">{textValue(record.kind)}</Badge>}
                              </div>
                              <p className="mt-2 text-sm leading-6 text-slate-900 dark:text-slate-100">
                                {compactText(record.path ?? record.url ?? record.description ?? record.summary, 220, copy.summaryFallback)}
                              </p>
                              <JsonDetails title={`${copy.details} · artifact`} value={record} />
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {copy.noSelection}
                      </Text>
                    )}
                  </SectionCard>
                </div>

                <SectionCard title={copy.openQuestions} badge={<Badge color="rose">{openQuestions.length}</Badge>}>
                  {openQuestions.length > 0 ? (
                    <ul className="list-disc space-y-2 pl-5 text-sm leading-6 text-tremor-content dark:text-dark-tremor-content">
                      {openQuestions.map((question, index) => (
                        <li key={`${question}-${index}`}>{question}</li>
                      ))}
                    </ul>
                  ) : (
                    <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {copy.noSelection}
                    </Text>
                  )}
                </SectionCard>

                <SectionCard title={copy.conversations} badge={<Badge color="cyan">{detail.conversations.length}</Badge>}>
                  {detail.conversations.length > 0 ? (
                    <div className="space-y-3">
                      {detail.conversations.map((conversation: AgentWorkflowConversation) => (
                        <details
                          key={conversation.conversation_uuid}
                          className="group rounded-2xl border border-tremor-border bg-tremor-background-subtle/70 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/60"
                        >
                          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background [&::-webkit-details-marker]:hidden">
                            <ChevronDown className="h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform group-open:rotate-180 dark:text-dark-tremor-content-subtle" />
                            <Badge color="cyan">{conversation.agent_role}</Badge>
                            <span className="truncate">{conversation.phase}</span>
                            <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                              {formatDateTime(conversation.timestamp)}
                            </span>
                          </summary>
                          <div className="space-y-3 border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
                            <div className="flex flex-wrap gap-2">
                              {conversation.model && <Badge color="blue">{conversation.model}</Badge>}
                              {conversation.provider && <Badge color="slate">{conversation.provider}</Badge>}
                              {conversation.temperature != null && <Badge color="amber">temp {conversation.temperature}</Badge>}
                            </div>
                            {conversation.prompt && (
                              <div className="rounded-2xl border border-tremor-border bg-white/80 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
                                <Text className="text-[11px] font-medium uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                  Prompt
                                </Text>
                                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                  {conversation.prompt}
                                </p>
                              </div>
                            )}
                            {conversation.response && (
                              <div className="rounded-2xl border border-tremor-border bg-white/80 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
                                <Text className="text-[11px] font-medium uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                  Response
                                </Text>
                                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                  {conversation.response}
                                </p>
                              </div>
                            )}
                            <JsonDetails title={`${copy.details} · conversation`} value={conversation} />
                          </div>
                        </details>
                      ))}
                    </div>
                  ) : (
                    <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {copy.noSelection}
                    </Text>
                  )}
                </SectionCard>

                <SectionCard title={copy.memories} badge={<Badge color="violet">{detail.memories.length}</Badge>}>
                  {detail.memories.length > 0 ? (
                    <div className="space-y-3">
                      {detail.memories.map((memory: AgentWorkflowMemory) => (
                        <details
                          key={memory.memory_uuid}
                          className="group rounded-2xl border border-tremor-border bg-tremor-background-subtle/70 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/60"
                        >
                          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background [&::-webkit-details-marker]:hidden">
                            <ChevronDown className="h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform group-open:rotate-180 dark:text-dark-tremor-content-subtle" />
                            <Badge color="violet">{memory.scope}</Badge>
                            <Badge color="slate">{memory.memory_type}</Badge>
                            <span className="truncate">{memory.summary || memory.content || memory.source_ref || memory.memory_uuid}</span>
                          </summary>
                          <div className="space-y-3 border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
                            <div className="flex flex-wrap gap-2">
                              {memory.source_type && <Badge color="cyan">{memory.source_type}</Badge>}
                              {memory.status && <Badge color="slate">{memory.status}</Badge>}
                              {memory.collection_name && <Badge color="blue">{memory.collection_name}</Badge>}
                            </div>
                            {memory.summary && (
                              <p className="text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                {memory.summary}
                              </p>
                            )}
                            {memory.content && (
                              <p className="whitespace-pre-wrap text-sm leading-6 text-tremor-content dark:text-dark-tremor-content">
                                {memory.content}
                              </p>
                            )}
                            <JsonDetails title={`${copy.details} · memory`} value={memory} />
                          </div>
                        </details>
                      ))}
                    </div>
                  ) : (
                    <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {copy.noSelection}
                    </Text>
                  )}
                </SectionCard>

                <SectionCard title={copy.taskAudit} badge={<Badge color="slate">{workbookEntries.length}</Badge>}>
                  {workbookEntries.length > 0 ? (
                    <div className="space-y-3">
                      {workbookEntries.map((entry) => {
                        const metadata = entry.metadata ?? {};
                        const stage = metadataText(metadata.workflow_stage);
                        const provider = metadataText(metadata.provider);
                        const event = metadataText(metadata.event);
                        return (
                          <details
                            key={entry.id}
                            className="group rounded-2xl border border-tremor-border bg-tremor-background-subtle/70 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/60"
                          >
                            <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background [&::-webkit-details-marker]:hidden">
                              <ChevronDown className="h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform group-open:rotate-180 dark:text-dark-tremor-content-subtle" />
                              <Badge color={colorForStatus(entry.entry_type)}>{entry.entry_type}</Badge>
                              {stage && <Badge color="indigo">{stage}</Badge>}
                              {event && <Badge color="slate">{event}</Badge>}
                              {provider && <Badge color="cyan">{provider}</Badge>}
                              <span className="truncate">{entry.title}</span>
                            </summary>
                            <div className="space-y-3 border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
                              <div className="flex flex-wrap gap-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                <span>{formatDateTime(entry.created_at)}</span>
                                {entry.model_used && <span>{entry.model_used}</span>}
                                {entry.tokens_used != null && <span>{entry.tokens_used} tokens</span>}
                                {entry.duration != null && <span>{entry.duration.toFixed(1)}s</span>}
                              </div>
                              {entry.content && (
                                <div className="rounded-2xl border border-tremor-border bg-white/80 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
                                  <Text className="text-[11px] font-medium uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                    {copy.details}
                                  </Text>
                                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                    {entry.content}
                                  </p>
                                </div>
                              )}
                              {(entry.prompt || entry.response) && (
                                <div className="grid gap-3 lg:grid-cols-2">
                                  {entry.prompt && (
                                    <div className="rounded-2xl border border-tremor-border bg-white/80 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
                                      <Text className="text-[11px] font-medium uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                        Prompt
                                      </Text>
                                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                        {entry.prompt}
                                      </p>
                                    </div>
                                  )}
                                  {entry.response && (
                                    <div className="rounded-2xl border border-tremor-border bg-white/80 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/80">
                                      <Text className="text-[11px] font-medium uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                        Response
                                      </Text>
                                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                        {entry.response}
                                      </p>
                                    </div>
                                  )}
                                </div>
                              )}
                              <JsonDetails title={`${copy.details} · workbook entry`} value={entry} />
                            </div>
                          </details>
                        );
                      })}
                    </div>
                  ) : (
                    <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {copy.noSelection}
                    </Text>
                  )}
                </SectionCard>

                <SectionCard title={copy.rawJson} badge={<Badge color="slate">{copy.details}</Badge>}>
                  <div className="grid gap-3 lg:grid-cols-2">
                    <JsonDetails title={`${copy.rawJson} · run`} value={detail.run} />
                    <JsonDetails title={`${copy.rawJson} · task`} value={detail.task} />
                  </div>
                  <div className="grid gap-3 lg:grid-cols-2">
                    <JsonDetails title={`${copy.rawJson} · result`} value={resultJson} />
                    <JsonDetails title={`${copy.rawJson} · workbook`} value={workbookEntries} />
                  </div>
                </SectionCard>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

export default function AgentRunsPage() {
  return (
    <Suspense fallback={<div className="flex h-64 items-center justify-center text-tremor-content dark:text-dark-tremor-content">Loading...</div>}>
      <AgentRunsPageContent />
    </Suspense>
  );
}
