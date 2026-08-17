import { BarChart, HeatmapChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import echarts from './echartsCore';

echarts.use([
  BarChart,
  HeatmapChart,
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
]);

export default echarts;
