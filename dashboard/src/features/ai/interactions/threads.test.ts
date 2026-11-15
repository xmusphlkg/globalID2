import { expect, it } from "vitest";
import type { AIInteractionItem } from "@/features/ai/api";
import { groupInteractionThreads } from "./threads";

const message = (id: number, run_id: number, timestamp: string, total_tokens: number) => ({
  id, run_id, timestamp, total_tokens, task_uuid: `task-${run_id}`, report_uuid: `report-${run_id}`, model: "writer", provider: "provider",
}) as AIInteractionItem;

it("groups by run without mutating message order and totals zero-valued messages", () => {
  const input = [message(3, 1, "2026-11-14T03:00:00Z", 30), message(2, 2, "2026-11-14T02:00:00Z", 20), message(1, 1, "2026-11-14T01:00:00Z", 0)];
  const threads = groupInteractionThreads(input);
  expect(threads.map(thread => thread.runId)).toEqual([1, 2]);
  expect(threads[0].messages.map(row => row.id)).toEqual([1, 3]);
  expect(threads[0].totalTokens).toBe(30);
  expect(threads[0].runModel).toBe("writer");
  expect(threads[0].runProvider).toBe("provider");
  expect(input.map(row => row.id)).toEqual([3, 2, 1]);
  expect(groupInteractionThreads(undefined)).toEqual([]);
});
