

import { Bot, Pencil, Play, Plus, Trash2 } from "lucide-react";

import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";

import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { type AutomationJob } from "@/features/operations/automation/api";

import { getSourceDisplayLabel } from "@/lib/source-labels";
import { cn } from "@/lib/utils";

import type { AutomationController, JobFilter } from "../hooks/use-automation-controller";
import { ActionButton, JobHealthBadge, inputClass } from "./form-controls";

import { scheduleLabel, formatDateTime } from "../schedule-model";

export function ScheduleTable({ controller }: { controller: AutomationController }) {
  const { lang, isLoading, jobs, countries, runJob, deleteJob, drawerOpen, editingJobId, jobSearch, setJobSearch, jobFilter, setJobFilter, schedulerEnabled, sourceConfigByCountry, sources, filteredJobs, openNewJob, startEdit, removeJob } = controller;
  const columns: DataTableColumn<AutomationJob>[] = [
    {
      key: "status",
      header: lang === "zh" ? "状态" : "Status",
      render: (job) => <JobHealthBadge job={job} />,
    },
    {
      key: "job",
      header: lang === "zh" ? "任务" : "Job",
      render: (job) => (
        <div className="min-w-[240px] max-w-[420px]">
          <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {job.name}
          </p>
          <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {job.job_id}
          </p>
        </div>
      ),
    },
    {
      key: "source",
      header: lang === "zh" ? "国家/来源" : "Country / Source",
      render: (job) => (
        <div className="min-w-[150px]">
          <p className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{job.country_code}</p>
          <p className="mt-1 truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {getSourceDisplayLabel(job.source, lang, job.country_code)}
          </p>
          {sourceConfigByCountry
            .get(job.country_code.toUpperCase())
            ?.source_options.find((option) => option.value === job.source)
            ?.source_kind === "history" ? (
              <p className="mt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {job.source === "is_doh_legacy_icd"
                  ? (lang === "zh" ? "已审核历史工作簿 · 登记诊断量（非病例通知）" : "Reviewed historical workbooks · registered diagnoses (not case notifications)")
                  : (lang === "zh" ? "已审核历史工作簿全量检查" : "Full reviewed historical-workbook check")}
              </p>
            ) : (
              (() => {
                const jobConfig = sourceConfigByCountry.get(job.country_code.toUpperCase());
                const policy = jobConfig?.source_options.find((option) => option.value === job.source)?.source_policy ?? jobConfig?.source_policy;
                if (!policy?.supports_current_month && !policy?.dynamic_revision_enabled) return null;
                return (
                  <p className="mt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {job.include_current_month
                      ? (lang === "zh" ? "含当前月（临时）" : "Current month included")
                      : (lang === "zh" ? "仅已闭合月份" : "Closed months only")}
                    {policy.dynamic_revision_enabled
                      ? ` · ${lang === "zh" ? "修订窗口" : "revision window"} ${job.revision_window_months ?? policy.default_revision_window ?? policy.default_revision_window_months}${policy.revision_window_unit === "weeks" ? "w" : policy.revision_window_unit === "years" ? "y" : "m"}`
                      : ""}
                  </p>
                );
              })()
            )}
        </div>
      ),
    },
    {
      key: "schedule",
      header: lang === "zh" ? "计划" : "Schedule",
      render: (job) => (
        <div className="min-w-[180px]">
          <p className="text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {scheduleLabel(job, lang)}
          </p>
          <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {schedulerEnabled ? formatDateTime(job.next_run_at) : (lang === "zh" ? "调度器已关闭" : "Scheduler off")}
          </p>
        </div>
      ),
    },
    {
      key: "last",
      header: lang === "zh" ? "最近运行" : "Last Run",
      render: (job) => (
        <div className="min-w-[130px]">
          <StatusBadge status={job.last_status}>{job.last_status || "-"}</StatusBadge>
          <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDateTime(job.last_finished_at || job.last_started_at)}
          </p>
        </div>
      ),
    },
    {
      key: "runs",
      header: lang === "zh" ? "次数" : "Runs",
      render: (job) => (
        <div className="whitespace-nowrap text-sm text-tremor-content dark:text-dark-tremor-content">
          <span className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{job.run_count}</span>
          <span className="text-tremor-content-subtle dark:text-dark-tremor-content-subtle"> / {job.skipped_count}</span>
        </div>
      ),
    },
    {
      key: "actions",
      header: "",
      className: "text-right",
      render: (job) => (
        <div className="flex min-w-[230px] justify-end gap-2">
          <ActionButton
            disabled={runJob.isPending}
            onClick={(event) => {
              event.stopPropagation();
              runJob.mutate(job.job_id);
            }}
            icon={<Play className="h-4 w-4" />}
          >
            {lang === "zh" ? "运行" : "Run"}
          </ActionButton>
          <ActionButton
            onClick={(event) => {
              event.stopPropagation();
              startEdit(job);
            }}
            icon={<Pencil className="h-4 w-4" />}
          >
            {lang === "zh" ? "编辑" : "Edit"}
          </ActionButton>
          <ActionButton
            tone="danger"
            disabled={deleteJob.isPending}
            onClick={(event) => {
              event.stopPropagation();
              void removeJob(job);
            }}
            icon={<Trash2 className="h-4 w-4" />}
          >
            {lang === "zh" ? "删除" : "Delete"}
          </ActionButton>
        </div>
      ),
    },
  ];
  return (
        <section className="min-w-0 space-y-3">
          <FilterToolbar>
            <input
              type="search"
              value={jobSearch}
              onChange={(event) => setJobSearch(event.target.value)}
              placeholder={lang === "zh" ? "搜索 job、国家、来源或最近任务" : "Search jobs, countries, sources, or last task"}
              className={cn(inputClass, "min-w-[240px] flex-1")}
            />
            <select
              value={jobFilter}
              onChange={(event) => setJobFilter(event.target.value as JobFilter)}
              className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
              aria-label={lang === "zh" ? "筛选 job" : "Filter jobs"}
            >
              <option value="all">{lang === "zh" ? "全部 job" : "All jobs"}</option>
              <option value="enabled">{lang === "zh" ? "已启用" : "Enabled"}</option>
              <option value="disabled">{lang === "zh" ? "已停用" : "Disabled"}</option>
              <option value="failed">{lang === "zh" ? "最近失败" : "Failed last run"}</option>
            </select>
            <ActionButton onClick={openNewJob} icon={<Plus className="h-4 w-4" />} tone="primary">
              {lang === "zh" ? "新建" : "New"}
            </ActionButton>
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
              rows={filteredJobs}
              getRowKey={(job) => job.job_id}
              selectedRowKey={drawerOpen ? editingJobId : null}
              onRowClick={startEdit}
              emptyState={
                <EmptyState
                  icon={<Bot className="h-10 w-10" />}
                  title={lang === "zh" ? "暂无自动化 job" : "No automation jobs"}
                  description={
                    jobSearch || jobFilter !== "all"
                      ? lang === "zh"
                        ? "当前筛选条件下没有匹配的 job。"
                        : "No jobs match the current filters."
                      : lang === "zh"
                        ? "创建一个 job 后，调度器会按计划生成采集任务。"
                        : "Create a job to let the scheduler generate crawl tasks on a schedule."
                  }
                />
              }
            />
          )}
        </section>
  );
}
