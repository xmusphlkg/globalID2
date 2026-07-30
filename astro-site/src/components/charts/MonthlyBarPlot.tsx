import { useMemo } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';
import type { YearSummary } from './monthlyBarModel';
import {
  MONTHLY_BAR_REPLACE_MERGE,
  buildMonthlyBarOption,
  type MonthlyBarChartColors,
} from './monthlyBarOption';

interface Props {
  summaries: YearSummary[];
  metricLabel: string;
  colors: MonthlyBarChartColors;
  title?: string;
  height: number | string;
}

export default function MonthlyBarPlot({
  summaries,
  metricLabel,
  colors,
  title,
  height,
}: Props) {
  const option = useMemo(
    () => buildMonthlyBarOption({
      summaries,
      metricLabel,
      colors,
      title,
    }),
    [colors, metricLabel, summaries, title]
  );

  return (
    <EChartsReact
      echarts={echarts}
      option={option}
      replaceMerge={MONTHLY_BAR_REPLACE_MERGE}
      lazyUpdate
      style={{ width: '100%', height }}
    />
  );
}
