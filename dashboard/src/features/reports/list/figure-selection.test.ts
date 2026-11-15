import { describe, expect, it } from "vitest";
import type { ReportSection } from "@/features/reports/api";
import { assignFigureNumbers, sectionFigures } from "./figure-selection";

describe("report evidence figure selection", () => {
  it("prefers section charts, preserves placement numbering and leaves inputs unchanged", () => {
    const section = { section_type: "trend_anomaly_analysis", charts: [{ figure_type: "epidemic_curve", position: "after_content" }, { figure_type: "anomaly_marker_curve", position: "before_content" }, { figure_type: "recent_window_heatmap" }] } as unknown as ReportSection;
    const original = structuredClone(section);
    const [selection] = assignFigureNumbers([section], [{ figure_type: "seasonal_baseline_band", section_type: "trend_anomaly_analysis" }]);
    expect(selection.beforeFigures).toMatchObject([{ figure_type: "anomaly_marker_curve", display_number: 1 }]);
    expect(selection.afterFigures).toMatchObject([{ figure_type: "epidemic_curve", display_number: 2 }]);
    expect(section).toEqual(original);
  });
  it("omits absent/unsupported evidence and avoids quality charts for complete data", () => {
    expect(sectionFigures({ section_type: "priority_signals", charts: [] } as unknown as ReportSection, [])).toEqual([]);
    const section = { section_type: "data_quality_limitations", metadata: { data_quality: { score: 1 } }, charts: [{ figure_type: "quality_summary", renderer: "echarts" }] } as unknown as ReportSection;
    expect(sectionFigures(section, [])).toEqual([]);
    expect(sectionFigures({ ...section, metadata: { data_quality: { score: 0 } } }, [])).toHaveLength(1);
  });
});
