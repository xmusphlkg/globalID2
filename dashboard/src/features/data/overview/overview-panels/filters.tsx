
import { CalendarDays, RotateCcw } from "lucide-react";

import { FilterToolbar } from "@/components/ui/FilterToolbar";

import { t } from "@/lib/i18n";

import type { OverviewContext } from "../hooks/use-overview-context";
import { intervals } from "../presentation";

export function OverviewFilters({ context }: { context: OverviewContext }) {
  const { lang, summary, interval, setInterval, diseaseCode, setDiseaseCode, startDate, setStartDate, endDate, setEndDate, trendMetric, setTrendMetric, diseases, hasCustomDateRange, metricButtons } = context;

  return (<>
      <FilterToolbar>
        <select
          aria-label={lang === "zh" ? "疾病筛选" : "Disease filter"}
          value={diseaseCode || "all"}
          onChange={(event) => setDiseaseCode(event.target.value === "all" ? null : event.target.value)}
          className="h-10 min-w-[220px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
        >
          <option value="all">{t(lang, "all_diseases")}</option>
          {diseases?.map((disease) => (
            <option key={disease.code} value={disease.code}>
              {disease.display_name}
            </option>
          ))}
        </select>

        <select
          aria-label={lang === "zh" ? "时间范围" : "Date range"}
          value={interval ? String(interval) : "all"}
          onChange={(event) => {
            setInterval(event.target.value === "all" ? null : Number(event.target.value));
            setStartDate("");
            setEndDate("");
          }}
          className="h-10 min-w-[150px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
        >
          {intervals.map((item) => (
            <option key={item.labelKey} value={item.value ? String(item.value) : "all"}>
              {t(lang, item.labelKey)}
            </option>
          ))}
        </select>

        <div className="flex h-10 items-center gap-1 rounded-tremor-default border border-tremor-border bg-tremor-background-subtle p-1 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          {metricButtons.map((item) => {
            const Icon = item.icon;
            const active = trendMetric === item.value;
            return (
              <button
                key={item.value}
                type="button"
                onClick={() => setTrendMetric(item.value)}
                className={`inline-flex h-8 items-center gap-1.5 rounded-tremor-default px-2.5 text-sm font-medium transition ${
                  active
                    ? "bg-tremor-background text-tremor-content-strong shadow-[0_1px_2px_rgba(23,33,31,0.08)] dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                    : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
          <CalendarDays className="h-4 w-4 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
          <input
            type="date"
            value={startDate}
            min={summary?.earliest_date ?? undefined}
            max={endDate || summary?.latest_date || undefined}
            onChange={(event) => {
              setStartDate(event.target.value);
              setInterval(null);
            }}
            className="h-8 w-[135px] border-none bg-transparent p-0 text-sm text-tremor-content-strong outline-none dark:text-dark-tremor-content-strong"
            aria-label={lang === "zh" ? "开始日期" : "Start date"}
          />
          <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">to</span>
          <input
            type="date"
            value={endDate}
            min={startDate || summary?.earliest_date || undefined}
            max={summary?.latest_date ?? undefined}
            onChange={(event) => {
              setEndDate(event.target.value);
              setInterval(null);
            }}
            className="h-8 w-[135px] border-none bg-transparent p-0 text-sm text-tremor-content-strong outline-none dark:text-dark-tremor-content-strong"
            aria-label={lang === "zh" ? "结束日期" : "End date"}
          />
        </div>

        {hasCustomDateRange ? (
          <button
            type="button"
            onClick={() => {
              setStartDate("");
              setEndDate("");
            }}
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle"
          >
            <RotateCcw className="h-4 w-4" />
            {lang === "zh" ? "清除日期" : "Clear dates"}
          </button>
        ) : null}
      </FilterToolbar>

  </>);
}
