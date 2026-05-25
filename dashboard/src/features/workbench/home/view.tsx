"use client";

import Link from "next/link";
import { useMemo } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  FileText,
  GitBranch,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { useOverviewSummary, useQualityStats } from "@/features/data/api";
import { type TaskItem, useTasks, useTaskWebSocket, useWorkerStatus } from "@/features/operations/tasks/api";
import { useReports } from "@/features/reports/api";
import { t } from "@/lib/i18n";
import { formatDate, formatNumber } from "@/lib/utils";
import { getCountryDisplayName, useCountries } from "@/shared/config/countries";
import { allRoutes, visibleNavigationSections } from "@/shared/navigation/route-registry";
import { useAppStore } from "@/stores/app-store";

const routeById = new Map(allRoutes.map((route) => [route.id, route]));

const priorityRouteIds = [
  "operations.flow",
  "operations.crawlTasks",
  "data.overview",
  "data.quality",
  "results.release",
  "results.reports",
];

function statusTone(status?: string | null): "neutral" | "info" | "success" | "warning" | "danger" | "primary" {
  if (!status) return "neutral";
  if (["failed", "error", "stopped"].includes(status)) return "danger";
  if (["running", "retrying"].includes(status)) return "warning";
  if (["queued", "generating", "reviewing"].includes(status)) return "info";
  if (["completed", "published", "approved"].includes(status)) return "success";
  return "neutral";
}

function taskAge(task: TaskItem, lang: "en" | "zh") {
  if (task.completed_at) return formatDate(task.completed_at);
  if (task.started_at) return lang === "zh" ? `启动 ${formatDate(task.started_at)}` : `Started ${formatDate(task.started_at)}`;
  return formatDate(task.created_at);
}

export default function HomePage() {
  const { lang, countryId, countryName, countryCode } = useAppStore();
  const { data: countries, error, isLoading } = useCountries();

  useTaskWebSocket();

  const selectedCountry = countries?.find((country) => country.id === countryId) ?? null;
  const selectedCountryLabel = selectedCountry
    ? getCountryDisplayName(selectedCountry, lang)
    : countryName;
  const { data: overviewSummary, isLoading: overviewLoading } = useOverviewSummary(countryId, lang);
  const { data: qualityStats } = useQualityStats(countryId);
  const { data: tasks, isLoading: tasksLoading } = useTasks(undefined, undefined, countryId, undefined, 8);
  const { data: workerStatus } = useWorkerStatus();
  const { data: reports } = useReports(countryId, undefined, 6);

  const taskSummary = useMemo(() => {
    const rows = tasks ?? [];
    return {
      total: rows.length,
      running: rows.filter((task) => ["running", "retrying"].includes(task.status)).length,
      failed: rows.filter((task) => task.status === "failed").length,
      active: rows.filter((task) => ["pending", "queued", "running", "retrying"].includes(task.status)).length,
    };
  }, [tasks]);

  const reportSummary = useMemo(() => {
    const rows = reports ?? [];
    return {
      pending: rows.filter((report) => ["pending", "generating", "reviewing"].includes(report.status)).length,
      published: rows.filter((report) => report.status === "published").length,
      failed: rows.filter((report) => report.status === "failed").length,
    };
  }, [reports]);

  const countryStatusTone = error
    ? "danger"
    : isLoading
      ? "warning"
      : selectedCountry
        ? "success"
        : "neutral";

  const recommendedRoute =
    taskSummary.failed > 0
      ? routeById.get("operations.crawlTasks")
      : reportSummary.pending > 0
        ? routeById.get("results.release")
        : routeById.get("data.overview");

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "brand_name")}
        title={t(lang, "workspace_title")}
        description={t(lang, "workspace_subtitle")}
        meta={
          <>
            <StatusBadge tone={countryStatusTone}>
              {countryCode || selectedCountry?.code || "--"}
            </StatusBadge>
            <StatusBadge tone="primary">{selectedCountryLabel || t(lang, "home_empty_country")}</StatusBadge>
          </>
        }
        actions={
          recommendedRoute ? (
            <Link href={recommendedRoute.href} className="primary-action">
              {t(lang, "home_jump_to")}
              <ArrowRight className="h-4 w-4" />
            </Link>
          ) : null
        }
      />

      <section className="overflow-hidden rounded-tremor-default border border-[#243a34] bg-[#17211f] text-white shadow-[0_12px_28px_rgba(23,33,31,0.16)]">
        <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="p-5 sm:p-6">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge tone={workerStatus?.worker_process_running ? "success" : "danger"}>
                {workerStatus?.worker_process_running
                  ? lang === "zh"
                    ? "Worker 运行中"
                    : "Worker running"
                  : lang === "zh"
                    ? "Worker 未运行"
                    : "Worker stopped"}
              </StatusBadge>
              <StatusBadge tone={taskSummary.failed > 0 ? "danger" : "success"}>
                {lang === "zh" ? `失败任务 ${taskSummary.failed}` : `${taskSummary.failed} failed tasks`}
              </StatusBadge>
              <StatusBadge tone={reportSummary.failed > 0 ? "danger" : "neutral"}>
                {lang === "zh" ? `报告异常 ${reportSummary.failed}` : `${reportSummary.failed} report issues`}
              </StatusBadge>
            </div>
            <div className="mt-5 max-w-3xl">
              <h2 className="text-2xl font-semibold leading-tight text-white sm:text-3xl">
                {selectedCountryLabel || t(lang, "home_empty_country")}
              </h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-white/[0.66]">
                {lang === "zh"
                  ? "采集、质量、发布和 AI 辅助流程集中在同一工作面板中。"
                  : "Ingestion, quality, release, and AI-assisted workflows are consolidated into one operating surface."}
              </p>
            </div>
            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              <div className="border-l border-white/15 pl-3">
                <p className="text-[11px] font-semibold uppercase text-white/[0.45]">{t(lang, "total_records")}</p>
                <p className="mt-1 text-xl font-semibold text-white">
                  {overviewLoading ? "-" : formatNumber(overviewSummary?.total_records ?? 0)}
                </p>
              </div>
              <div className="border-l border-white/15 pl-3">
                <p className="text-[11px] font-semibold uppercase text-white/[0.45]">{t(lang, "latest_date")}</p>
                <p className="mt-1 text-xl font-semibold text-white">
                  {overviewSummary?.latest_date ? formatDate(overviewSummary.latest_date) : "-"}
                </p>
              </div>
              <div className="border-l border-white/15 pl-3">
                <p className="text-[11px] font-semibold uppercase text-white/[0.45]">{t(lang, "completeness")}</p>
                <p className="mt-1 text-xl font-semibold text-white">
                  {qualityStats ? `${(100 - qualityStats.zero_cases_pct).toFixed(1)}%` : "-"}
                </p>
              </div>
            </div>
          </div>

          <div className="border-t border-white/10 p-5 sm:p-6 xl:border-l xl:border-t-0">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-tremor-default bg-white text-[#17211f]">
                <Sparkles className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <p className="text-[11px] font-semibold uppercase text-white/[0.48]">{t(lang, "recommended_next")}</p>
                <p className="truncate text-sm font-semibold text-white">
                  {recommendedRoute ? t(lang, recommendedRoute.labelKey) : "-"}
                </p>
              </div>
            </div>
            <div className="mt-5 space-y-2">
              {priorityRouteIds.map((routeId) => {
                const route = routeById.get(routeId);
                if (!route) return null;
                const Icon = route.icon;
                const active = recommendedRoute?.href === route.href;
                return (
                  <Link
                    key={route.href}
                    href={route.href}
                    className={`group flex items-center gap-3 rounded-tremor-default px-3 py-2.5 text-sm transition ${
                      active
                        ? "bg-white text-[#17211f]"
                        : "text-white/[0.70] hover:bg-white/[0.08] hover:text-white"
                    }`}
                  >
                    <Icon className={`h-4 w-4 ${active ? "text-tremor-brand" : "text-white/[0.42] group-hover:text-white"}`} />
                    <span className="min-w-0 flex-1 truncate font-medium">{t(lang, route.labelKey)}</span>
                    <ArrowRight className="h-4 w-4 opacity-60 transition group-hover:translate-x-0.5" />
                  </Link>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={t(lang, "running_tasks")}
          value={tasksLoading ? "-" : taskSummary.running}
          icon={<Activity className="h-4 w-4" />}
          tone={taskSummary.running > 0 ? "warning" : "neutral"}
          hint={lang === "zh" ? `${taskSummary.active} 个排队或运行` : `${taskSummary.active} queued or running`}
        />
        <MetricTile
          label={t(lang, "failed_tasks")}
          value={tasksLoading ? "-" : taskSummary.failed}
          icon={<AlertTriangle className="h-4 w-4" />}
          tone={taskSummary.failed > 0 ? "danger" : "success"}
          hint={taskSummary.failed > 0 ? (lang === "zh" ? "需要处理" : "Needs triage") : (lang === "zh" ? "队列健康" : "Queue healthy")}
        />
        <MetricTile
          label={t(lang, "total_diseases")}
          value={overviewLoading ? "-" : overviewSummary?.total_diseases ?? "-"}
          icon={<Database className="h-4 w-4" />}
          tone="primary"
          hint={selectedCountryLabel || t(lang, "select_country")}
        />
        <MetricTile
          label={t(lang, "reports")}
          value={(reports ?? []).length}
          icon={<FileText className="h-4 w-4" />}
          tone={reportSummary.failed > 0 ? "danger" : "info"}
          hint={lang === "zh" ? `发布 ${reportSummary.published} · 待处理 ${reportSummary.pending}` : `${reportSummary.published} published · ${reportSummary.pending} pending`}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
        <section className="app-panel p-4">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-tremor-content-strong">
                {lang === "zh" ? "最近任务" : "Recent Tasks"}
              </h2>
              <p className="text-xs text-tremor-content-subtle">
                {workerStatus?.worker_pid ? `PID ${workerStatus.worker_pid}` : lang === "zh" ? "后台队列" : "Background queue"}
              </p>
            </div>
            <Link href="/sources/tasks" className="inline-flex items-center gap-1 text-sm font-semibold text-tremor-brand">
              {lang === "zh" ? "查看全部" : "View all"}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>

          <div className="divide-y divide-tremor-border">
            {(tasks ?? []).slice(0, 6).map((task) => (
              <Link
                key={task.task_uuid}
                href={`/sources/tasks?task=${task.task_uuid}`}
                className="grid gap-3 py-3 transition hover:bg-tremor-background-subtle sm:grid-cols-[150px_minmax(0,1fr)_120px]"
              >
                <div className="flex items-center gap-2">
                  <StatusBadge status={task.status}>{task.status}</StatusBadge>
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-tremor-content-strong">{task.task_name}</p>
                  <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle">{task.task_uuid}</p>
                </div>
                <div className="flex items-center gap-2 text-xs text-tremor-content-subtle sm:justify-end">
                  <Clock3 className="h-3.5 w-3.5" />
                  {taskAge(task, lang)}
                </div>
              </Link>
            ))}
            {!tasks?.length ? (
              <div className="flex min-h-[180px] items-center justify-center text-sm text-tremor-content-subtle">
                {tasksLoading ? t(lang, "loading") : t(lang, "no_data")}
              </div>
            ) : null}
          </div>
        </section>

        <aside className="space-y-4">
          <section className="app-panel p-4">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-tremor-content-strong">
                {lang === "zh" ? "工作流地图" : "Workflow Map"}
              </h2>
              <GitBranch className="h-4 w-4 text-tremor-content-subtle" />
            </div>
            <div className="space-y-3">
              {visibleNavigationSections.map((section) => (
                <div key={section.id} className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3">
                  <div className="flex items-center gap-2">
                    <section.icon className="h-4 w-4 text-tremor-brand" />
                    <p className="text-sm font-semibold text-tremor-content-strong">{t(lang, section.titleKey)}</p>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {section.items.slice(0, 4).map((item) => (
                      <Link
                        key={item.href}
                        href={item.href}
                        className="rounded-tremor-default border border-tremor-border bg-tremor-background px-2 py-1 text-xs font-medium text-tremor-content transition hover:border-tremor-ring hover:text-tremor-content-strong"
                      >
                        {t(lang, item.labelKey)}
                      </Link>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="app-panel p-4">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-tremor-content-strong">
                {lang === "zh" ? "发布流转" : "Release Flow"}
              </h2>
              <StatusBadge tone={reportSummary.failed > 0 ? "danger" : "success"}>
                {reportSummary.failed > 0 ? reportSummary.failed : reportSummary.published}
              </StatusBadge>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="app-panel-muted px-3 py-2">
                <Cpu className="mb-2 h-4 w-4 text-blue-600" />
                <p className="text-lg font-semibold text-tremor-content-strong">{reportSummary.pending}</p>
                <p className="text-[11px] font-semibold uppercase text-tremor-content-subtle">{t(lang, "flow_queue")}</p>
              </div>
              <div className="app-panel-muted px-3 py-2">
                <CheckCircle2 className="mb-2 h-4 w-4 text-emerald-600" />
                <p className="text-lg font-semibold text-tremor-content-strong">{reportSummary.published}</p>
                <p className="text-[11px] font-semibold uppercase text-tremor-content-subtle">{t(lang, "flow_published")}</p>
              </div>
              <div className="app-panel-muted px-3 py-2">
                <ShieldCheck className="mb-2 h-4 w-4 text-amber-600" />
                <p className="text-lg font-semibold text-tremor-content-strong">{qualityStats?.unique_diseases ?? "-"}</p>
                <p className="text-[11px] font-semibold uppercase text-tremor-content-subtle">{t(lang, "quality")}</p>
              </div>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
