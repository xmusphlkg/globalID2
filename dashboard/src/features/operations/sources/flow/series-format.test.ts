import { describe, expect, it } from "vitest";

import { formatSeriesMetadata } from "./series-format";

describe("formatSeriesMetadata", () => {
  it("humanizes ontology metadata values", () => {
    expect(formatSeriesMetadata("not_comparable")).toBe("not comparable");
  });

  it("handles metadata omitted by partially enriched series such as JP", () => {
    expect(formatSeriesMetadata(undefined)).toBe("Not specified");
    expect(formatSeriesMetadata(null)).toBe("Not specified");
    expect(formatSeriesMetadata("  ")).toBe("Not specified");
  });
});
