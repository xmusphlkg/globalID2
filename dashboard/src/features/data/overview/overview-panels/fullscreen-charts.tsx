
import { Download, X } from "lucide-react";

import { Chart } from "@/components/charts/Chart";

import { EmptyState } from "@/components/ui/EmptyState";

import { t } from "@/lib/i18n";

import type { OverviewContext } from "../hooks/use-overview-context";

export function FullscreenCharts({ context }: { context: OverviewContext }) {
  const { lang, isLoading, isTrendFullscreen, setIsTrendFullscreen, isMonthlyFullscreen, setIsMonthlyFullscreen, trendChart, fullscreenTrendChart, setFullscreenTrendChart, monthlyChart, fullscreenMonthlyChart, setFullscreenMonthlyChart, trend, focusLabel, currentDateRangeLabel, activeMetricLabel, chartSubtitle, trendExportTitle, monthlyExportTitle, trendOption, monthlyComparisonOption, downloadDashboardChartImage } = context;

  return (<>
      {isTrendFullscreen ? (
        <div className="fixed inset-0 z-50 flex flex-col bg-tremor-background dark:bg-dark-tremor-background">
          <div className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-tremor-border px-5 py-3 dark:border-dark-tremor-border">
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {t(lang, "trend")}
              </h2>
              <p className="truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {focusLabel} · {activeMetricLabel} · {currentDateRangeLabel}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() =>
                  downloadDashboardChartImage(
                    fullscreenTrendChart ?? trendChart,
                    trendExportTitle,
                    chartSubtitle,
                    "gids-epidemic-trend",
                  )
                }
                disabled={!trendOption}
                className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              >
                <Download className="h-4 w-4" />
                {lang === "zh" ? "下载图片" : "Download image"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsTrendFullscreen(false);
                  setFullscreenTrendChart(null);
                }}
                className="icon-button"
                aria-label={lang === "zh" ? "关闭全屏" : "Close fullscreen"}
                title={lang === "zh" ? "关闭全屏" : "Close fullscreen"}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="min-h-0 flex-1 p-4">
            {trendOption ? (
              <Chart
                className="h-full"
                height="calc(100vh - 112px)"
                option={trendOption}
                onReady={setFullscreenTrendChart}
              />
            ) : (
              <EmptyState
                title={isLoading ? t(lang, "loading") : t(lang, "no_data")}
                className="h-full rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
              />
            )}
          </div>
        </div>
      ) : null}

      {isMonthlyFullscreen ? (
        <div className="fixed inset-0 z-50 flex flex-col bg-tremor-background dark:bg-dark-tremor-background">
          <div className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-tremor-border px-5 py-3 dark:border-dark-tremor-border">
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {lang === "zh" ? "逐年月度对比" : "Yearly Monthly Comparison"}
              </h2>
              <p className="truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {focusLabel} · {activeMetricLabel} · {currentDateRangeLabel}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() =>
                  downloadDashboardChartImage(
                    fullscreenMonthlyChart ?? monthlyChart,
                    monthlyExportTitle,
                    chartSubtitle,
                    "gids-yearly-monthly-comparison",
                  )
                }
                disabled={!monthlyComparisonOption}
                className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              >
                <Download className="h-4 w-4" />
                {lang === "zh" ? "下载图片" : "Download image"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsMonthlyFullscreen(false);
                  setFullscreenMonthlyChart(null);
                }}
                className="icon-button"
                aria-label={lang === "zh" ? "关闭全屏" : "Close fullscreen"}
                title={lang === "zh" ? "关闭全屏" : "Close fullscreen"}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="min-h-0 flex-1 p-4">
            {monthlyComparisonOption ? (
              <Chart
                className="h-full"
                height="calc(100vh - 112px)"
                option={monthlyComparisonOption}
                onReady={setFullscreenMonthlyChart}
              />
            ) : (
              <EmptyState
                title={isLoading ? t(lang, "loading") : t(lang, "no_data")}
                className="h-full rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
              />
            )}
          </div>
        </div>
      ) : null}
  </>);
}
