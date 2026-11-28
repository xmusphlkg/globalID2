import { describe, expect, it } from "vitest";
import {
  buildReportUuidMap,
  buildStatusChartData,
  buildTaskInteractionsHref,
  buildTypeChartData,
  canCancelTask,
  canRetryTask,
  computePaginationBounds,
  computeTaskSummary,
  parseReportId,
  parseReportUuid,
  resolveTaskReportUuid,
} from "./helpers";

describe("ai tasks helpers", () => {
  it("parses finite report IDs from numbers and numeric strings", () => {
    expect(parseReportId(42)).toBe(42);
    expect(parseReportId("108")).toBe(108);
    expect(parseReportId("not-a-number")).toBeNull();
    expect(parseReportId(Number.NaN)).toBeNull();
    expect(parseReportId(Number.POSITIVE_INFINITY)).toBeNull();
    expect(parseReportId(null)).toBeNull();
    expect(parseReportId(undefined)).toBeNull();
  });

  it("parses trimmed non-empty report UUIDs", () => {
    expect(parseReportUuid("  rep-123  ")).toBe("rep-123");
    expect(parseReportUuid("   ")).toBeNull();
    expect(parseReportUuid(123)).toBeNull();
    expect(parseReportUuid(null)).toBeNull();
  });

  it("resolves report UUID from output_data first, then task report_id or output_data report_id", () => {
    const reportMap = buildReportUuidMap([
      { id: 10, report_uuid: "uuid-10" },
      { id: 20, report_uuid: "uuid-20" },
    ]);

    expect(resolveTaskReportUuid(null, reportMap)).toBeNull();
    expect(
      resolveTaskReportUuid(
        { report_id: 10, output_data: { report_uuid: " direct-uuid " } },
        reportMap,
      ),
    ).toBe("direct-uuid");
    expect(
      resolveTaskReportUuid({ report_id: 10, output_data: null }, reportMap),
    ).toBe("uuid-10");
    expect(
      resolveTaskReportUuid({ report_id: null, output_data: { report_id: "20" } }, reportMap),
    ).toBe("uuid-20");
    expect(
      resolveTaskReportUuid({ report_id: 999, output_data: {} }, reportMap),
    ).toBeNull();
  });

  it("computes task summary counts and average progress", () => {
    expect(computeTaskSummary([], 0)).toEqual({
      total: 0,
      running: 0,
      failed: 0,
      avgProgress: 0,
    });

    expect(
      computeTaskSummary(
        [
          { status: "running", progress: 40 },
          { status: "failed", progress: 20 },
          { status: "completed", progress: 100 },
        ],
        15,
      ),
    ).toEqual({
      total: 15,
      running: 1,
      failed: 1,
      avgProgress: 53,
    });
  });

  it("aggregates and sorts status and task type chart buckets descending", () => {
    const tasks = [
      { status: "completed", task_type: "generate_report" },
      { status: "running", task_type: "agent_workflow" },
      { status: "completed", task_type: "agent_workflow" },
      { status: "completed", task_type: "agent_workflow" },
    ];

    expect(buildStatusChartData(tasks)).toEqual([
      { name: "completed", value: 3 },
      { name: "running", value: 1 },
    ]);
    expect(buildTypeChartData(tasks)).toEqual([
      { name: "agent_workflow", value: 3 },
      { name: "generate_report", value: 1 },
    ]);
  });

  it("computes pagination bounds cleanly for empty and multi-page datasets", () => {
    expect(computePaginationBounds(1, 0, 0, 100)).toEqual({
      offset: 0,
      totalPages: 1,
      visibleStart: 0,
      visibleEnd: 0,
    });
    expect(computePaginationBounds(2, 235, 100, 100)).toEqual({
      offset: 100,
      totalPages: 3,
      visibleStart: 101,
      visibleEnd: 200,
    });
  });

  it("evaluates retry/cancel eligibility and builds interaction links", () => {
    expect(canRetryTask("failed")).toBe(true);
    expect(canRetryTask("cancelled")).toBe(true);
    expect(canRetryTask("running")).toBe(false);

    expect(canCancelTask("pending")).toBe(true);
    expect(canCancelTask("queued")).toBe(true);
    expect(canCancelTask("running")).toBe(true);
    expect(canCancelTask("completed")).toBe(false);

    expect(buildTaskInteractionsHref("task/1")).toBe("/production/interactions?task=task%2F1");
    expect(buildTaskInteractionsHref("task-1", "rep-9")).toBe(
      "/production/interactions?task=task-1&uuid=rep-9",
    );
  });
});
