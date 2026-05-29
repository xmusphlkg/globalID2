"use client";

import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import {
  BarChart as EBarChart,
  GraphChart as EGraphChart,
  HeatmapChart as EHeatmapChart,
  LineChart as ELineChart,
  PieChart as EPieChart,
  ScatterChart as EScatterChart,
} from "echarts/charts";
import {
  AriaComponent,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  TitleComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { withTheme } from "@/lib/chart-theme";

echarts.use([
  EBarChart,
  EGraphChart,
  EHeatmapChart,
  ELineChart,
  EPieChart,
  EScatterChart,
  AriaComponent,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  TitleComponent,
  VisualMapComponent,
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
