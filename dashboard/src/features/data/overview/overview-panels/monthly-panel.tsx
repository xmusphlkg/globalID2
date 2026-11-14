
import { Maximize2 } from "lucide-react";

import { Chart } from "@/components/charts/Chart";

import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { t } from "@/lib/i18n";

import type { OverviewContext } from "../hooks/use-overview-context";

export function MonthlyPanel({ context }: { context: OverviewContext }) {
  const { lang, isLoading, setIsMonthlyFullscreen, setMonthlyChart, monthlyComparison, focusLabel, currentDateRangeLabel, activeMetricLabel, monthlyComparisonOption } = context;

  return (<>
      <section className="app-panel p-4">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {lang === "zh" ? "逐年月度对比" : "Yearly Monthly Comparison"}
            </h2>
            <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {focusLabel} · {activeMetricLabel} · {currentDateRangeLabel}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge tone="info">
              {monthlyComparison ? `${new Set(monthlyComparison.map((row) => row.year)).size} years` : "0 years"}
            </StatusBadge>
            <button
              type="button"
              onClick={() => setIsMonthlyFullscreen(true)}
              disabled={!monthlyComparisonOption}
              className="icon-button disabled:cursor-not-allowed disabled:opacity-50"
              aria-label={lang === "zh" ? "全屏查看" : "View fullscreen"}
              title={lang === "zh" ? "全屏查看" : "View fullscreen"}
            >
              <Maximize2 className="h-4 w-4" />
            </button>
          </div>
        </div>

        {monthlyComparisonOption ? (
          <Chart
            height={360}
            option={monthlyComparisonOption}
            onReady={setMonthlyChart}
          />
        ) : (
          <EmptyState
            title={isLoading ? t(lang, "loading") : t(lang, "no_data")}
            className="h-[360px] rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
          />
        )}
      </section>

  </>);
}
