import { describe, expect, it } from "vitest";
import type { OntologySeries } from "@/features/operations/sources/api";
import { buildSeriesMappingRisks } from "./mapping-risks";

const row = (overrides: Partial<OntologySeries> = {}) => ({ concept_id: "flu", source_id: "weekly", frequency: "weekly", measure: "cases", reporting_basis: "notification", local_labels: ["Influenza"], ...overrides }) as OntologySeries;

describe("series mapping risks", () => {
  it("ignores case-only aliases and keeps frequency, source, measure and reporting basis separate", () => {
    expect(buildSeriesMappingRisks([row(), row({ local_labels: [" influenza "] }), row({ frequency: "monthly", local_labels: ["ILI"] }), row({ source_id: "hospital", local_labels: ["ILI"] }), row({ measure: "deaths", local_labels: ["ILI"] }), row({ reporting_basis: "admission", local_labels: ["ILI"] })])).toEqual([]);
  });
  it("reports distinct labels only within the same series scope without mutating input", () => {
    const rows = [row(), row({ local_labels: ["ILI", ""] })];
    const original = structuredClone(rows);
    expect(buildSeriesMappingRisks(rows)).toMatchObject([{ conceptId: "flu", sourceId: "weekly", labels: ["Influenza", "ILI"], rows }]);
    expect(rows).toEqual(original);
  });
});
