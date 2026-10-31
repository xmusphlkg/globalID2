import { describe, expect, it } from "vitest";

import { foldConsecutiveEntries, logFieldRows, windowTaskLogs } from "./task-log-display";
import type { WorkbookEntry } from "@/lib/hooks/useTasks";

function entry(id: number, title: string, content: string, entryType = "info", success = true): WorkbookEntry {
  return {
    id,
    entry_uuid: null,
    entry_type: entryType,
    title,
    content,
    content_type: "text",
    prompt: null,
    response: null,
    model_used: null,
    tokens_used: null,
    cost: null,
    duration: null,
    success,
    error_message: null,
    metadata: {},
    created_at: `2026-10-30T00:00:${String(id).padStart(2, "0")}Z`,
  };
}

describe("logFieldRows", () => {
  it("keeps short configuration blocks as fields", () => {
    expect(logFieldRows("Country: CN\nSource: cdc_weekly\nForce: No")).toEqual([
      { label: "Country", value: "CN" },
      { label: "Source", value: "cdc_weekly" },
      { label: "Force", value: "No" },
    ]);
  });

  it("leaves single-line errors, JSON, and command output as log text", () => {
    expect(logFieldRows("Error: AIWorkFailed: agent_workflow: 1 item failed, model: hy3")).toEqual([]);
    expect(logFieldRows('{\n  "overall_ready": true,\n  "blockers": []\n}')).toEqual([]);
    expect(logFieldRows("2026-10-30 23:50:11 | INFO | database ready\nWriting manifest…")).toEqual([]);
    expect(
      logFieldRows(
        "Generated profiles did not pass the publication gate.\nen: generator error: empty completion",
      ),
    ).toEqual([]);
  });
});

describe("windowTaskLogs", () => {
  it("pins errors and warnings ahead of the recent routine tail", () => {
    const entries = [
      entry(1, "Section Started", "Disease: Anthrax"),
      entry(2, "Task Failed", "Error: TimeoutError", "error", false),
      entry(3, "Section Started", "Disease: Zika"),
      entry(4, "Stale Task Recovered", "Requeued after the worker restarted", "warning"),
    ];
    const result = windowTaskLogs(entries, 1);
    expect(result.pinned.map((item) => item.id)).toEqual([2, 4]);
    expect(result.recent.map((item) => item.id)).toEqual([3]);
    expect(result.earlier.map((item) => item.id)).toEqual([1]);
  });
});

describe("foldConsecutiveEntries", () => {
  it("folds adjacent copies and keeps a different payload", () => {
    const entries = [
      entry(1, "Analysis Progress", "Running"),
      entry(2, "Analysis Progress", "Running"),
      entry(3, "Section Started", "Disease: Zika"),
    ];
    const groups = foldConsecutiveEntries(entries);
    expect(groups.map((group) => [group.entry.id, group.repeats])).toEqual([
      [2, 2],
      [3, 1],
    ]);
  });
});
