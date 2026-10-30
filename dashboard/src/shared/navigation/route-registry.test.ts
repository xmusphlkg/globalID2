import { describe, expect, it } from "vitest";
import { t } from "@/lib/i18n";
import { navigationSections, findRouteByPath } from "./route-registry";

describe("control-center navigation", () => {
  it("exposes six responsibility-based workspaces with unique canonical routes", () => {
    expect(navigationSections.map((section) => section.title)).toEqual([
      "Overview",
      "Ingestion & Tasks",
      "Data Governance",
      "AI",
      "Publish",
      "Settings",
    ]);
    const hrefs = navigationSections.flatMap((section) => section.items.map((item) => item.href));
    expect(new Set(hrefs).size).toBe(hrefs.length);
    expect(hrefs.some((href) => href.startsWith("/sources/"))).toBe(false);
    expect(hrefs.some((href) => href.startsWith("/ai/"))).toBe(false);
  });
});

it("keeps configuration and research tools discoverable under their business workspaces", () => {
  for (const href of ["/settings/integrations", "/settings/models", "/collection/sources/ingestion", "/collection/schedules", "/data/literature", "/models/queue", "/models/tasks", "/publish/tasks"]) {
    expect(findRouteByPath(href)?.href).toBe(href);
  }
  expect(findRouteByPath("/collection/runs/run-1")?.id).toBe("collection.runs");
  for (const section of navigationSections) {
    expect(t("zh", section.titleKey)).not.toBe(section.titleKey);
    for (const item of section.items) expect(t("zh", item.labelKey)).not.toBe(item.labelKey);
  }
});

it("limits collection navigation to four sources, tasks, schedules and runtime", () => {
  const section = navigationSections.find(item => item.id === "collection")!;
  expect(section.items.map(item => item.label)).toEqual(["Source - Situation","Source - Ingestion","Source - Literature","Source - Knowledge","Tasks","Schedules","Runtime"]);
  expect(section.items.some(item => item.href.startsWith("/data") || item.href.startsWith("/publish"))).toBe(false);
  expect(findRouteByPath("/data/quality")?.section).toBe("data");
  expect(findRouteByPath("/models/knowledge")?.section).toBe("models");
  expect(findRouteByPath("/publish/tasks")?.section).toBe("publish");
});
