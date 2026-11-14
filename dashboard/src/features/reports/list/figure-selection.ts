

import { type ReportSection } from "@/features/reports/api";

import { asRecord, asArray, asNumber, asString } from "./presentation";
export function assignFigureNumbers(sections: ReportSection[], allFigures: Record<string, unknown>[]) {
  let nextFigureNumber = 1;
  return sections.map((section) => {
    const charts = sectionFigures(section, allFigures);
    const numberFigures = (placement: "before_content" | "after_content") =>
      charts
        .filter((figure) => (asString(figure.position) || "after_content") === placement)
        .map((figure) => ({
          ...figure,
          display_number: nextFigureNumber++,
        }));

    return {
      section,
      beforeFigures: numberFigures("before_content"),
      afterFigures: numberFigures("after_content"),
    };
  });
}

export function isAuditReportSection(section: ReportSection) {
  return section.section_type === "methodology" || section.section_type === "evidence_appendix";
}

export function sectionFigures(section: ReportSection, allFigures: Record<string, unknown>[]) {
  const directCharts = asArray(section.charts).map(asRecord);
  if (directCharts.length > 0) return curateSectionFigures(section, directCharts);

  const metadata = asRecord(section.metadata);
  const metadataFigures = asArray(metadata.figure_specs).map(asRecord);
  if (metadataFigures.length > 0) return curateSectionFigures(section, metadataFigures);

  const sectionType = asString(section.section_type);
  if (!sectionType) return [];
  return curateSectionFigures(section, allFigures.filter((figure) => asString(figure.section_type) === sectionType));
}

export function sectionDisplayType(section: ReportSection) {
  const metadata = asRecord(section.metadata);
  return asString(metadata.display_type) || asString(section.section_type) || "section";
}

export function firstFigureOfType(figures: Record<string, unknown>[], types: string[]) {
  for (const type of types) {
    const found = figures.find((figure) => asString(figure.figure_type) === type);
    if (found) return found;
  }
  return null;
}

export function sectionNeedsDataQualityFigure(section: ReportSection) {
  const metadata = asRecord(section.metadata);
  const quality = asRecord(metadata.data_quality);
  const score = asNumber(quality.score);
  if (score !== null && score < 0.9) return true;
  const missing = asRecord(quality.missing_rates);
  return Object.values(missing).some((value) => Number(value) >= 0.2);
}

export function curateSectionFigures(section: ReportSection, figures: Record<string, unknown>[]) {
  const renderable = figures.filter((figure) => asString(figure.renderer) === "echarts" || !asString(figure.renderer));
  const sectionType = asString(section.section_type) || asString(renderable[0]?.section_type);

  if (sectionType === "trend_anomaly_analysis") {
    const primary = firstFigureOfType(renderable, ["anomaly_marker_curve", "seasonal_baseline_band", "epidemic_curve"]);
    const secondary = firstFigureOfType(
      renderable.filter((figure) => figure !== primary),
      ["epidemic_curve", "recent_window_heatmap"],
    );
    return [primary, secondary].filter((figure): figure is Record<string, unknown> => Boolean(figure));
  }

  if (sectionType === "priority_signals") {
    return [firstFigureOfType(renderable, ["risk_matrix", "risk_ranking_bar", "signal_context_panel"])].filter(
      (figure): figure is Record<string, unknown> => Boolean(figure),
    );
  }

  if (sectionType === "disease_profiles") {
    return [firstFigureOfType(renderable, ["cases_incidence_panel", "epidemic_curve"])].filter(
      (figure): figure is Record<string, unknown> => Boolean(figure),
    );
  }

  if (sectionType === "data_quality_limitations" && !sectionNeedsDataQualityFigure(section)) {
    return [];
  }

  return renderable.slice(0, 1);
}
