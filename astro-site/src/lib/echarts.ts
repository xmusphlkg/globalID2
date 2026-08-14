// src/lib/echarts.ts
// Tree-shakable ECharts core – only registers chart types actually used.
import * as echarts from 'echarts/core';

import {
  LineChart,
  BarChart,
  HeatmapChart,
  MapChart,
  ScatterChart,
  SankeyChart,
  ThemeRiverChart,
} from 'echarts/charts';

import {
  AriaComponent,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  GeoComponent,
  SingleAxisComponent,
  ToolboxComponent,
  MarkAreaComponent,
  MarkLineComponent,
} from 'echarts/components';

import { CanvasRenderer } from 'echarts/renderers';

echarts.use([
  LineChart, BarChart, HeatmapChart, MapChart, ScatterChart,
  SankeyChart, ThemeRiverChart,
  AriaComponent, TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent, VisualMapComponent, GeoComponent,
  SingleAxisComponent, ToolboxComponent,
  MarkAreaComponent, MarkLineComponent,
  CanvasRenderer,
]);

export default echarts;
