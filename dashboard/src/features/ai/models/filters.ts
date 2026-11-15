

import { type AIModelItem, type AIProviderItem, type AIRuntimeRoute } from "@/features/ai/api";

export type HealthFilter = "all" | "healthy" | "attention" | "checking";
export function matchesQuery(fields: Array<string | number | null | undefined>, query: string): boolean {
  if (!query) return true;
  return fields.some((field) => String(field ?? "").toLowerCase().includes(query));
}

export function providerMatchesFilter(provider: AIProviderItem, filter: HealthFilter): boolean {
  const status = (provider.last_check_status || "").toLowerCase();
  if (filter === "all") return true;
  if (filter === "healthy") return provider.is_active && provider.has_api_key && status === "available" && !provider.rate_limit_active && !provider.runtime_failure_active;
  if (filter === "attention") {
    return !provider.is_active || !provider.has_api_key || provider.rate_limit_active || provider.runtime_failure_active || ["unavailable", "failed", "rate_limited"].includes(status);
  }
  return status === "checking";
}

export function modelMatchesFilter(model: AIModelItem, filter: HealthFilter, route?: AIRuntimeRoute): boolean {
  const status = (model.last_check_status || "").toLowerCase();
  if (filter === "all") return true;
  if (filter === "healthy") return model.is_enabled && (route ? route.available_for_routing : status === "available" && !model.rate_limit_active && !model.runtime_failure_active);
  if (filter === "attention") {
    return !model.is_enabled || (route ? !route.available_for_routing : model.rate_limit_active || model.runtime_failure_active || ["unavailable", "failed", "rate_limited"].includes(status));
  }
  return status === "checking";
}

export function routeMatchesFilter(route: AIRuntimeRoute, filter: HealthFilter): boolean {
  if (filter === "all") return true;
  if (filter === "healthy") return route.available_for_routing;
  if (filter === "attention") return !route.available_for_routing || route.rate_limit_active || route.runtime_failure_active || !route.has_api_key;
  return (route.last_check_status || "").toLowerCase() === "checking";
}
