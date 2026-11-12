import { BarChart, HeatmapChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
  VisualMapContinuousComponent,
} from 'echarts/components';
import echarts from './echartsCore';

echarts.use([
  BarChart,
  HeatmapChart,
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
  VisualMapContinuousComponent,
]);

export default echarts;
