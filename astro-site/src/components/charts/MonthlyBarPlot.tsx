import { useMemo } from 'react';
import EChartsReact from '../../lib/echartsReact';
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
  lang: 'en' | 'zh' | 'fr';
  colors: MonthlyBarChartColors;
  title?: string;
  height: number | string;
}

export default function MonthlyBarPlot({
  summaries,
  metricLabel,
  lang,
  colors,
  title,
  height,
}: Props) {
  const option = useMemo(
    () => buildMonthlyBarOption({
      summaries,
      metricLabel,
      lang,
      colors,
      title,
    }),
    [colors, lang, metricLabel, summaries, title]
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
