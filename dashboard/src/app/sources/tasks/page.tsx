"use client";

import { useMemo, useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useCancelTask, useTaskDetail, useTasks, useTaskWebSocket } from "@/lib/hooks/useTasks";
import { useQualitySources } from "@/lib/hooks/useQuality";
import { formatDate } from "@/lib/utils";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { Ban, ChevronDown, Download, Search } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  Color,
  DonutChart,
  Grid,
  Metric,
  ProgressBar,
  Text,
  Title,
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
  { value: "", label: "All task types" },
  { value: "crawl_data", label: "Crawl only" },
  { value: "generate_report", label: "Reports" },
  { value: "generate_section", label: "Sections" },
  { value: "review_section", label: "Reviews" },
  { value: "send_email", label: "Email" },
];

const cancellableStatuses = new Set(["pending", "queued", "running", "retrying"]);

export default function CrawlTasksPage() {
  const { lang, countryId } = useAppStore();
  const [statusFilter, setStatusFilter] = useState("");
  const [taskTypeFilter, setTaskTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [expandedUuid, setExpandedUuid] = useState<string | null>(null);

  useTaskWebSocket({ extraQueryKeys: [["sources-automation"], ["sources-flow"]] });

  const { data: tasks, isLoading } = useTasks(
    statusFilter || undefined,
    taskTypeFilter || undefined,
    countryId,
    search || undefined,
    200,
  );
  const { data: taskDetail, isFetching: detailLoading } = useTaskDetail(expandedUuid);
  const { data: sources } = useQualitySources(countryId);
  const cancelTask = useCancelTask();

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
            ? "查看所有任务，筛选类型，并在任务卡住时直接取消。"
            : "Review all tasks, filter by type, and cancel stuck runs directly from this page."}
        </Text>
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

      <Grid numItemsLg={3} className="gap-6">
        {sources && sources.length > 0 && (
          <Card className="lg:col-span-1">
            <Title>{t(lang, "data_sources")}</Title>
            <Text>Distribution by source</Text>
            <DonutChart
              className="mt-6 h-64"
              data={sources.map((s) => ({
                name: s.data_source ?? "Unknown",
                value: s.count,
              }))}
              category="value"
              index="name"
              colors={["slate", "blue", "cyan", "teal", "violet", "amber", "rose"]}
              showAnimation={true}
              showTooltip={true}
            />
          </Card>
        )}

        <Card className={sources && sources.length > 0 ? "lg:col-span-2" : "lg:col-span-3"}>
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative min-w-[220px] flex-1">
              <Search
                className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle"
                aria-hidden="true"
              />
              <input
                type="text"
                placeholder="Search tasks..."
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
                return (
                  <Card key={task.task_uuid} className="p-0">
                    <div className="flex items-stretch">
                      <button
                        className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-subtle"
                        onClick={() => setExpandedUuid(expanded ? null : task.task_uuid)}
                      >
                        <Badge color={statusBadge[task.status] ?? "slate"}>{task.status}</Badge>
                        <Badge color="slate">{task.task_type}</Badge>
                        <span className="min-w-0 flex-1 truncate text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                          {task.task_name}
                        </span>
                        <div className="hidden w-40 items-center gap-2 md:flex">
                          <ProgressBar value={task.progress} color={task.progress === 100 ? "emerald" : "teal"} className="flex-1" />
                          <Text>{task.progress}%</Text>
                        </div>
                        <Text className="hidden w-28 text-right md:block">{formatDate(task.created_at)}</Text>
                        <ChevronDown className={`h-4 w-4 text-tremor-content-subtle transition-transform ${expanded ? "rotate-180" : ""}`} />
                      </button>
                      <div className="flex items-center px-3">
                        <Button
                          size="xs"
                          color={canCancel ? "rose" : "slate"}
                          variant={canCancel ? "secondary" : "light"}
                          disabled={!canCancel || cancelTask.isPending}
                          icon={Ban}
                          onClick={() => handleCancel(task.task_uuid)}
                        >
                          {task.cancel_requested ? "Cancelling" : "Cancel"}
                        </Button>
                      </div>
                    </div>

                    {expanded && (
                      <div className="border-t border-tremor-border px-4 pb-4 pt-3 dark:border-dark-tremor-border">
                        <TaskDetailPanel taskDetail={taskDetail} detailLoading={detailLoading} emptyMessage="Failed to load task details" />
                      </div>
                    )}
                  </Card>
                );
              })
            )}
          </div>
        </Card>
      </Grid>
    </div>
  );
}
