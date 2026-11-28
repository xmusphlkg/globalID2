import { describe, expect, it } from "vitest";
import type { DiseaseAuditLogEntry } from "@/features/ai/api";
import {
  decisionLabel,
  decisionTone,
  diseaseAuditCopy,
  formatDiseaseRunError,
  formatMetadata,
  getActiveAuditRoutes,
  getVisibleAuditLogs,
  logTone,
  parseRunError,
  resolveAuditLogs,
  trimLongText,
} from "./helpers";

describe("disease-audit helpers", () => {
  it("maps decisions to expected badge tones and localized labels", () => {
    expect(decisionTone("merge")).toBe("success");
    expect(decisionTone("add_standard_disease")).toBe("info");
    expect(decisionTone("keep_separate")).toBe("neutral");
    expect(decisionTone("needs_human_review")).toBe("warning");
    expect(decisionTone(undefined)).toBe("warning");

    expect(decisionLabel("merge", "en")).toBe("Merge");
    expect(decisionLabel("merge", "zh")).toBe("建议合并");
    expect(decisionLabel("add_standard_disease", "en")).toBe("Add disease");
    expect(decisionLabel("keep_separate", "zh")).toBe("建议保留");
    expect(decisionLabel("unknown", "en")).toBe("Human review");
  });

  it("maps log levels to badge tones", () => {
    expect(logTone("error")).toBe("danger");
    expect(logTone("warning")).toBe("warning");
    expect(logTone("info")).toBe("neutral");
  });

  it("trims long strings and appends ellipsis only past max length", () => {
    expect(trimLongText("  hello  ", 10)).toBe("hello");
    expect(trimLongText("abcdef", 4)).toBe("abcd…");
  });

  it("parses errors and formats known HTTP/network error categories", () => {
    expect(parseRunError({ status: 500, message: "Boom" })).toEqual({
      status: 500,
      message: "Boom",
      lowerMessage: "boom",
    });

    const ui = diseaseAuditCopy.en;

    expect(formatDiseaseRunError({ status: 404, message: "Route not found" }, ui)).toEqual({
      message: ui.routeMissing,
      details: "Route not found",
    });

    expect(formatDiseaseRunError(new Error("Request timeout exceeded"), ui)).toEqual({
      message: ui.runErrorTimeout,
      details: "Request timeout exceeded",
    });

    const serverErr = formatDiseaseRunError({ status: 500, message: "Traceback" }, ui);
    expect(serverErr.message).toBe(ui.runError500);
    expect(serverErr.details).toContain(ui.runError500Hint);
    expect(serverErr.details).toContain("Traceback");

    expect(formatDiseaseRunError(new Error("Failed to fetch"), ui)).toEqual({
      message: ui.networkError,
      details: "Failed to fetch",
    });

    expect(formatDiseaseRunError("", ui)).toEqual({
      message: ui.runErrorUnknown,
      details: undefined,
    });

    expect(formatDiseaseRunError(new Error("Custom validation issue"), ui)).toEqual({
      message: "Custom validation issue",
      details: undefined,
    });
  });

  it("formats non-empty metadata and reverses/slices audit logs without mutating input", () => {
    expect(formatMetadata(undefined)).toBeNull();
    expect(formatMetadata({})).toBeNull();
    expect(formatMetadata({ count: 3 })).toBe('{\n  "count": 3\n}');

    const logs: DiseaseAuditLogEntry[] = [
      { run_id: "r1", timestamp: "2026-01-01T00:00:00Z", level: "info", event: "start", message: "first" },
      { run_id: "r1", timestamp: "2026-01-01T00:00:01Z", level: "warning", event: "retry", message: "second" },
      { run_id: "r1", timestamp: "2026-01-01T00:00:02Z", level: "error", event: "fail", message: "third" },
    ];

    const visible = getVisibleAuditLogs(logs, 2);
    expect(visible.map((entry) => entry.message)).toEqual(["third", "second"]);
    expect(logs.map((entry) => entry.message)).toEqual(["first", "second", "third"]);

    expect(resolveAuditLogs(logs, [])).toBe(logs);
    expect(resolveAuditLogs([], logs)).toBe(logs);
    expect(resolveAuditLogs(undefined, undefined)).toEqual([]);
  });

  it("filters active model routes available for routing", () => {
    const routes = [
      { model_key: "m1", available_for_routing: true },
      { model_key: "m2", available_for_routing: false },
      { model_key: "m3" },
    ];
    expect(getActiveAuditRoutes(routes)).toEqual([{ model_key: "m1", available_for_routing: true }]);
    expect(getActiveAuditRoutes(undefined)).toEqual([]);
  });
});
