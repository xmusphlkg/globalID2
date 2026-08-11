import { describe, expect, it } from "vitest";
import { navigationSections } from "./route-registry";

describe("control-center navigation", () => {
  it("exposes exactly five English workspaces with unique canonical routes", () => {
    expect(navigationSections.map((section) => section.title)).toEqual([
      "Overview",
      "Ingestion & Tasks",
      "Data Governance",
      "AI & Reports",
      "Settings",
    ]);
    const hrefs = navigationSections.flatMap((section) => section.items.map((item) => item.href));
    expect(new Set(hrefs).size).toBe(hrefs.length);
    expect(hrefs.some((href) => href.startsWith("/sources/"))).toBe(false);
    expect(hrefs.some((href) => href.startsWith("/ai/"))).toBe(false);
  });
});
