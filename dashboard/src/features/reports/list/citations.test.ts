import { describe, expect, it } from "vitest";
import type { ReportDetail } from "@/features/reports/api";
import { collectReportReferences, formatReferenceCitation, linkCitationMarkers, referenceUrl } from "./citations";

describe("report citations", () => {
  it("deduplicates legacy disease-card attribution and keeps explicit reference order", () => {
    const detail = { metadata: { references: [{ source_id: "b", citation_index: 2, title: "Second" }, { source_id: "a", citation_index: 1, title: "First", resolved_url: "https://pubmed.ncbi.nlm.nih.gov/123/" }], disease_cards: [{ knowledge_context: { source_attribution: [{ source_id: "a", title: "Duplicate" }] } }] } } as unknown as ReportDetail;
    const refs = collectReportReferences(detail, ["Text [1]"]);
    expect(refs).toMatchObject([{ source_id: "a" }, { source_id: "b" }]);
    expect(linkCitationMarkers("Text [1], [2], [3] and [1](https://example.com)", refs)).toBe("Text [[1]](#report-ref-1), [[2]](#report-ref-2), [3] and [1](https://example.com)");
    expect(referenceUrl(refs[0])).toBe("https://pubmed.ncbi.nlm.nih.gov/123/");
    expect(formatReferenceCitation(refs[0], 1)).toContain("PMID: 123.");
  });
  it("limits legacy-only references to cited markers and handles missing evidence", () => {
    const detail = { metadata: { disease_cards: [{ knowledge_context: { source_attribution: [{ title: "One" }, { title: "Two" }] } }] } } as unknown as ReportDetail;
    expect(collectReportReferences(detail, ["Only [2]"])).toMatchObject([{ title: "Two" }]);
    expect(collectReportReferences({ metadata: {} } as unknown as ReportDetail, [])).toEqual([]);
  });
});
