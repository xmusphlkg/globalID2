import { MapChart, ScatterChart } from 'echarts/charts';
import {
  GeoComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import echarts from './echartsCore';

echarts.use([
  MapChart,
  ScatterChart,
  GeoComponent,
  TooltipComponent,
  VisualMapComponent,
]);

export default echarts;
