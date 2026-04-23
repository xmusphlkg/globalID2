"use client";

import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import {
  BarChart as EBarChart,
  GraphChart as EGraphChart,
  LineChart as ELineChart,
  PieChart as EPieChart,
} from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { withTheme } from "@/lib/chart-theme";

echarts.use([
  EBarChart,
  EGraphChart,
  ELineChart,
  EPieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

export { echarts };

interface ChartProps {
  option: echarts.EChartsCoreOption;
  height?: number;
  className?: string;
}

export function Chart({ option, height = 350, className }: ChartProps) {
  return (
    <div className={className} style={{ minWidth: 0 }}>
      <ReactEChartsCore
        echarts={echarts}
        option={withTheme(option as Record<string, unknown>)}
        style={{ height, width: "100%", minWidth: 0 }}
        notMerge
        lazyUpdate
      />
    </div>
  );
}
