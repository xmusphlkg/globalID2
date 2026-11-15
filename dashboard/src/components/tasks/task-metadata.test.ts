import { describe, expect, it } from "vitest";
import type { TaskDetail } from "@/lib/hooks/useTasks";
import { deriveRawArchivePath, extractStructuredRows, latestStageSnapshots, metadataString, qualityOverall, tokenTotal } from "./task-metadata";

const task = (input_data: Record<string, unknown>, country_code: string | null = "CA-ON") => ({ input_data, country_code }) as TaskDetail;

describe("task metadata presentation", () => {
  it("shows an archive path only when raw storage was explicitly requested", () => {
    expect(deriveRawArchivePath(task({ save_raw: true }))).toBe("data/raw/ca-on");
    expect(deriveRawArchivePath(task({ save_raw: true, country_code: "US" }))).toBe("data/raw/us");
    expect(deriveRawArchivePath(task({ save_raw: false, country_code: "US" }))).toBeNull();
    expect(deriveRawArchivePath(task({ save_raw: true }, null))).toBeNull();
  });

  it("keeps structured values and zero metrics without coercing arbitrary objects", () => {
    expect(extractStructuredRows("Source: archive, URL: https://example.test/data")).toEqual([
      { label: "Source", value: "archive" }, { label: "URL", value: "https://example.test/data" },
    ]);
    expect(extractStructuredRows("Error: connection timed out")).toEqual([]);
    expect(metadataString(false)).toBe("no");
    expect(metadataString({ secret: "hidden" })).toBeNull();
    expect(tokenTotal({ total: 0 })).toBe(0);
    expect(qualityOverall({ overall: 0, score: 0.9 })).toBe("0.00");
  });

  it("shows only the latest retry with workflow stages in execution order", () => {
    const stages = [
      { stage: "store", attempt: 1, status: "failed" },
      { stage: "store", attempt: 2, status: "completed" },
      { stage: "organize", attempt: 2, status: "completed" },
      { stage: "collect", attempt: 2, status: "completed" },
    ];
    expect(latestStageSnapshots(stages).map(row => [row.stage, row.attempt])).toEqual([
      ["collect", 2], ["organize", 2], ["store", 2],
    ]);
    expect(stages[1].stage).toBe("store");
  });
});
