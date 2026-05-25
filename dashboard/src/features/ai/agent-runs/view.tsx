"use client";

import { Suspense, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  Ban,
  BookOpen,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  ExternalLink,
  FileText,
  MessageSquareText,
  RefreshCcw,
  Search,
} from "lucide-react";

import { ApiError } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useTaskWebSocket } from "@/features/operations/tasks/api";
import {
  type AgentRunSummary,
  type AgentWorkflowEvidence,
  type AgentWorkflowStep,
  useAgentRunDetail,
  useAgentRuns,
  useCancelAgentRun,
  useResumeAgentRun,
} from "@/features/ai/api";
import { cn } from "@/lib/utils";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";

const STATUS_FILTERS = ["pending", "queued", "running", "completed", "failed", "cancelled"];
const LIST_LIMIT = 50;
const inputClass =
  "h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function shortUuid(value?: string | null): string {
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
  if (typeof value === "string") return value.trim() || fallback;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return fallback;
}

function compactText(value: unknown, max = 180, fallback = "-"): string {
  const text = textValue(value, "").trim();
  if (!text) return fallback;
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1))}...`;
}

function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

function riskTone(risk: string): "neutral" | "warning" | "danger" | "success" {
  const normalized = risk.toLowerCase();
  if (normalized === "low") return "success";
  if (normalized === "medium") return "warning";
  if (normalized === "high" || normalized === "critical") return "danger";
  return "neutral";
}

function ActionButton({
  children,
  icon,
  tone = "neutral",
  disabled,
  onClick,
  className,
}: {
  children: ReactNode;
  icon?: ReactNode;
  tone?: "neutral" | "primary" | "danger";
  disabled?: boolean;
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
  className?: string;
}) {
  const toneClass =
    tone === "primary"
      ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted hover:bg-tremor-brand/90"
      : tone === "danger"
        ? "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300"
        : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle";

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center justify-center gap-2 rounded-tremor-default border px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-55",
        toneClass,
        className,
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function DetailSection({ title, badge, children }: { title: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-tremor-default border border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background">
      <div className="flex items-center justify-between gap-3 border-b border-tremor-border px-4 py-3 dark:border-dark-tremor-border">
        <h3 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{title}</h3>
        {badge}
      </div>
      <div className="space-y-3 px-4 py-4">{children}</div>
    </section>
  );
}

function JsonDetails({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
      <summary className="cursor-pointer list-none px-3 py-2 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong [&::-webkit-details-marker]:hidden">
        {title}
      </summary>
      <pre className="max-h-80 overflow-auto border-t border-tremor-border px-3 py-3 font-mono text-xs leading-6 text-tremor-content-strong dark:border-dark-tremor-border dark:text-dark-tremor-content-strong">
        {prettyJson(value)}
      </pre>
    </details>
  );
}

function AgentRunsPageContent() {
  const { lang, countryId } = useAppStore();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchParamsString = searchParams.toString();
  const initialSelected = searchParams.get("task_uuid") ?? searchParams.get("task") ?? null;
  const [selectedUuid, setSelectedUuid] = useState<string | null>(initialSelected);
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const autoSelectedRef = useRef(false);

  useTaskWebSocket({ extraQueryKeys: [["agent-runs"], ["agent-run"]] });

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
  }, [searchParamsString, searchParams]);

  useEffect(() => {
    if (selectedUuid || runs.length === 0) return;
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
    const evidence = runs.reduce((totalCount, run) => {
      const raw = run.run.result_json?.evidence_count;
      return totalCount + (typeof raw === "number" ? raw : 0);
    }, 0);
    return { total, running, failed, completed, evidence };
  }, [runs]);

  const detailMetrics = useMemo(() => {
    const resultJson = detail?.run.result_json ?? {};
    const confidence = typeof resultJson.confidence === "number" ? resultJson.confidence : null;
    return {
      evidence: detail?.evidence.length ?? 0,
      findings: detail?.run.findings.length ?? 0,
      steps: detail?.steps.length ?? 0,
      citations: detail?.run.citations.length ?? 0,
      confidence,
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

  const selectRun = (uuid: string) => {
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

  const columns = useMemo<DataTableColumn<AgentRunSummary>[]>(
    () => [
      {
        key: "status",
        header: lang === "zh" ? "状态" : "Status",
        render: (item) => <StatusBadge status={item.run.status}>{statusLabel(item.run.status)}</StatusBadge>,
      },
      {
        key: "run",
        header: lang === "zh" ? "运行" : "Run",
        render: (item) => (
          <div className="min-w-[260px] max-w-[520px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {item.task.task_name}
            </p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {item.task.task_uuid}
            </p>
          </div>
        ),
      },
      {
        key: "scope",
        header: lang === "zh" ? "范围" : "Scope",
        render: (item) => (
          <div className="min-w-[150px]">
            <div className="flex flex-wrap gap-1.5">
              <StatusBadge tone={riskTone(item.run.risk_level)}>{item.run.risk_level}</StatusBadge>
              <StatusBadge tone="neutral">{item.run.mode}</StatusBadge>
            </div>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {item.country_code?.toUpperCase() || "-"} / {item.run.output_format}
            </p>
          </div>
        ),
      },
      {
        key: "summary",
        header: lang === "zh" ? "摘要" : "Summary",
        render: (item) => (
          <p className="min-w-[240px] max-w-[420px] truncate text-sm text-tremor-content dark:text-dark-tremor-content">
            {compactText(item.run.summary ?? item.task.description, 180, lang === "zh" ? "暂无摘要" : "No summary yet")}
          </p>
        ),
      },
      {
        key: "metrics",
        header: lang === "zh" ? "指标" : "Metrics",
        render: (item) => {
          const confidence = typeof item.run.result_json?.confidence === "number" ? item.run.result_json.confidence : null;
          return (
            <div className="min-w-[120px] text-sm text-tremor-content dark:text-dark-tremor-content">
              <p>{item.run.step_count} steps</p>
              <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {confidence == null ? "-" : `${Math.round(confidence * 100)}%`} confidence
              </p>
            </div>
          );
        },
      },
      {
        key: "created",
        header: lang === "zh" ? "创建时间" : "Created",
        render: (item) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDateTime(item.task.created_at)}
          </span>
        ),
      },
    ],
    [lang],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_ai")}
        title={lang === "zh" ? "Agent Run" : "Agent Runs"}
        description={
          lang === "zh"
            ? "查看多专家工作流的计划、证据链、步骤和审计日志。"
            : "Inspect workflow plans, evidence trails, steps, and audit logs for each agent run."
        }
        meta={
          <>
            <StatusBadge tone={summary.failed > 0 ? "danger" : "success"}>
              {summary.failed > 0 ? `${summary.failed} failed` : lang === "zh" ? "无失败" : "No failures"}
            </StatusBadge>
            <StatusBadge tone={summary.running > 0 ? "warning" : "neutral"}>
              {summary.running} {lang === "zh" ? "运行中" : "running"}
            </StatusBadge>
          </>
        }
        actions={
          <>
            <ActionButton
              onClick={() => {
                void listQuery.refetch();
                void detailQuery.refetch();
              }}
              icon={<RefreshCcw className="h-4 w-4" />}
            >
              {lang === "zh" ? "刷新" : "Refresh"}
            </ActionButton>
            <Link
              href="/ai/tasks"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              <MessageSquareText className="h-4 w-4" />
              {lang === "zh" ? "AI 任务" : "AI Tasks"}
            </Link>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label={lang === "zh" ? "最近运行" : "Recent Runs"} value={summary.total} icon={<FileText className="h-4 w-4" />} tone="neutral" hint={lang === "zh" ? "当前筛选结果" : "Current result set"} />
        <MetricTile label={lang === "zh" ? "运行中" : "Running"} value={summary.running} icon={<Clock3 className="h-4 w-4" />} tone="warning" hint={lang === "zh" ? "仍在处理" : "Still processing"} />
        <MetricTile label={lang === "zh" ? "已完成" : "Completed"} value={summary.completed} icon={<CheckCircle2 className="h-4 w-4" />} tone="success" hint={lang === "zh" ? "最近列表内" : "In recent list"} />
        <MetricTile label={lang === "zh" ? "证据计数" : "Evidence Count"} value={summary.evidence} icon={<BookOpen className="h-4 w-4" />} tone="info" hint={lang === "zh" ? "来自结果摘要" : "From run result JSON"} />
      </div>

      <FilterToolbar>
        <div className="relative min-w-[240px] flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={lang === "zh" ? "搜索任务名、提示词、UUID、摘要或证据" : "Search task name, prompt, UUID, summary, or evidence"}
            className={cn(inputClass, "pl-9")}
          />
        </div>
        <select
          className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          aria-label={lang === "zh" ? "按状态筛选" : "Filter by status"}
        >
          <option value="">{lang === "zh" ? "全部状态" : "All statuses"}</option>
          {STATUS_FILTERS.map((status) => (
            <option key={status} value={status}>{status}</option>
          ))}
        </select>
      </FilterToolbar>

      {listError ? (
        <AlertBox tone="danger" icon={<AlertTriangle className="h-4 w-4" />}>{listError}</AlertBox>
      ) : null}

      {listQuery.isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((item) => (
            <div key={item} className="h-16 animate-pulse rounded-tremor-default border border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
          ))}
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={runs}
          getRowKey={(item) => item.task.task_uuid}
          selectedRowKey={selectedUuid}
          onRowClick={(item) => selectRun(item.task.task_uuid)}
          emptyState={
            <EmptyState
              icon={<BookOpen className="h-10 w-10" />}
              title={lang === "zh" ? "暂无 Agent Run" : "No agent runs found"}
              description={lang === "zh" ? "先创建一个 AGENT_WORKFLOW 任务，再回来查看。" : "Create an AGENT_WORKFLOW task first, then return here to inspect it."}
            />
          }
        />
      )}

      <DetailDrawer
        open={Boolean(selectedUuid)}
        title={detail?.task.task_name ?? (lang === "zh" ? "Agent Run 详情" : "Agent Run Detail")}
        subtitle={detailTaskUuid ?? undefined}
        onClose={() => setSelectedUuid(null)}
        className="sm:w-[860px] sm:max-w-[860px]"
      >
        {detailQuery.isLoading && !detail ? (
          <div className="space-y-3">
            <div className="h-6 w-1/3 animate-pulse rounded bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
            <div className="h-32 animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          </div>
        ) : detailError ? (
          <AlertBox tone="danger" icon={<AlertTriangle className="h-4 w-4" />}>{detailError}</AlertBox>
        ) : !detail ? (
          <EmptyState
            icon={<FileText className="h-10 w-10" />}
            title={lang === "zh" ? "暂无详情" : "No detail available"}
            description={lang === "zh" ? "请选择一个 run 查看完整审计链。" : "Select a run to view the full audit trail."}
          />
        ) : (
          <div className="space-y-5">
            <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-4 py-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge status={detail.task.status}>{statusLabel(detail.task.status)}</StatusBadge>
                    <StatusBadge tone={riskTone(detail.run.risk_level)}>{detail.run.risk_level}</StatusBadge>
                    <StatusBadge tone="neutral">{detail.run.mode}</StatusBadge>
                    <StatusBadge tone="primary">{detail.run.output_format}</StatusBadge>
                    {detail.task.country_code ? <StatusBadge tone="info">{detail.task.country_code.toUpperCase()}</StatusBadge> : null}
                  </div>
                  <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    {detail.run.summary ?? detail.task.description ?? (lang === "zh" ? "暂无摘要。" : "No summary yet.")}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Link
                    href={`/ai/tasks?task_uuid=${encodeURIComponent(detail.task.task_uuid)}`}
                    className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
                  >
                    <MessageSquareText className="h-4 w-4" />
                    {lang === "zh" ? "任务日志" : "Task logs"}
                  </Link>
                  {canResume ? (
                    <ActionButton disabled={resumeMutation.isPending} onClick={handleResume} icon={<CheckCircle2 className="h-4 w-4" />}>
                      {["pending", "queued"].includes(detail.task.status) ? (lang === "zh" ? "启动" : "Start") : (lang === "zh" ? "继续" : "Resume")}
                    </ActionButton>
                  ) : null}
                  {canCancel ? (
                    <ActionButton tone="danger" disabled={cancelMutation.isPending} onClick={handleCancel} icon={<Ban className="h-4 w-4" />}>
                      {lang === "zh" ? "取消" : "Cancel"}
                    </ActionButton>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <MetricTile label={lang === "zh" ? "步骤" : "Steps"} value={detailMetrics.steps} icon={<Cpu className="h-4 w-4" />} tone="neutral" />
              <MetricTile label={lang === "zh" ? "证据" : "Evidence"} value={detailMetrics.evidence} icon={<BookOpen className="h-4 w-4" />} tone="info" />
              <MetricTile label={lang === "zh" ? "结论" : "Findings"} value={detailMetrics.findings} icon={<FileText className="h-4 w-4" />} tone="success" />
              <MetricTile label={lang === "zh" ? "置信度" : "Confidence"} value={detailMetrics.confidence == null ? "-" : `${Math.round(detailMetrics.confidence * 100)}%`} icon={<CheckCircle2 className="h-4 w-4" />} tone="primary" />
            </div>

            <DetailSection title={lang === "zh" ? "提示词与摘要" : "Prompt & Summary"}>
              <div className="space-y-4 text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                <div>
                  <p className="font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Prompt</p>
                  <p className="mt-2 whitespace-pre-wrap">{detail.run.prompt}</p>
                </div>
                {detail.run.summary ? (
                  <div>
                    <p className="font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{lang === "zh" ? "摘要" : "Summary"}</p>
                    <p className="mt-2 whitespace-pre-wrap">{detail.run.summary}</p>
                  </div>
                ) : null}
              </div>
            </DetailSection>

            <DetailSection title={lang === "zh" ? "步骤" : "Steps"} badge={<StatusBadge tone="neutral">{detail.steps.length}</StatusBadge>}>
              <div className="space-y-3">
                {detail.steps.length ? detail.steps.map((step: AgentWorkflowStep) => (
                  <details key={step.step_uuid} className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
                    <summary className="cursor-pointer list-none px-3 py-3 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong [&::-webkit-details-marker]:hidden">
                      <span className="mr-2">#{step.step_order}</span>
                      <StatusBadge status={step.status}>{statusLabel(step.status)}</StatusBadge>
                      <span className="ml-2">{step.step_name}</span>
                    </summary>
                    <div className="space-y-3 border-t border-tremor-border px-3 py-3 dark:border-dark-tremor-border">
                      {step.input_summary ? <p className="text-sm text-tremor-content dark:text-dark-tremor-content">{step.input_summary}</p> : null}
                      {step.output_summary ? <p className="text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">{step.output_summary}</p> : null}
                      {step.error_message ? <AlertBox tone="danger" icon={<AlertTriangle className="h-4 w-4" />}>{step.error_message}</AlertBox> : null}
                      <JsonDetails title="Input payload" value={step.input_payload} />
                      <JsonDetails title="Output payload" value={step.output_payload} />
                    </div>
                  </details>
                )) : <p className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">-</p>}
              </div>
            </DetailSection>

            <DetailSection title={lang === "zh" ? "证据" : "Evidence"} badge={<StatusBadge tone="info">{detail.evidence.length}</StatusBadge>}>
              <div className="space-y-3">
                {detail.evidence.length ? detail.evidence.map((item: AgentWorkflowEvidence) => (
                  <div key={item.evidence_uuid} className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge tone="info">{item.evidence_type}</StatusBadge>
                      <StatusBadge tone="neutral">{item.source_type}</StatusBadge>
                      <span className="text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{item.title || item.source_name || "-"}</span>
                    </div>
                    {item.content_snippet ? <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-tremor-content dark:text-dark-tremor-content">{item.content_snippet}</p> : null}
                    {item.resolved_url || item.url ? (
                      <a href={item.resolved_url || item.url || "#"} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 break-all text-sm font-medium text-tremor-brand hover:underline">
                        <ExternalLink className="h-3.5 w-3.5" />
                        {item.resolved_url || item.url}
                      </a>
                    ) : null}
                  </div>
                )) : <p className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">-</p>}
              </div>
            </DetailSection>

            <div className="grid gap-5 xl:grid-cols-2">
              <DetailSection title={lang === "zh" ? "结论" : "Findings"} badge={<StatusBadge tone="success">{detail.run.findings.length}</StatusBadge>}>
                <JsonDetails title={lang === "zh" ? "结论 JSON" : "Findings JSON"} value={detail.run.findings} />
              </DetailSection>
              <DetailSection title={lang === "zh" ? "引用/产物" : "Citations / Artifacts"} badge={<StatusBadge tone="neutral">{detailMetrics.citations}</StatusBadge>}>
                <JsonDetails title="Citations" value={detail.run.citations} />
                <JsonDetails title="Artifacts" value={detail.run.artifacts} />
              </DetailSection>
            </div>

            <DetailSection title={lang === "zh" ? "原始数据" : "Raw Data"}>
              <div className="grid gap-3 lg:grid-cols-2">
                <JsonDetails title="Run JSON" value={detail.run} />
                <JsonDetails title="Task JSON" value={detail.task} />
                <JsonDetails title="Conversations" value={detail.conversations} />
                <JsonDetails title="Memories" value={detail.memories} />
              </div>
            </DetailSection>
          </div>
        )}
      </DetailDrawer>
    </div>
  );
}

function AlertBox({ tone, icon, children }: { tone: "danger" | "warning"; icon?: ReactNode; children: ReactNode }) {
  const toneClass =
    tone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/50 dark:bg-rose-950/25 dark:text-rose-200"
      : "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/25 dark:text-amber-200";
  return (
    <div className={cn("flex gap-2 rounded-tremor-default border px-4 py-3 text-sm", toneClass)}>
      {icon}
      <div>{children}</div>
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
