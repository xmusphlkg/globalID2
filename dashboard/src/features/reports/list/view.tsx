"use client";

import { useEffect, useMemo, useState } from "react";

import { FileText, Gauge, Layers3, Search, Send, TimerReset } from "lucide-react";

import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { type ReportListItem, useReportDetail, useReports } from "@/features/reports/api";

import { ReportWorkflow } from "@/features/reports/workflow";

import { t } from "@/lib/i18n";
import { formatDate } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

import { statusLabel, percent, periodDate } from "./presentation";
import { FLOW_GROUPS, type FlowGroupId } from "./presentation";
import { QualityCell, ReportTitleCell } from "./components/table-cells";
import { ReportDetailPanel } from "./components/report-detail-panel";
export default function ReportsPage() {
  const { lang } = useAppStore();
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const [flowFilter, setFlowFilter] = useState<FlowGroupId | "all">("all");
  const [search, setSearch] = useState("");

  const { data: reports, isLoading } = useReports("JP");
  const { data: detail, isFetching: detailLoading } = useReportDetail(selectedUuid);
  const list = reports ?? [];

  useEffect(() => {
    if (selectedUuid && !list.some((item) => item.report_uuid === selectedUuid)) {
      setSelectedUuid(null);
    }
  }, [list, selectedUuid]);

  const flowCounts = useMemo(() => {
    return {
      all: list.length,
      queue: list.filter((item) => FLOW_GROUPS[0].statuses.includes(item.status)).length,
      review: list.filter((item) => FLOW_GROUPS[1].statuses.includes(item.status)).length,
      published: list.filter((item) => FLOW_GROUPS[2].statuses.includes(item.status)).length,
    };
  }, [list]);

  const filteredReports = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    const activeStatuses =
      flowFilter === "all"
        ? null
        : FLOW_GROUPS.find((group) => group.id === flowFilter)?.statuses ?? [];

    return list.filter((report) => {
      if (activeStatuses && !activeStatuses.includes(report.status)) return false;
      if (!normalizedSearch) return true;

      const haystack = [
        report.title,
        report.report_type,
        report.status,
        report.country_name,
        report.primary_disease,
        ...(report.disease_names ?? []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      return haystack.includes(normalizedSearch);
    });
  }, [flowFilter, list, search]);

  const selectedReport = useMemo(
    () => list.find((item) => item.report_uuid === selectedUuid) ?? null,
    [list, selectedUuid],
  );

  const avgQuality = useMemo(() => {
    const scores = list
      .map((item) => item.quality_score)
      .filter((score): score is number => score != null);

    if (scores.length === 0) return null;
    return scores.reduce((sum, score) => sum + score, 0) / scores.length;
  }, [list]);

  const hasFilters = Boolean(search || flowFilter !== "all");

  const columns = useMemo<DataTableColumn<ReportListItem>[]>(
    () => [
      {
        key: "status",
        header: lang === "zh" ? "状态" : "Status",
        render: (report) => <StatusBadge status={report.status}>{statusLabel(lang, report.status)}</StatusBadge>,
      },
      {
        key: "report",
        header: t(lang, "report_title"),
        render: (report) => <ReportTitleCell report={report} />,
      },
      {
        key: "type",
        header: t(lang, "report_type"),
        render: (report) => (
          <span className="whitespace-nowrap font-mono text-xs text-tremor-content dark:text-dark-tremor-content">
            {report.report_type}
          </span>
        ),
      },
      {
        key: "quality",
        header: t(lang, "quality_score"),
        render: (report) => <QualityCell score={report.quality_score} />,
      },
      {
        key: "sections",
        header: t(lang, "sections"),
        render: (report) => (
          <span className="whitespace-nowrap text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {report.section_count}
          </span>
        ),
      },
      {
        key: "period",
        header: lang === "zh" ? "周期" : "Period",
        render: (report) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {periodDate(report.period_start)} - {periodDate(report.period_end)}
          </span>
        ),
      },
      {
        key: "created",
        header: lang === "zh" ? "创建时间" : "Created",
        render: (report) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDate(report.created_at)}
          </span>
        ),
      },
    ],
    [lang],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_results")}
        title={t(lang, "reports")}
        description={t(lang, "publication_flow")}
        meta={
          <>
            <StatusBadge tone={flowCounts.queue > 0 ? "warning" : "neutral"}>
              {t(lang, "flow_queue")} {flowCounts.queue}
            </StatusBadge>
            <StatusBadge tone="info">
              {t(lang, "flow_review")} {flowCounts.review}
            </StatusBadge>
            <StatusBadge tone="success">
              {t(lang, "flow_published")} {flowCounts.published}
            </StatusBadge>
          </>
        }
      />

      <ReportWorkflow lang={lang} />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={t(lang, "report_overview_total")}
          value={isLoading ? "-" : flowCounts.all}
          icon={<Layers3 className="h-4 w-4" />}
          tone="neutral"
          hint={lang === "zh" ? "当前国家报告" : "Reports for current country"}
        />
        <MetricTile
          label={t(lang, "report_overview_filtered")}
          value={isLoading ? "-" : filteredReports.length}
          icon={<Send className="h-4 w-4" />}
          tone="primary"
          hint={flowFilter === "all" ? t(lang, "flow_all") : t(lang, FLOW_GROUPS.find((group) => group.id === flowFilter)?.titleKey ?? "flow_all")}
        />
        <MetricTile
          label={t(lang, "report_overview_published")}
          value={isLoading ? "-" : flowCounts.published}
          icon={<FileText className="h-4 w-4" />}
          tone="success"
          hint={lang === "zh" ? "可用于对外发布" : "Ready for downstream use"}
        />
        <MetricTile
          label={t(lang, "report_overview_quality")}
          value={isLoading ? "-" : percent(avgQuality)}
          icon={<Gauge className="h-4 w-4" />}
          tone="info"
          hint={lang === "zh" ? "按已有质量分计算" : "Based on scored reports"}
        />
      </div>

      <FilterToolbar>
        <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
          <input
            type="search"
            placeholder={lang === "zh" ? "搜索标题、疾病、类型" : "Search title, disease, or type"}
            className="h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={`inline-flex h-10 items-center gap-2 rounded-tremor-default border px-3 text-sm font-medium transition ${
              flowFilter === "all"
                ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted dark:border-dark-tremor-brand dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
                : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
            }`}
            onClick={() => setFlowFilter("all")}
          >
            {t(lang, "flow_all")}
            <span className="rounded-tremor-default bg-black/10 px-2 py-0.5 text-xs dark:bg-white/10">{flowCounts.all}</span>
          </button>
          {FLOW_GROUPS.map((group) => (
            <button
              key={group.id}
              type="button"
              className={`inline-flex h-10 items-center gap-2 rounded-tremor-default border px-3 text-sm font-medium transition ${
                flowFilter === group.id
                  ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted dark:border-dark-tremor-brand dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
                  : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              }`}
              onClick={() => setFlowFilter(group.id)}
            >
              {t(lang, group.titleKey)}
              <span className="rounded-tremor-default bg-black/10 px-2 py-0.5 text-xs dark:bg-white/10">
                {flowCounts[group.id]}
              </span>
            </button>
          ))}
        </div>

        {hasFilters ? (
          <button
            type="button"
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            onClick={() => {
              setSearch("");
              setFlowFilter("all");
            }}
          >
            <TimerReset className="h-4 w-4" />
            {lang === "zh" ? "重置" : "Reset"}
          </button>
        ) : null}
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
          rows={filteredReports}
          getRowKey={(report) => report.report_uuid}
          selectedRowKey={selectedUuid}
          onRowClick={(report) => setSelectedUuid(report.report_uuid)}
          emptyState={
            <EmptyState
              icon={<FileText className="h-10 w-10" />}
              title={t(lang, "flow_empty")}
              description={
                hasFilters
                  ? lang === "zh"
                    ? "当前筛选条件下没有报告。"
                    : "No reports match the current filters."
                  : lang === "zh"
                    ? "当前国家还没有生成报告。"
                    : "No reports have been generated for the current country."
              }
            />
          }
        />
      )}

      <DetailDrawer
        open={Boolean(selectedUuid)}
        title={selectedReport?.title ?? (lang === "zh" ? "报告详情" : "Report Detail")}
        subtitle={
          selectedReport ? (
            <span className="flex min-w-0 items-center gap-2">
              <StatusBadge status={selectedReport.status}>{statusLabel(lang, selectedReport.status)}</StatusBadge>
              <span className="truncate">{selectedReport.report_type}</span>
              <span>{formatDate(selectedReport.created_at)}</span>
            </span>
          ) : null
        }
        onClose={() => setSelectedUuid(null)}
        className="sm:w-[820px] sm:max-w-[820px]"
      >
        <ReportDetailPanel detail={detail} lang={lang} loading={detailLoading} />
      </DetailDrawer>
    </div>
  );
}
