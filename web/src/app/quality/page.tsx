"use client";

import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import {
  useQualityStats,
  useQualityGaps,
  useQualitySources,
  useQualityCompleteness,
} from "@/lib/hooks/useQuality";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { KPICard } from "@/components/KPICard";
import { Chart } from "@/components/charts/Chart";
import { formatNumber } from "@/lib/utils";
import { ShieldCheck, Database, CalendarDays, Clock } from "lucide-react";
import { Badge, Card, ProgressBar, Text, Title } from "@tremor/react";

export default function QualityPage() {
  const { lang, countryId } = useAppStore();

  const { data: stats } = useQualityStats(countryId);
  const { data: gaps } = useQualityGaps(countryId);
  const { data: sources } = useQualitySources(countryId);
  const { data: completeness } = useQualityCompleteness(
    countryId,
    undefined,
    undefined,
    lang,
  );

  if (!countryId) {
    return (
      <Card>
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <ShieldCheck className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
          <Title className="mt-3">{t(lang, "no_data")}</Title>
          <Text>Select a country to view data quality metrics.</Text>
        </div>
      </Card>
    );
  }

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="blue" className="w-fit">{t(lang, "mod_database")}</Badge>
        <Title className="text-2xl">{t(lang, "quality")}</Title>
        <Text>Data quality metrics and completeness analysis</Text>
      </div>

      {stats && (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4 stagger">
          <KPICard
            title={t(lang, "total_records")}
            value={stats.total_records}
            icon={<Database className="h-5 w-5" />}
            accent="primary"
          />
          <KPICard
            title={t(lang, "total_diseases")}
            value={stats.unique_diseases}
            icon={<ShieldCheck className="h-5 w-5" />}
            accent="info"
          />
          <KPICard
            title="Earliest"
            value={stats.earliest_date ?? "—"}
            icon={<CalendarDays className="h-5 w-5" />}
            accent="success"
          />
          <KPICard
            title="Latest"
            value={stats.latest_date ?? "—"}
            icon={<Clock className="h-5 w-5" />}
            accent="warning"
          />
        </div>
      )}

      {stats && (
        <section>
          <Title className="mb-4">{t(lang, "zero_values")}</Title>
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <Card>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Text className="font-semibold">Zero Cases</Text>
                  <Badge color="amber">{stats.zero_cases_pct}%</Badge>
                </div>
                <ProgressBar value={stats.zero_cases_pct} color="amber" />
                <Text className="text-xs">
                  {formatNumber(stats.zero_cases_count)} records with zero cases
                </Text>
              </div>
            </Card>
            <Card>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Text className="font-semibold">Zero Deaths</Text>
                  <Badge color="amber">{stats.zero_deaths_pct}%</Badge>
                </div>
                <ProgressBar value={stats.zero_deaths_pct} color="amber" />
                <Text className="text-xs">
                  {formatNumber(stats.zero_deaths_count)} records with zero deaths
                </Text>
              </div>
            </Card>
          </div>
        </section>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {gaps && gaps.length > 0 && (
          <Card>
            <Title className="mb-3">{t(lang, "time_gaps")}</Title>
            <Chart
              height={280}
              option={{
                tooltip: { trigger: "axis" },
                grid: { left: 60, right: 20, bottom: 50, top: 20 },
                xAxis: {
                  type: "category",
                  data: gaps.map((g) => g.month),
                  axisLabel: { rotate: 30, fontSize: 11 },
                },
                yAxis: { type: "value", name: "Gap (months)", splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } } },
                series: [
                  {
                    type: "bar",
                    data: gaps.map((g) => g.gap_months),
                    barMaxWidth: 24,
                    itemStyle: { borderRadius: [4, 4, 0, 0] },
                  },
                ],
              }}
            />
          </Card>
        )}

        {sources && sources.length > 0 && (
          <Card>
            <Title className="mb-3">{t(lang, "data_sources")}</Title>
            <Chart
              height={280}
              option={{
                tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
                series: [
                  {
                    type: "pie",
                    radius: ["45%", "72%"],
                    center: ["50%", "50%"],
                    data: sources.map((s) => ({
                      name: s.data_source ?? "Unknown",
                      value: s.count,
                    })),
                    label: { show: true, formatter: "{b}\n{d}%", fontSize: 11 },
                    emphasis: {
                      itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: "rgba(0,0,0,0.15)" },
                    },
                  },
                ],
              }}
            />
          </Card>
        )}
      </div>

      {completeness && completeness.length > 0 && (
        <section>
          <Title className="mb-4">{t(lang, "completeness")}</Title>
          <Card className="overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-tremor-border dark:divide-dark-tremor-border">
                <thead className="bg-tremor-background-subtle dark:bg-dark-tremor-background-subtle">
                  <tr>
                    <th className="px-3 py-2.5 text-left text-xs font-semibold text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Disease</th>
                    <th className="px-3 py-2.5 text-right text-xs font-semibold text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Data Months</th>
                    <th className="px-3 py-2.5 text-right text-xs font-semibold text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Expected</th>
                    <th className="px-3 py-2.5 text-right text-xs font-semibold text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Rate</th>
                    <th className="px-3 py-2.5 text-right text-xs font-semibold text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Records</th>
                    <th className="px-3 py-2.5 text-left text-xs font-semibold text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Earliest</th>
                    <th className="px-3 py-2.5 text-left text-xs font-semibold text-tremor-content-subtle dark:text-dark-tremor-content-subtle">Latest</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-tremor-border dark:divide-dark-tremor-border">
                  {completeness.map((c, i) => (
                    <tr key={i} className="hover:bg-tremor-background-subtle/50 dark:hover:bg-dark-tremor-background-subtle/50">
                      <td className="px-3 py-2 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{c.disease_name}</td>
                      <td className="px-3 py-2 text-right text-sm text-tremor-content dark:text-dark-tremor-content">{c.data_months}</td>
                      <td className="px-3 py-2 text-right text-sm text-tremor-content dark:text-dark-tremor-content">{c.expected_months}</td>
                      <td className="px-3 py-2 text-right">
                        <Badge color={
                          c.completeness_rate >= 90
                            ? "emerald"
                            : c.completeness_rate >= 60
                              ? "amber"
                              : "rose"
                        }>
                          {c.completeness_rate.toFixed(1)}%
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-right text-sm text-tremor-content dark:text-dark-tremor-content">{formatNumber(c.total_records)}</td>
                      <td className="px-3 py-2 text-sm text-tremor-content dark:text-dark-tremor-content">{c.earliest_date ?? "—"}</td>
                      <td className="px-3 py-2 text-sm text-tremor-content dark:text-dark-tremor-content">{c.latest_date ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </section>
      )}
    </div>
  );
}
