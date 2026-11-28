"use client";

import { Chart } from "@/components/charts/Chart";
import { Card, Grid, Title } from "@/components/ui/tremor";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { type ChartBucket, statusColors } from "../helpers";

interface TaskOverviewChartsProps {
  statusChartData: ChartBucket[];
  typeChartData: ChartBucket[];
}

export function TaskOverviewCharts({
  statusChartData,
  typeChartData,
}: TaskOverviewChartsProps) {
  return (
    <Grid numItems={1} numItemsLg={2} className="gap-4">
      <Card>
        <Title className="mb-2">Status Distribution</Title>
        <Chart
          height={240}
          option={{
            tooltip: { trigger: "item" },
            series: [
              {
                type: "pie",
                radius: ["40%", "70%"],
                center: ["50%", "50%"],
                label: { formatter: "{b}: {d}%", color: CHART_TOKENS.text, fontSize: 11 },
                data: statusChartData.map((row) => ({
                  ...row,
                  itemStyle: { color: statusColors[row.name] ?? "#9ca3af" },
                })),
              },
            ],
          }}
        />
      </Card>
      <Card>
        <Title className="mb-2">Task Types</Title>
        <Chart
          height={240}
          option={{
            tooltip: { trigger: "axis" },
            grid: { left: 120, right: 20, bottom: 20, top: 10 },
            xAxis: {
              type: "value",
              splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } },
            },
            yAxis: {
              type: "category",
              data: typeChartData.map((r) => r.name).reverse(),
              axisLabel: { fontSize: 11 },
            },
            series: [
              {
                type: "bar",
                data: typeChartData.map((r) => r.value).reverse(),
                barMaxWidth: 20,
                itemStyle: { borderRadius: [0, 4, 4, 0], color: CHART_TOKENS.info },
              },
            ],
          }}
        />
      </Card>
    </Grid>
  );
}
