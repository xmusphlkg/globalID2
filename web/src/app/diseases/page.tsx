"use client";

import { useEffect, useMemo, useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import {
  useDiseases,
  useDiseaseRecords,
  useCompare,
} from "@/lib/hooks/useDiseases";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { KPICard } from "@/components/KPICard";
import { Chart } from "@/components/charts/Chart";
import { Activity, Skull, Percent, BarChart3 } from "lucide-react";
import { Title, Text, Card, Grid, Select, SelectItem, MultiSelect, MultiSelectItem, Flex, Badge } from "@tremor/react";

export default function DiseasesPage() {
  const { lang, countryId } = useAppStore();
  const { data: diseases } = useDiseases(countryId, lang);

  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [compareCodes, setCompareCodes] = useState<string[]>([]);

  const { data: records } = useDiseaseRecords(
    compareMode ? null : selectedCode,
    countryId,
  );
  const { data: compareData } = useCompare(
    compareMode ? countryId : null,
    compareCodes,
  );

  useEffect(() => {
    if (!compareMode && !selectedCode && diseases && diseases.length > 0) {
      setSelectedCode(diseases[0].code);
    }
  }, [compareMode, selectedCode, diseases]);

  const compareXAxis = useMemo(() => {
    if (!compareData?.diseases) return [] as string[];
    return Array.from(
      new Set(compareData.diseases.flatMap((d) => d.data.map((row) => row.time_period))),
    ).sort();
  }, [compareData]);

  if (!countryId) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-tremor-content-subtle">
        <Activity className="h-12 w-12 mb-4" />
        <Title>{t(lang, "no_data")}</Title>
        <Text>Select a country to analyze diseases.</Text>
      </div>
    );
  }

  const totalCases = records?.reduce((s, r) => s + (r.cases ?? 0), 0) ?? 0;
  const totalDeaths = records?.reduce((s, r) => s + (r.deaths ?? 0), 0) ?? 0;
  const cfr = totalCases > 0 ? ((totalDeaths / totalCases) * 100).toFixed(2) : "0.00";
  const avgMonthly = records && records.length > 0 ? Math.round(totalCases / records.length) : 0;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-2">
          <Badge size="sm" color="blue">2</Badge>
          <Text className="text-xs font-semibold uppercase tracking-wider text-tremor-content-subtle">{t(lang, "mod_database")}</Text>
        </div>
        <Title className="text-2xl">{t(lang, "diseases")}</Title>
        <Text>Detailed disease analysis and comparison</Text>
      </div>

      <Card className="mb-6 py-4">
        <Flex alignItems="center" justifyContent="between" className="flex-col sm:flex-row gap-4">
           <div className="flex items-center gap-3 bg-tremor-background-muted dark:bg-dark-tremor-background-muted p-1 rounded-tremor-default self-start sm:self-auto">
             <button
                className={`px-4 py-1.5 text-sm font-medium rounded-tremor-small transition-colors ${!compareMode ? "bg-tremor-background shadow-tremor-card text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong" : "text-tremor-content dark:text-dark-tremor-content"}`}
                onClick={() => setCompareMode(false)}
             >
               Analysis
             </button>
             <button
                className={`px-4 py-1.5 text-sm font-medium rounded-tremor-small transition-colors ${compareMode ? "bg-tremor-background shadow-tremor-card text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong" : "text-tremor-content dark:text-dark-tremor-content"}`}
                onClick={() => setCompareMode(true)}
             >
               Compare
             </button>
           </div>
           
           <div className="w-full sm:w-auto min-w-[280px]">
             {compareMode ? (
                <MultiSelect
                   value={compareCodes}
                   onValueChange={setCompareCodes}
                   placeholder="Select diseases to compare..."
                >
                   {diseases?.map((d) => (
                     <MultiSelectItem key={d.code} value={d.code}>{d.display_name}</MultiSelectItem>
                   ))}
                </MultiSelect>
             ) : (
                <Select
                   value={selectedCode || "all"}
                   onValueChange={(v) => setSelectedCode(v === "all" ? null : v)}
                   placeholder={t(lang, "all_diseases")}
                >
                   <SelectItem value="all">{t(lang, "all_diseases")}</SelectItem>
                   {diseases?.map((d) => (
                     <SelectItem key={d.code} value={d.code}>{d.display_name}</SelectItem>
                   ))}
                </Select>
             )}
           </div>
        </Flex>
      </Card>

      {!compareMode ? (
         <div className="space-y-6">
            <Grid numItems={1} numItemsSm={2} numItemsLg={4} className="gap-6">
               <KPICard title={t(lang, "total_cases")} value={totalCases} icon={<Activity className="h-5 w-5" />} accent="warning" />
               <KPICard title={t(lang, "total_deaths")} value={totalDeaths} icon={<Skull className="h-5 w-5" />} accent="error" />
               <KPICard title={t(lang, "cfr")} value={`${cfr}%`} icon={<Percent className="h-5 w-5" />} accent="info" />
              <KPICard title={t(lang, "avg_monthly")} value={avgMonthly} icon={<BarChart3 className="h-5 w-5" />} accent="primary" />
            </Grid>

            <Card>
              <Title>{t(lang, "cases_trend")}</Title>
               <div className="h-96 mt-4">
                 {records && records.length > 0 ? (
                    <Chart
                      option={{
                        tooltip: { trigger: "axis" },
                        legend: { data: [t(lang, "cases"), t(lang, "deaths")] },
                        grid: { left: 60, right: 20, bottom: 40, top: 40 },
                        xAxis: {
                          type: "category",
                          data: records.map((r) => r.time),
                          boundaryGap: false,
                        },
                        yAxis: [
                          { type: "value", name: t(lang, "cases"), splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } } },
                          { type: "value", name: t(lang, "deaths"), show: false },
                        ],
                        dataZoom: [{ type: "inside" }, { type: "slider", height: 20 }],
                        series: [
                          {
                            name: t(lang, "cases"),
                            type: "line",
                            data: records.map((r) => r.cases),
                            smooth: true,
                            areaStyle: { opacity: 0.1 },
                            lineStyle: { width: 3 },
                            itemStyle: { color: CHART_TOKENS.warning },
                          },
                          {
                            name: t(lang, "deaths"),
                            type: "line",
                            yAxisIndex: 1,
                            data: records.map((r) => r.deaths),
                            smooth: true,
                            lineStyle: { width: 3 },
                            itemStyle: { color: CHART_TOKENS.destructive },
                          },
                        ],
                      }}
                    />
                 ) : (
                    <div className="h-full flex items-center justify-center border border-dashed border-tremor-border rounded-tremor-default">
                       <Text>{t(lang, "no_data")}</Text>
                    </div>
                 )}
               </div>
            </Card>
         </div>
      ) : (
         <Card>
            <Title className="mb-4">Disease Comparison</Title>
            <div className="h-96">
               {(compareData && compareData.diseases.length > 0 && compareCodes.length > 0) ? (
                 <Chart
                   option={{
                     tooltip: { trigger: "axis" },
                     legend: { data: compareData.diseases.map((d) => d.disease_name) },
                     grid: { left: 50, right: 20, bottom: 40, top: 40 },
                     xAxis: {
                       type: "category",
                       data: compareXAxis,
                     },
                     yAxis: { type: "value", splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } } },
                     series: compareData.diseases.map((disease) => ({
                       name: disease.disease_name,
                       type: "line",
                       smooth: true,
                       data: compareXAxis.map((period) => {
                         const match = disease.data.find((row) => row.time_period === period);
                         return match ? match.cases : 0;
                       }),
                       lineStyle: { width: 2 },
                     })),
                   }}
                 />
               ) : (
                 <div className="h-full flex items-center justify-center border border-dashed border-tremor-border rounded-tremor-default">
                    <Text className="text-tremor-content-subtle">Select multiple diseases above to compare</Text>
                 </div>
               )}
            </div>
         </Card>
      )}
    </div>
  );
}
