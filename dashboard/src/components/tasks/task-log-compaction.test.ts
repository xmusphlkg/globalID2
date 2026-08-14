import { describe, expect, it } from "vitest";

import type { WorkbookEntry } from "@/lib/hooks/useTasks";
import { compactTaskLogEntries } from "./task-log-compaction";

function entry(id: number, title: string): WorkbookEntry {
  return {
    id,
    entry_uuid: null,
    entry_type: "info",
    title,
    content: title,
    content_type: "text",
    prompt: null,
    response: null,
    model_used: null,
    tokens_used: null,
    cost: null,
    duration: null,
    success: true,
    error_message: null,
    metadata: {},
    created_at: `2026-08-13T00:00:0${id}Z`,
  };
}

describe("compactTaskLogEntries", () => {
  it("folds all repeated chunks after a command completes", () => {
    const result = compactTaskLogEntries([
      entry(1, "Build Astro Site Started"),
      entry(2, "Build Astro Site Output #1"),
      entry(3, "Build Astro Site Output #2"),
      entry(4, "Build Astro Site Completed"),
    ]);

    expect(result.entries.map((item) => item.title)).toEqual([
      "Build Astro Site Started",
      "Build Astro Site Completed",
    ]);
    expect(result.hiddenCount).toBe(2);
  });

  it("keeps only the newest chunk while a command is running", () => {
    const result = compactTaskLogEntries([
      entry(1, "Publish Downloads Started"),
      entry(2, "Publish Downloads Output #1"),
      entry(3, "Publish Downloads Output #2"),
      entry(4, "Publish Downloads Output #3"),
    ]);

    expect(result.entries.map((item) => item.title)).toEqual([
      "Publish Downloads Started",
      "Publish Downloads Output #3",
    ]);
    expect(result.hiddenCount).toBe(2);
  });

  it("leaves ordinary workbook entries unchanged", () => {
    const entries = [entry(1, "Release Preflight"), entry(2, "Phase 1/3")];

    expect(compactTaskLogEntries(entries)).toEqual({ entries, hiddenCount: 0 });
  });
});
