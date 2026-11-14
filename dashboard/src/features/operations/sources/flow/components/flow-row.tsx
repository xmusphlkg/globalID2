

import { type DataSourceFlow } from "@/features/operations/sources/api";

import { formatDate } from "@/lib/utils";
import { getSourceDisplayLabel } from "@/lib/source-labels";
import { formatSeriesMetadata } from "../series-format";
import { Badge, Text, Title } from "@/components/ui/tremor";

import { StatusBadge } from "@/components/ui/StatusBadge";
import { ChevronRight } from "lucide-react";

import { StagePill } from "./stage-pill";

// ── Row per data source ──────────────────────────────────────────────────────
export function FlowRow({
  flow,
  lang,
}: {
  flow: DataSourceFlow;
  lang: "en" | "zh";
}) {
  const sourceSeriesCount = flow.source_series_count ?? 0;
  const sourceObservationCount = flow.source_observation_count ?? 0;
  const hasSeriesFacts = sourceSeriesCount > 0 || sourceObservationCount > 0;
  const coverageStart = flow.earliest_date ?? (
    flow.history_start_year ? `${flow.history_start_year}-01-01` : null
  );
  const coverageText = coverageStart && flow.latest_date
    ? `${coverageStart} → ${flow.latest_date}`
    : coverageStart
      ? (lang === "zh" ? `配置起始：${coverageStart}` : `Configured from ${coverageStart}`)
      : null;

  return (
    <div className="app-panel overflow-hidden p-4">
      <div className="flex flex-col md:flex-row md:items-center gap-4">
        {/* Source name + stats */}
        <div className="w-full md:w-[320px] flex-shrink-0 space-y-1.5">
          {flow.country_name ? (
            <div className="flex items-center gap-2">
              <Badge color="teal">{flow.country_code || flow.country_name}</Badge>
              <Text className="text-xs">{flow.country_name}</Text>
            </div>
          ) : null}
          <Title className="text-sm font-semibold break-words whitespace-normal leading-tight">{flow.data_source}</Title>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span className="inline-flex items-center gap-1 text-tremor-content dark:text-dark-tremor-content">
              {hasSeriesFacts
                ? `${sourceObservationCount.toLocaleString()} ${lang === "zh" ? "条来源观测" : "source observations"}`
                : `${flow.record_count.toLocaleString()} ${lang === "zh" ? "条兼容记录" : "compatibility records"}`}
            </span>
            {hasSeriesFacts ? (
              <span className="inline-flex items-center gap-1 text-tremor-content dark:text-dark-tremor-content">
                {sourceSeriesCount.toLocaleString()} {lang === "zh" ? "个来源序列" : "source series"}
              </span>
            ) : null}
            {hasSeriesFacts && flow.record_count > 0 ? (
              <span className="inline-flex items-center gap-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh" ? "兼容投影" : "Compatibility projection"}: {flow.record_count.toLocaleString()}
              </span>
            ) : null}
            {flow.latest_date && (
              <span className="inline-flex items-center gap-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh" ? "数据覆盖截止" : "Coverage end"}: {flow.latest_date}
              </span>
            )}
            {coverageText && (
              <span className="inline-flex items-center gap-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh" ? "历史覆盖" : "History"}: {coverageText}
              </span>
            )}
          </div>
          {hasSeriesFacts ? (
            <div className="space-y-1.5 pt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {Object.entries(flow.metric_types ?? {}).length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {Object.entries(flow.metric_types).map(([metric, count]) => (
                    <StatusBadge key={metric} tone={metric === "registered_diagnoses" ? "warning" : "info"}>
                      {metric === "registered_diagnoses"
                        ? (lang === "zh" ? "登记诊断序列（非病例通知）" : "Registered-diagnosis series (not case notifications)")
                        : metric === "case_notifications"
                          ? (lang === "zh" ? "病例通知序列" : "Case-notification series")
                        : formatSeriesMetadata(metric)}
                      {` ${count}`}
                    </StatusBadge>
                  ))}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-1">
                {Object.entries(flow.series_availability ?? {}).map(([status, count]) => (
                  <StatusBadge key={`series-${status}`} tone={status === "active" ? "success" : "neutral"}>
                    {lang === "zh" ? "序列状态" : "Series"} {status} {count}
                  </StatusBadge>
                ))}
                {Object.entries(flow.source_availability ?? {}).map(([status, count]) => (
                  <StatusBadge key={`source-${status}`} tone={status === "available" ? "success" : "warning"}>
                    {lang === "zh" ? "可用性" : "Availability"} {status} {count}
                  </StatusBadge>
                ))}
                {Object.entries(flow.observation_quality ?? {}).map(([status, count]) => (
                  <StatusBadge key={`quality-${status}`} tone={status === "rejected" ? "danger" : "neutral"}>
                    {lang === "zh" ? "质量" : "Quality"} {status} {count.toLocaleString()}
                  </StatusBadge>
                ))}
                {Object.entries(flow.mapping_relations ?? {}).map(([status, count]) => (
                  <StatusBadge key={`mapping-${status}`} tone={status === "exact" ? "success" : "warning"}>
                    {lang === "zh" ? "映射" : "Mapping"} {status} {count}
                  </StatusBadge>
                ))}
                {Object.entries(flow.comparability ?? {}).map(([status, count]) => (
                  <StatusBadge key={`comparability-${status}`} tone={status === "direct" ? "success" : "neutral"}>
                    {lang === "zh" ? "可比性" : "Comparability"} {formatSeriesMetadata(status)} {count}
                  </StatusBadge>
                ))}
              </div>
            </div>
          ) : null}
          {flow.latest_task_uuid && (
            <div className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "最近运行/检查" : "Latest run/check"}: {getSourceDisplayLabel(flow.latest_task_source, lang, flow.country_code)}
              {" · "}
              {flow.latest_task_status || "-"}
              {flow.latest_task_time ? ` · ${formatDate(flow.latest_task_time)}` : ""}
            </div>
          )}
          {!flow.latest_task_uuid && flow.source_scope ? (
            <div className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh"
                ? "最近运行/检查：未记录（待首次调度运行）"
                : "Latest run/check: not recorded (awaiting first scheduled run)"}
            </div>
          ) : null}
        </div>

        {/* Pipeline stages */}
        <div className="flex flex-1 items-center gap-2 overflow-x-auto pb-2 md:pb-0 scrollbar-hide py-1">
          {flow.stages.map((stage, idx) => (
            <div key={stage.stage} className="flex items-center gap-2">
              <StagePill stage={stage} lang={lang} />
              {idx < flow.stages.length - 1 && (
                <ChevronRight className="h-4 w-4 shrink-0 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
