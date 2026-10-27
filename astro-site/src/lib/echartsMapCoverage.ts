// src/lib/echartsMapCoverage.ts
// Lean ECharts registry used by the disease coverage choropleth.
//
// `echartsMap.ts` also registers VisualMapComponent and ScatterChart for the
// full /countries explorer. Disease pages already ship their own chart islands,
// so pulling those extra components in here pushed the largest disease route
// past the 370,000 byte compressed route-asset budget. This registry keeps only
// what a choropleth needs; the coverage map computes its own discrete colour
// buckets and renders territory markers as positioned dots instead.
import { MapChart } from 'echarts/charts';
import { GeoComponent, TooltipComponent } from 'echarts/components';
import echarts from './echartsCore';

echarts.use([MapChart, GeoComponent, TooltipComponent]);

export default echarts;
