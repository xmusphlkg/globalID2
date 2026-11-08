import { useEffect, useRef, useState } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echarts';
import { BoxplotChart, ScatterChart } from 'echarts/charts';
import { AriaComponent } from 'echarts/components';
import { reportFigureView, type FigureLocale } from '../../../../shared/report-figure-options';

echarts.use([AriaComponent, BoxplotChart, ScatterChart]);
type Row = Record<string, unknown>;

function EvidenceFigure({ figure, data, lang }: { figure: Row; data: Row; lang: FigureLocale }) {
  const view = reportFigureView(figure, data, lang);
  const container = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!container.current || typeof IntersectionObserver === 'undefined') { setVisible(true); return; }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: '200px' });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  if (!view) return null;
  return <figure ref={container} className="mt-6 min-w-0 border border-[rgb(var(--border))] p-4">
    <h3 className="font-semibold">{lang === 'zh' ? '图' : 'Figure'} {String(figure.number)} · {view.title}</h3>
    <div className="min-w-0" style={{ minHeight: 340 }}>
      {visible && <EChartsReact echarts={echarts} option={view.option} notMerge style={{ height: 340, width: '100%' }} />}
    </div>
    <figcaption className="text-sm leading-6 text-[rgb(var(--text-muted))]">{view.caption}</figcaption>
    <details className="mt-3 text-xs">
      <summary className="cursor-pointer">{lang === 'zh' ? '查看图表数据' : lang === 'fr' ? 'Voir les données' : 'View chart data'}</summary>
      <div className="mt-2 overflow-x-auto"><table className="w-full text-left">
        <caption className="sr-only">{view.title}</caption>
        <thead><tr>{view.columns.map(column => <th key={column} scope="col" className="p-2">{column}</th>)}</tr></thead>
        <tbody>{view.rows.map((row, index) => <tr key={index}>{row.map((cell, i) => <td className="p-2" key={i}>{cell}</td>)}</tr>)}</tbody>
      </table></div>
    </details>
  </figure>;
}

export default function ReportEvidenceFigures({ figures, data, lang, sectionType }: { figures: Row[]; data: Row; lang: FigureLocale; sectionType: string }) {
  return <>{figures.filter(figure => figure.section_type === sectionType).slice(0, 1).map(figure => <EvidenceFigure key={String(figure.id)} figure={figure} data={data} lang={lang} />)}</>;
}
