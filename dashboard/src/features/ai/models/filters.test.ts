import { describe, expect, it } from "vitest";
import type { AIModelItem, AIProviderItem, AIRuntimeRoute } from "@/features/ai/api";
import { modelMatchesFilter, providerMatchesFilter, routeMatchesFilter } from "./filters";
import { routingStatusReason } from "./presentation";

describe("model center admission and health", () => {
  it("uses actual route availability over the last successful model probe", () => {
    const model = { is_enabled: true, last_check_status: "available" } as AIModelItem;
    const route = { available_for_routing: false, routing_status: "outside_window", routing_status_reason: "Outside model window" } as AIRuntimeRoute;
    expect(modelMatchesFilter(model, "healthy", route)).toBe(false);
    expect(modelMatchesFilter(model, "attention", route)).toBe(true);
    expect(routeMatchesFilter(route, "attention")).toBe(true);
    expect(routingStatusReason(route, false)).toBe("Outside model window");
    expect(routingStatusReason(route, true)).toContain("时段");
  });
  it("keeps disabled or uncredentialed providers out of healthy results", () => {
    const provider = { is_active: false, has_api_key: true, last_check_status: "available" } as AIProviderItem;
    expect(providerMatchesFilter(provider, "healthy")).toBe(false);
    expect(providerMatchesFilter(provider, "attention")).toBe(true);
    expect(providerMatchesFilter({ ...provider, is_active: true, has_api_key: false }, "healthy")).toBe(false);
  });
});
