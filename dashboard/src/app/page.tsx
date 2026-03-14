"use client";

import { useMemo, useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { useOverviewSummary, useOverviewTrend } from "@/lib/hooks/useOverview";
import { useDiseases } from "@/lib/hooks/useDiseases";
import { useReports } from "@/lib/hooks/useReports";
import { KPICard } from "@/components/KPICard";
import { Chart } from "@/components/charts/Chart";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { formatDate } from "@/lib/utils";
import { Activity, Database, CalendarDays, TrendingUp, FileText } from "lucide-react";
import { Card, Title, Text, Grid, Col, Badge, Flex, Select, SelectItem, List, ListItem } from "@tremor/react";

const intervals = [
  { labelKey: "interval_30d", value: 30 },
  { labelKey: "interval_90d", value: 90 },
  { labelKey: "interval_1y", value: 365 },
  { labelKey: "interval_all", value: null },
] as const;

export default function OverviewPage() {
  const { lang, countryId } = useAppStore();
  const { data: summary, isLoading } = useOverviewSummary(countryId, lang);
  const [interval, setInterval] = useState<number | null>(null);
  const [diseaseCode, setDiseaseCode] = useState<string | null>(null);

  const { data: diseases } = useDiseases(countryId, lang);
  const { data: trend } = useOverviewTrend(countryId, diseaseCode, interval);
  const { data: releaseReports } = useReports(countryId, undefined, 8);

  const releaseStats = useMemo(() => {
    const rows = releaseReports ?? [];
    const queue = rows.filter((r) => ["pending", "generating", "completed", "failed"].includes(r.status)).length;
    const review = rows.filter((r) => ["reviewing", "approved"].includes(r.status)).length;
    const published = rows.filter((r) => r.status === "published").length;
    return { queue, review, published };
  }, [releaseReports]);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="mb-8">
         <Title className="text-2xl">{t(lang, "dashboard")}</Title>
         <Text>Disease surveillance overview and release pipeline snapshot</Text>
      </div>

      <Grid numItems={1} numItemsSm={2} numItemsLg={4} className="gap-6 mb-6">
        <KPICard title={t(lang, "total_diseases")} value={summary?.total_diseases ?? "-"} icon={<Activity className="h-5 w-5" />} accent="primary" />
        <KPICard title={t(lang, "total_records")} value={summary?.total_records ?? "-"} icon={<Database className="h-5 w-5" />} accent="info" />
        <KPICard title={t(lang, "recent_cases")} value={summary?.recent_cases_30d ?? "-"} icon={<TrendingUp className="h-5 w-5" />} accent="warning" />
        <KPICard title={t(lang, "latest_date")} value={summary?.latest_date ? formatDate(summary.latest_date) : "-"} icon={<CalendarDays className="h-5 w-5" />} accent="success" />
      </Grid>

      <Grid numItems={1} numItemsLg={12} className="gap-6">
        <Col numColSpan={1} numColSpanLg={9}>
          <Card>
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-6 gap-4">
              <Title>{t(lang, "trend")}</Title>
              <div className="flex flex-col sm:flex-row gap-2">
                <Select
                  value={diseaseCode || "all"}
                  onValueChange={(val) => setDiseaseCode(val === "all" ? null : val)}
                  className="w-full sm:w-48"
                  placeholder={t(lang, "all_diseases")}
                >
                  <SelectItem value="all">{t(lang, "all_diseases")}</SelectItem>
                  {diseases?.map((d) => (
                    <SelectItem key={d.code} value={d.code}>{d.display_name}</SelectItem>
                  ))}
                </Select>
                <Select
                  value={interval ? String(interval) : "all"}
                  onValueChange={(val) => setInterval(val === "all" ? null : Number(val))}
                  className="w-full sm:w-36"
                  placeholder={t(lang, "interval_all")}
                >
                  {intervals.map((int) => (
                    <SelectItem key={int.labelKey} value={int.value ? String(int.value) : "all"}>
                      {t(lang, int.labelKey)}
                    </SelectItem>
                  ))}
                </Select>
              </div>
            </div>

            {trend && trend.length > 0 ? (
              <div className="h-80 w-full mt-4">
                <Chart
                  height={320}
                  option={{
                    tooltip: { trigger: "axis" },
                    legend: { data: [t(lang, "cases"), t(lang, "deaths")] },
                    grid: { left: 74, right: 20, bottom: 52, top: 36 },
                    dataZoom: [{ type: "inside" }, { type: "slider", height: 20 }],
                    xAxis: {
                      type: "category",
                      data: trend.map((r) => r.time_period),
                      axisLabel: { rotate: 30, fontSize: 11 },
                      boundaryGap: false,
                    },
                    yAxis: [
                      { type: "value", name: t(lang, "cases"), splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } } },
                      { type: "value", name: t(lang, "deaths"), splitLine: { show: false } },
                    ],
                    series: [
                      {
                        name: t(lang, "cases"),
                        type: "line",
                        data: trend.map((r) => r.cases),
                        smooth: true,
                        showSymbol: false,
                        areaStyle: { opacity: 0.08 },
                        lineStyle: { width: 2.5 },
                      },
                      {
                        name: t(lang, "deaths"),
                        type: "line",
                        yAxisIndex: 1,
                        data: trend.map((r) => r.deaths),
                        smooth: true,
                        showSymbol: false,
                        lineStyle: { width: 2.5 },
                      },
                    ],
                  }}
                />
              </div>
            ) : (
              <div className="h-80 flex items-center justify-center border border-dashed border-tremor-border rounded-tremor-default">
                <Text>{isLoading ? t(lang, "loading") : t(lang, "no_data")}</Text>
              </div>
            )}
          </Card>
        </Col>

        <Col numColSpan={1} numColSpanLg={3}>
          <div className="space-y-6">
             <Card>
               <Title>{t(lang, "release_snapshot")}</Title>
               <List className="mt-4">
                 <ListItem>
                   <Text>{t(lang, "flow_queue")}</Text>
                   <Badge color="slate">{releaseStats.queue}</Badge>
                 </ListItem>
                 <ListItem>
                   <Text>{t(lang, "flow_review")}</Text>
                   <Badge color="blue">{releaseStats.review}</Badge>
                 </ListItem>
                 <ListItem>
                   <Text>{t(lang, "flow_published")}</Text>
                   <Badge color="emerald">{releaseStats.published}</Badge>
                 </ListItem>
               </List>
             </Card>

             <Card>
               <Title className="mb-4">{t(lang, "recent_releases")}</Title>
               {releaseReports && releaseReports.length > 0 ? (
                 <div className="space-y-4">
                   {releaseReports.slice(0, 5).map((report) => (
                     <div key={report.report_uuid} className="p-3 border border-tremor-border rounded-tremor-default dark:border-dark-tremor-border">
                       <Flex alignItems="start" justifyContent="between" className="gap-2">
                         <Text className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong text-xs line-clamp-2">
                           {report.title}
                         </Text>
                         {['published', 'approved'].includes(report.status) ? (
                            <Badge color="emerald" size="xs">{report.status}</Badge>
                         ) : ['failed'].includes(report.status) ? (
                            <Badge color="rose" size="xs">{report.status}</Badge>
                         ) : (
                           <Badge color="slate" size="xs">{report.status}</Badge>
                         )}
                       </Flex>
                       <Flex className="mt-3 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                         <span>{formatDate(report.created_at)}</span>
                         <span>{report.section_count} {t(lang, "sections")}</span>
                       </Flex>
                     </div>
                   ))}
                 </div>
               ) : (
                 <div className="py-6 flex flex-col items-center justify-center text-center">
                   <FileText className="h-8 w-8 text-tremor-content-subtle mb-2" />
                   <Text>{t(lang, "no_data")}</Text>
                 </div>
               )}
             </Card>
          </div>
        </Col>
      </Grid>
    </div>
  );
}
