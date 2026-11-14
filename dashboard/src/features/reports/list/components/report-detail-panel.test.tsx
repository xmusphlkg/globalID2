import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReportDetail } from "@/features/reports/api";
import { ReportDetailPanel } from "./report-detail-panel";
vi.mock("@/features/reports/v4-preview", () => ({ ReportV4Preview: ({ detail }: { detail: ReportDetail }) => <div data-testid="v4-preview">{detail.report_uuid}</div> }));
vi.mock("@/features/reports/report-figures", () => ({ ReportFigureList: () => null }));
afterEach(cleanup);

const detail = { report_uuid: "report-one", sections: [], status: "completed", metadata: {}, report_type: "monthly", period_start: "2026-01-01T00:00:00Z", period_end: "2026-01-31T00:00:00Z", summary: "Legacy report summary", quality_score: null, generation_time: null } as unknown as ReportDetail;

describe("report layout compatibility", () => {
  it("renders legacy reports with UTC calendar dates and missing quality", () => {
    render(<ReportDetailPanel detail={detail} lang="en" loading={false} />);
    expect(screen.getByText("Legacy report summary")).toBeInTheDocument();
    expect(screen.getByText("2026-01-01 - 2026-01-31")).toBeInTheDocument();
    expect(screen.queryByTestId("v4-preview")).toBeNull();
    expect(screen.getAllByText("--").length).toBeGreaterThan(0);
  });
  it("routes V4 reports to the existing V4 preview", () => {
    render(<ReportDetailPanel detail={{ ...detail, metadata: { report_layout: "report_v4", report_document_v4: { version: 4 } } }} lang="en" loading={false} />);
    expect(screen.getByTestId("v4-preview")).toHaveTextContent("report-one");
    expect(screen.queryByText("Legacy report summary")).toBeNull();
  });
});
