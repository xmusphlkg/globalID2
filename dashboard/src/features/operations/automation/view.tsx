"use client";

import Link from "next/link";

import { AlertTriangle, ArrowRight, Bell, Bot, CalendarClock, Clock3, Cpu, Mail, Plus, Settings2 } from "lucide-react";

import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";

import { t } from "@/lib/i18n";

import { useAutomationController } from "./hooks/use-automation-controller";
import { ScheduleTable } from "./components/schedule-table";
import { ScheduleForm } from "./components/schedule-form";
import { ActionButton, AlertPanel, InlineStatus } from "./components/form-controls";
import { formatDateTime } from "./schedule-model";

export default function SourcesAutomationPage() {
  const controller = useAutomationController();
  const { lang, config, isLoading, jobs, workerStatus, schedulerEnabled, workerRunning, sources, summary, deliveryStatus, openNewJob } = controller;

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_sources")}
        title={t(lang, "automation")}
        description={
          lang === "zh"
            ? "管理自动采集 job、调度状态、失败通知和最近执行结果。"
            : "Manage automated collection jobs, scheduler state, failure alerts, and recent execution outcomes."
        }
        meta={
          <>
            <StatusBadge status={schedulerEnabled ? "enabled" : "disabled"}>
              {schedulerEnabled ? (lang === "zh" ? "调度器开启" : "Scheduler on") : (lang === "zh" ? "调度器关闭" : "Scheduler off")}
            </StatusBadge>
            <StatusBadge status={workerRunning ? "enabled" : "stopped"}>
              {workerRunning ? (lang === "zh" ? "Worker 运行中" : "Worker running") : (lang === "zh" ? "Worker 停止" : "Worker stopped")}
            </StatusBadge>
            <StatusBadge tone={deliveryStatus.tone}>{deliveryStatus.label}</StatusBadge>
          </>
        }
        actions={
          <>
            <Link
              href="/operations/tasks?scope=all"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              {lang === "zh" ? "采集任务" : "Crawl Tasks"}
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              href="/operations/sources"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              {lang === "zh" ? "数据流程" : "Data Flow"}
              <ArrowRight className="h-4 w-4" />
            </Link>
            <ActionButton tone="primary" onClick={openNewJob} icon={<Plus className="h-4 w-4" />}>
              {lang === "zh" ? "新建 job" : "New job"}
            </ActionButton>
          </>
        }
      />

      {!isLoading && !schedulerEnabled ? (
        <AlertPanel tone="danger">
          <div className="flex gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="font-medium">
                {lang === "zh"
                  ? "自动化总开关当前关闭，已保存的 job 不会自动创建爬取任务。"
                  : "The scheduler is disabled, so saved jobs will not create crawl tasks automatically."}
              </p>
              <p className="mt-1">
                {lang === "zh"
                  ? "请在 `.env` 中将 `AUTOMATION__ENABLED` 设为 `true`，然后重启 API 服务。"
                  : "Set `AUTOMATION__ENABLED=true` in `.env`, then restart the API service."}
              </p>
            </div>
          </div>
        </AlertPanel>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={lang === "zh" ? "调度器" : "Scheduler"}
          value={config?.enabled ? "On" : "Off"}
          icon={<CalendarClock className="h-4 w-4" />}
          tone={schedulerEnabled ? "success" : "danger"}
          hint={lang === "zh" ? `轮询间隔 ${config?.poll_interval_seconds ?? "-"}s` : `Poll interval ${config?.poll_interval_seconds ?? "-"}s`}
        />
        <MetricTile
          label={lang === "zh" ? "启用 job" : "Enabled Jobs"}
          value={`${summary.enabled}/${summary.total}`}
          icon={<Bot className="h-4 w-4" />}
          tone="primary"
          hint={lang === "zh" ? `${summary.disabled} 个停用` : `${summary.disabled} disabled`}
        />
        <MetricTile
          label={lang === "zh" ? "最近失败" : "Recent Failures"}
          value={summary.failed}
          icon={<AlertTriangle className="h-4 w-4" />}
          tone={summary.failed > 0 ? "danger" : "success"}
          hint={lang === "zh" ? "按最近状态统计" : "Based on last status"}
        />
        <MetricTile
          label={lang === "zh" ? "提醒收件人" : "Alert Recipients"}
          value={summary.alerting}
          icon={<Bell className="h-4 w-4" />}
          tone={summary.alerting > 0 ? "success" : "warning"}
          hint={lang === "zh" ? `累计运行 ${summary.runCount} 次` : `${summary.runCount} total runs`}
        />
      </div>

      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="min-w-0 space-y-5">
          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <div className="mb-1 flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {lang === "zh" ? "运行状态" : "Runtime"}
              </h2>
              <Settings2 className="h-4 w-4 text-tremor-content-subtle" />
            </div>
            <InlineStatus
              icon={<Clock3 className="h-4 w-4" />}
              label={lang === "zh" ? "最近 tick" : "Last tick"}
              value={formatDateTime(config?.last_tick_at)}
            />
            <InlineStatus
              icon={<Cpu className="h-4 w-4" />}
              label="Worker"
              value={workerStatus ? (workerRunning ? "running" : "stopped") : "checking"}
            />
            <InlineStatus
              icon={<Mail className="h-4 w-4" />}
              label={lang === "zh" ? "邮件发送" : "Email delivery"}
              value={config?.email_enabled ? "configured" : "disabled"}
            />
          </section>

          {!workerRunning ? (
            <AlertPanel tone="warning">
              {lang === "zh"
                ? "自动化只会先创建 queued 任务；如果 worker 没启动，任务不会继续执行。"
                : "Automation can create queued tasks, but they will not continue without a running worker."}
            </AlertPanel>
          ) : null}

          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {lang === "zh" ? "通知" : "Notifications"}
            </h2>
            <div className="mt-3 space-y-3">
              <StatusBadge tone={deliveryStatus.tone}>{deliveryStatus.label}</StatusBadge>
              {!config?.email_enabled ? (
                <AlertPanel tone="warning">
                  {lang === "zh"
                    ? "SMTP 尚未准备完成，请到设置中心补齐主机、账号、密码和发件邮箱。"
                    : "SMTP is not ready. Open Settings to finish the host, username, password, and from-email setup."}
                </AlertPanel>
              ) : null}
              {!config?.admin_emails.length ? (
                <AlertPanel tone="warning">
                  {lang === "zh"
                    ? "请在设置中心补充管理员邮箱，自动化失败提醒才会送达。"
                    : "Add admin email recipients in Settings so automation failure alerts have somewhere to go."}
                </AlertPanel>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {(config?.admin_emails ?? []).length > 0 ? (
                  config?.admin_emails.map((email) => (
                    <StatusBadge key={email} tone="neutral">{email}</StatusBadge>
                  ))
                ) : (
                  <p className="text-sm text-tremor-content dark:text-dark-tremor-content">
                    {lang === "zh" ? "尚未配置管理员邮箱" : "No admin emails configured"}
                  </p>
                )}
              </div>
              <Link
                href="/settings/integrations"
                className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
              >
                {lang === "zh" ? "打开设置中心" : "Open Settings"}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </section>
        </aside>

        <ScheduleTable controller={controller} />
      </div>

      <ScheduleForm controller={controller} />
    </div>
  );
}
