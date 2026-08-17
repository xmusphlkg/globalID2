import {
  BarChart,
  HeatmapChart,
  SankeyChart,
  ThemeRiverChart,
} from 'echarts/charts';
import {
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  SingleAxisComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import echarts from './echartsCore';

echarts.use([
  BarChart,
  HeatmapChart,
  SankeyChart,
  ThemeRiverChart,
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  SingleAxisComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
]);

export default echarts;
