
import { Maximize2 } from "lucide-react";

import { Chart } from "@/components/charts/Chart";

import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { t } from "@/lib/i18n";
import { formatNumber } from "@/lib/utils";

import type { OverviewContext } from "../hooks/use-overview-context";

export function TrendPanel({ context }: { context: OverviewContext }) {
  const { lang, summary, isLoading, setIsTrendFullscreen, setTrendChart, trend, releaseStats, focusLabel, currentDateRangeLabel, activeMetricLabel, trendOption } = context;

  return (<>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="app-panel p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {t(lang, "trend")}
              </h2>
              <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {focusLabel} · {activeMetricLabel} · {currentDateRangeLabel}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <StatusBadge tone="info">{trend?.length ?? 0} points</StatusBadge>
              <button
                type="button"
                onClick={() => setIsTrendFullscreen(true)}
                disabled={!trendOption}
                className="icon-button disabled:cursor-not-allowed disabled:opacity-50"
                aria-label={lang === "zh" ? "全屏查看" : "View fullscreen"}
                title={lang === "zh" ? "全屏查看" : "View fullscreen"}
              >
                <Maximize2 className="h-4 w-4" />
              </button>
            </div>
          </div>

          {trendOption ? (
            <Chart
              height={330}
              option={trendOption}
              onReady={setTrendChart}
            />
          ) : (
            <EmptyState
              title={isLoading ? t(lang, "loading") : t(lang, "no_data")}
              className="h-[330px] rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
            />
          )}
        </section>

        <aside className="space-y-4">
          <section className="app-panel p-4">
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {t(lang, "top_diseases")}
            </h2>
            {summary?.top_diseases?.length ? (
              <div className="mt-4 space-y-2">
                {summary.top_diseases.slice(0, 5).map((disease, index) => (
                  <div
                    key={`${disease.name}-${index}`}
                    className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                          {disease.name}
                        </p>
                        <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {formatNumber(disease.total_cases)} {t(lang, "cases")} · {disease.total_deaths === null ? "–" : formatNumber(disease.total_deaths)} {t(lang, "deaths")}
                        </p>
                      </div>
                      <StatusBadge tone={index === 0 ? "success" : "neutral"}>{index + 1}</StatusBadge>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState title={t(lang, "no_data")} className="py-10" />
            )}
          </section>

          <section className="app-panel p-4">
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {t(lang, "release_snapshot")}
            </h2>
            <div className="mt-4 grid gap-2">
              <div className="flex items-center justify-between rounded-tremor-default bg-tremor-background-subtle px-3 py-2 dark:bg-dark-tremor-background-subtle">
                <span className="text-sm text-tremor-content dark:text-dark-tremor-content">{t(lang, "flow_queue")}</span>
                <StatusBadge>{releaseStats.queue}</StatusBadge>
              </div>
              <div className="flex items-center justify-between rounded-tremor-default bg-tremor-background-subtle px-3 py-2 dark:bg-dark-tremor-background-subtle">
                <span className="text-sm text-tremor-content dark:text-dark-tremor-content">{t(lang, "flow_review")}</span>
                <StatusBadge tone="info">{releaseStats.review}</StatusBadge>
              </div>
              <div className="flex items-center justify-between rounded-tremor-default bg-tremor-background-subtle px-3 py-2 dark:bg-dark-tremor-background-subtle">
                <span className="text-sm text-tremor-content dark:text-dark-tremor-content">{t(lang, "flow_published")}</span>
                <StatusBadge tone="success">{releaseStats.published}</StatusBadge>
              </div>
            </div>
          </section>
        </aside>
      </div>

  </>);
}
