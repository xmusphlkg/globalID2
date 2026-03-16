// src/lib/echarts.ts
// Tree-shakable ECharts core – only registers chart types actually used.
import * as echarts from 'echarts/core';

import { LineChart, BarChart, HeatmapChart, MapChart, ScatterChart } from 'echarts/charts';

import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  GeoComponent,
} from 'echarts/components';

import { CanvasRenderer } from 'echarts/renderers';

echarts.use([
  LineChart, BarChart, HeatmapChart, MapChart, ScatterChart,
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent, VisualMapComponent, GeoComponent,
  CanvasRenderer,
]);

export default echarts;
