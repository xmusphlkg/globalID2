import { describe, expect, it, vi } from "vitest";
import LegacyTaskPage from "@/app/operations/tasks/page";
import RunsPage from "@/app/collection/runs/page";
import { permanentRedirect } from "next/navigation";
vi.mock("next/navigation", () => ({ permanentRedirect: vi.fn((href: string) => { throw new Error(href); }) }));
describe("legacy bookmarks", () => {
  it("preserves task and kind through the old task URL and opens run details", async () => {
    const searchParams = Promise.resolve({ task: "run-1", kind: "ingestion" });
    await expect(LegacyTaskPage({ searchParams })).rejects.toThrow("/collection/runs?task=run-1&kind=ingestion");
    await expect(RunsPage({ searchParams })).rejects.toThrow("/collection/runs/run-1?task=run-1&kind=ingestion");
    expect(permanentRedirect).toHaveBeenCalledTimes(2);
  });
});

it("retains repeated, empty and encoded bookmark values without double decoding", async () => {
  const values = { task: ["run/with space", "second"], kind: ["ingestion", "a&b"], query: "中文 + %2F", empty: "" };
  try { await LegacyTaskPage({ searchParams: Promise.resolve(values) }); } catch (error) {
    const url = new URL((error as Error).message, "http://localhost");
    expect(url.pathname).toBe("/collection/runs");
    expect(url.searchParams.getAll("task")).toEqual(values.task);
    expect(url.searchParams.getAll("kind")).toEqual(values.kind);
    expect(url.searchParams.get("query")).toBe(values.query);
    expect(url.searchParams.has("empty")).toBe(true);
  }
});
