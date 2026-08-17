// src/lib/echarts.ts
// Default registry for the common time-series and bar-chart islands.
import echarts from './echartsCore';

import {
  LineChart,
  BarChart,
} from 'echarts/charts';

import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';

echarts.use([
  LineChart,
  BarChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
]);

export default echarts;
