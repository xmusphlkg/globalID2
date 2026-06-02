"use client";

import { useEffect, useState } from "react";
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
import { type ChartTheme, withTheme } from "@/lib/chart-theme";

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

function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>(() => {
    if (typeof document === "undefined") return "light";
    return document.documentElement.classList.contains("dark") ||
      document.documentElement.getAttribute("data-theme") === "dark"
      ? "dark"
      : "light";
  });

  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    const update = () =>
      setTheme(root.classList.contains("dark") || root.getAttribute("data-theme") === "dark" ? "dark" : "light");
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { attributes: true, attributeFilter: ["class", "data-theme"] });
    return () => observer.disconnect();
  }, []);

  return theme;
}

interface ChartProps {
  option: echarts.EChartsCoreOption;
  height?: number;
  className?: string;
}

export function Chart({ option, height = 350, className }: ChartProps) {
  const theme = useChartTheme();
  return (
    <div className={className} style={{ minWidth: 0 }}>
      <ReactEChartsCore
        echarts={echarts}
        option={withTheme(option as Record<string, unknown>, theme)}
        style={{ height, width: "100%", minWidth: 0 }}
        notMerge
        lazyUpdate
      />
    </div>
  );
}
