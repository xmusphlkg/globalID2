import type { components } from "@/generated/api";

export type RealtimeChannel = components["schemas"]["RealtimeChannelOverviewOut"]["channel"];
export type PendingEligibility = "all" | "publishable" | "blocked";

// DTOs come from the backend contract; credentials and raw errors are excluded
// by that public projection rather than mirrored in a separate frontend schema.
export type BacklogSummary = components["schemas"]["RealtimeBacklogOut"];
export type RealtimeRun = components["schemas"]["RealtimeRunOut"];
export type RealtimeChannelOverview = components["schemas"]["RealtimeChannelOverviewOut"];
export type RealtimeOverview = components["schemas"]["RealtimeOverviewOut"];
export type RealtimePending = components["schemas"]["RealtimePendingOut"];
export type RealtimeActionResult = components["schemas"]["RealtimeActionOut"];
export type RealtimeControlResult = components["schemas"]["RealtimeControlOut"];
export type RealtimePage<T> = Omit<components["schemas"]["RealtimePendingPageOut"], "items"> & { items: T[] };
