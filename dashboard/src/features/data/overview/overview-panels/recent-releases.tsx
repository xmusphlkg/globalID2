import { useMemo } from "react";
import { FileText } from "lucide-react";

import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { type ReportListItem } from "@/features/reports/api";
import { t } from "@/lib/i18n";
import { formatDate } from "@/lib/utils";

import type { OverviewContext } from "../hooks/use-overview-context";

export function RecentReleases({ context }: { context: OverviewContext }) {
  const { lang, releaseReports } = context;
  const reportColumns = useMemo<DataTableColumn<ReportListItem>[]>(
    () => [
      {
        key: "status",
        header: lang === "zh" ? "状态" : "Status",
        render: (report) => <StatusBadge status={report.status}>{report.status}</StatusBadge>,
      },
      {
        key: "title",
        header: lang === "zh" ? "报告" : "Report",
        render: (report) => (
          <div className="min-w-[240px] max-w-[420px]">
            <p className="line-clamp-2 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {report.title}
            </p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {report.section_count} {t(lang, "sections")}
            </p>
          </div>
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

  return (<>
      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {t(lang, "recent_releases")}
          </h2>
          <StatusBadge>{releaseReports?.length ?? 0}</StatusBadge>
        </div>
        <DataTable
          columns={reportColumns}
          rows={releaseReports ?? []}
          getRowKey={(report) => report.report_uuid}
          emptyState={<EmptyState icon={<FileText className="h-10 w-10" />} title={t(lang, "no_data")} />}
        />
      </section>

  </>);
}
