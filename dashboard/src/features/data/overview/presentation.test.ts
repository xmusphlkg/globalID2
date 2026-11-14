import { describe, expect, it } from "vitest";
import { dateRangeLabel, formatMetricValue, metricNumberFromChartValue } from "./presentation";

describe("overview metric display", () => {
  it("shows missing values as missing while retaining explicit zero in tooltips", () => {
    for (const missing of [null, undefined, "", "  ", ["2026-01-01", null]]) {
      expect(metricNumberFromChartValue(missing)).toBeNull();
      expect(formatMetricValue(metricNumberFromChartValue(missing), "cases")).toBe("-");
    }
    expect(formatMetricValue(metricNumberFromChartValue(["2026-01-01", 0]), "cases")).toBe("0");
    expect(formatMetricValue(metricNumberFromChartValue("0"), "incidence_rate")).toBe("0.00");
  });
  it("preserves date endpoints and open ranges", () => {
    expect(dateRangeLabel("2026-01-01", "", undefined, "2026-11-14")).toBe("2026-01-01 -> 2026-11-14");
    expect(dateRangeLabel("", "2026-11-14")).toBe("Until 2026-11-14");
    expect(dateRangeLabel("", "", undefined, undefined, "zh")).toBe("全部时间");
  });
});
