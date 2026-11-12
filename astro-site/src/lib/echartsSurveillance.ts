// Surveillance plots render lines, bars and marked thresholds.
import echarts from './echartsCore';

import {
  LineChart,
  BarChart,
} from 'echarts/charts';

import {
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';

echarts.use([
  LineChart,
  BarChart,
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
]);

export default echarts;
