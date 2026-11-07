/** Shared preview/public renderer. All statistics arrive frozen from Python. */
export type FigureLocale = "zh" | "en" | "fr";
type Row = Record<string, unknown>;
const record = (v: unknown): Row => v && typeof v === "object" && !Array.isArray(v) ? v as Row : {};
const array = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
const number = (v: unknown): number | null => typeof v === "number" && Number.isFinite(v) ? v : null;
export const figureText = (value: unknown, lang: FigureLocale): string => {
  const text = record(value)[lang] ?? record(value).en ?? value;
  return typeof text === "string" ? text : "";
};
const placements: Record<string, string> = {
  epidemic_curve: "disease_context", period_comparison_bar: "disease_context", period_cases_bar: "disease_context",
  cumulative_cases_line: "disease_context", rolling_average_line: "disease_context", period_change_bar: "disease_context",
  case_distribution_boxplot: "disease_context", case_distribution_histogram: "disease_context", historical_context_line: "disease_context",
  attention_ranking_bar: "signal_evidence", attention_dot: "signal_evidence", missingness_bar: "data_notes",
};
export function reportFigureView(figure: Row, figureData: Row, lang: FigureLocale) {
  const kind = String(figure.figure_type);
  if (figure.contract !== "report_v4.figures.1" || figureData.version !== "report_v4.figures.1"
    || figure.renderer !== "echarts" || figure.position !== "after_content" || !placements[kind]
    || figure.section_type !== placements[kind]) return null;
  const data = record(record(figureData.datasets)[String(figure.data_key)]);
  const t = (zh: string, en: string, fr: string) => ({ zh, en, fr })[lang];
  const format = (v: unknown) => number(v) === null ? "—" : (v as number).toLocaleString(lang, { maximumFractionDigits: 3 });
  const title = figureText(figure.title, lang), caption = figureText(figure.caption, lang);
  // Fixed declarative options only; model-supplied options/code are ignored.
  const option: Record<string, any> = {
    animation: false, aria: { enabled: true, description: `${title}. ${caption}` },
    grid: { top: 32, left: 65, right: 24, bottom: 65 },
    tooltip: { trigger: "axis", renderMode: "richText", confine: true },
  };
  let columns: string[], rows: string[][];
  const valueAxis = (name: string) => ({ type: "value", min: 0, name, nameLocation: "middle", nameGap: 45 });
  const category = (labels: string[]) => ({ type: "category", data: labels, axisLabel: { hideOverlap: true } });
  if (["attention_ranking_bar", "attention_dot", "missingness_bar"].includes(kind)) {
    const values = array(data.rows).map(record), missing = kind === "missingness_bar";
    if (values.length < 2 || values.length > 5 || values.some(r => number(r.score) === null || Number(r.score) < 0 || Number(r.score) > 100)) return null;
    const labels = values.map(r => figureText(r.names, lang));
    if (labels.some(v => !v)) return null;
    const metric = missing ? t("缺失比例 (%)", "Missing (%)", "Manquant (%)") : t("监测关注分", "Attention score", "Score d’attention");
    columns = [missing ? t("字段", "Field", "Champ") : t("疾病", "Disease", "Maladie"), metric];
    rows = values.map((r, i) => [labels[i], format(r.score)]);
    option.grid.left = 150;
    option.xAxis = { ...valueAxis(metric), max: 100, nameGap: 30 };
    option.yAxis = { ...category(labels), inverse: true, axisLabel: { width: 130, overflow: "break" } };
    option.series = [{ type: kind === "attention_dot" ? "scatter" : "bar", name: metric, data: values.map(r => r.score), symbolSize: 12, barMaxWidth: 32, itemStyle: { color: "#0f766e" } }];
  } else if (kind === "case_distribution_boxplot") {
    const summary = array(data.summary);
    if (summary.length !== 5 || summary.some((v, i) => number(v) === null || Number(v) < 0 || (i > 0 && Number(v) < Number(summary[i - 1]))) || Number(data.sample_size) < 8) return null;
    columns = [t("统计量", "Statistic", "Statistique"), t("病例", "Cases", "Cas")];
    rows = [t("最小值", "Minimum", "Minimum"), "Q1", t("中位数", "Median", "Médiane"), "Q3", t("最大值", "Maximum", "Maximum")].map((v, i) => [v, format(summary[i])]);
    rows.push([t("报告期数", "Periods", "Périodes"), format(data.sample_size)]);
    option.xAxis = category([t("本报告窗口", "Report window", "Fenêtre du rapport")]);
    option.yAxis = valueAxis(t("报告病例", "Reported cases", "Cas déclarés"));
    option.series = [{ type: "boxplot", data: [summary], itemStyle: { color: "#ccfbf1", borderColor: "#0f766e" } }];
  } else if (kind === "case_distribution_histogram") {
    const bins = array(data.bins).map(record);
    if (bins.length !== 5 || Number(data.sample_size) < 10 || bins.some((r, i) => [r.lower, r.upper, r.count].some(v => number(v) === null || Number(v) < 0) || Number(r.upper) <= Number(r.lower) || !Number.isInteger(r.count) || (i > 0 && r.lower !== bins[i - 1].upper)) || bins.reduce((n, r) => n + Number(r.count), 0) !== data.sample_size) return null;
    const labels = bins.map((r, i) => `[${format(r.lower)}, ${format(r.upper)}${i === 4 ? "]" : ")"}`);
    columns = [t("病例区间", "Case interval", "Intervalle des cas"), t("报告期数", "Periods", "Périodes")];
    rows = bins.map((r, i) => [labels[i], format(r.count)]);
    option.xAxis = category(labels); option.yAxis = valueAxis(columns[1]);
    option.series = [{ type: "bar", data: bins.map(r => r.count), barGap: "0%", barCategoryGap: "0%", itemStyle: { color: "#0f766e" } }];
  } else {
    const points = array(data.points).map(record), change = kind === "period_change_bar", rolling = kind === "rolling_average_line", historical = kind === "historical_context_line";
    if (points.length < 2 || points.length > 103) return null;
    const labels = points.map(r => String(r.period ?? "")), values = points.map(r => number(r.cases));
    if (labels.some((v, i) => !v || (i > 0 && v <= labels[i - 1])) || points.some(r => r.cases !== null && number(r.cases) === null) || (!change && values.some(v => v !== null && v < 0))) return null;
    const observed = values.filter(v => v !== null).length;
    if (kind === "period_comparison_bar" ? points.length !== 2 || observed !== 2 : observed < 3) return null;
    const metric = change ? t("病例增减", "Case difference", "Variation des cas") : t("报告病例", "Reported cases", "Cas déclarés");
    columns = [t("报告期", "Reporting period", "Période"), metric]; rows = points.map((r, i) => [labels[i], format(r.cases)]);
    option.xAxis = category(labels); option.yAxis = valueAxis(metric);
    if (change) option.yAxis.min = Math.min(0, ...values.filter((v): v is number => v !== null));
    const line = ["epidemic_curve", "cumulative_cases_line", "rolling_average_line", "historical_context_line"].includes(kind);
    option.series = [{ name: metric, type: line ? "line" : "bar", data: values, connectNulls: false, smooth: false, showSymbol: points.length < 18, barMaxWidth: 32, itemStyle: { color: "#0f766e" } }];
    if (rolling) {
      if (points.length < 6 || points.some((r, i) => i < 2 ? r.mean !== null : number(r.mean) === null || Number(r.mean) < 0)) return null;
      const meanLabel = t("三期均值", "Three-period mean", "Moyenne sur trois périodes");
      columns.push(meanLabel); rows.forEach((r, i) => r.push(format(points[i].mean)));
      option.legend = { top: 0 };
      option.series.push({ name: meanLabel, type: "line", data: points.map(r => r.mean), connectNulls: false, smooth: false, showSymbol: false, lineStyle: { type: "dashed", color: "#b45309" }, itemStyle: { color: "#b45309" } });
    }
    if (historical) {
      const start = String(data.report_start ?? ""), end = String(data.report_end ?? "");
      const current = labels.filter(v => v >= start && v <= end);
      if (!start || !end || start > end || !current.length || data.scope !== "historical_context") return null;
      columns.push(t("范围", "Scope", "Périmètre")); rows.forEach((r, i) => r.push(labels[i] < start ? t("历史背景", "Historical", "Historique") : t("本报告期", "Report period", "Période du rapport")));
      const segments = [...new Set(points.filter(p => p.cases !== null).map(p => String(p.segment ?? "same")))];
      if (segments.length > 3) return null;
      if (segments.some(segment => segment !== "same")) {
        columns.push(t("来源分段", "Source segment", "Segment source"));
        rows.forEach((r, i) => r.push(points[i].source ? `${String(points[i].source)} · ${segments.indexOf(String(points[i].segment)) + 1}` : "—"));
        option.series = segments.map((segment, index) => ({
          ...option.series[0], name: `${String(points.find(p => p.segment === segment)?.source ?? metric)} · ${index + 1}`,
          data: points.map(p => String(p.segment ?? "same") === segment ? p.cases : null),
          connectNulls: false, showSymbol: true, symbolSize: 4,
          itemStyle: { color: index === segments.length - 1 ? "#0f766e" : "#64748b" },
        }));
      }
      option.series[0].markArea = { silent: true, itemStyle: { color: "rgba(15,118,110,0.12)" }, data: [[{ xAxis: current[0] }, { xAxis: current.at(-1) }]] };
      // A single current period has zero-width area: mark its boundary as well.
      option.series[0].markLine = { silent: true, symbol: "none", label: { formatter: t("本报告期", "Report period", "Période du rapport") }, data: [{ xAxis: current[0] }] };
    }
  }
  return { title, caption, columns, rows, option };
}
