import { ApiError } from "@/lib/api";
import { realtimePublishMessages, type RealtimeMessageKey } from "./messages";

export type RealtimeLanguage = "en" | "zh";
export type RealtimeText = (key: RealtimeMessageKey) => string;

export function realtimeText(lang: RealtimeLanguage): RealtimeText {
  return (key) => realtimePublishMessages[lang][key];
}

export function realtimeDate(value: string | null | undefined, lang: RealtimeLanguage) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString(lang === "zh" ? "zh-CN" : "en-US");
}

export function realtimeAge(value: number | null | undefined, text: RealtimeText) {
  if (value == null || !Number.isFinite(value)) return "—";
  const seconds = Math.max(0, value);
  const [count, unit] = seconds >= 86400 ? [seconds / 86400, "days"] as const
    : seconds >= 3600 ? [seconds / 3600, "hours"] as const
      : seconds >= 60 ? [seconds / 60, "minutes"] as const
        : [seconds, "seconds"] as const;
  return `${Math.floor(count)} ${text(unit)}`;
}

export function realtimeDuration(value: number | null | undefined, text: RealtimeText) {
  return value == null || !Number.isFinite(value) ? "—" : `${Math.max(0, value).toFixed(1)} ${text("seconds")}`;
}

export function realtimeStatus(value: string | null | undefined, text: RealtimeText) {
  const statuses = ["pending", "queued", "running", "retrying", "completed", "failed", "cancelled", "skipped"] as const;
  return statuses.includes(value as typeof statuses[number]) ? text(value as typeof statuses[number]) : text("unknown");
}

export function realtimeProblem(error: unknown, text: RealtimeText) {
  if (error instanceof ApiError) {
    if (error.code === "no_enabled_release_job") return text("no_enabled_job");
    if (error.code === "realtime_channel_disabled") return text("disabled_hint");
    if (error.status === 401 || error.status === 403) return text("request_denied");
    if (error.status === 409) return text("request_conflict");
    if (error.status === 408 || error.status === 504) return text("request_timeout");
  }
  // Error.message may contain credentials, paths, upstream responses, or tracebacks.
  return text("request_failed");
}

export function realtimeRetryStatus(value: string | null | undefined, text: RealtimeText) {
  const labels: Record<string, RealtimeMessageKey> = { none: "none", not_scheduled: "retry_not_scheduled", scheduled: "retry_scheduled", queued: "queued", terminal: "retry_finished", succeeded: "retry_succeeded" };
  return text(labels[value ?? ""] ?? "unknown");
}

export function realtimeContinuationStatus(value: string | null | undefined, text: RealtimeText) {
  const labels: Record<string, RealtimeMessageKey> = { none: "none", pending: "pending", queued: "queued", exhausted: "followup_exhausted" };
  return text(labels[value ?? ""] ?? "unknown");
}

export function realtimeBlockedReason(value: string | null | undefined, text: RealtimeText) {
  const labels: Record<string, RealtimeMessageKey> = { inactive_country: "blocked_inactive", disabled_country: "blocked_disabled", unknown_country: "blocked_unknown", invalid_scope: "blocked_invalid" };
  return text(labels[value ?? ""] ?? "blocked_hint");
}

export function realtimeIssue(code: string, text: RealtimeText) {
  const labels: Record<string, RealtimeMessageKey> = {
    pending_changes_delayed: "issue_pending",
    pending_scopes_blocked: "issue_blocked",
    running_task_delayed: "issue_running",
    queued_task_delayed: "issue_queued",
    retry_schedule_missing: "issue_retry",
    retry_overdue: "issue_retry",
    public_completed_version_mismatch: "issue_version",
    completed_release_receipt_missing: "issue_receipt",
    latest_release_unsuccessful: "issue_failed",
    database_unavailable: "issue_database",
    automatic_publication_paused: "issue_paused",
    control_state_invalid: "issue_control",
    control_state_unavailable: "issue_control",
    public_pointer_unavailable: "issue_public",
    http_status: "issue_public",
    transient_http_recovered: "issue_transient_http",
    object_checksum_mismatch: "issue_public",
    manifest_invalid: "issue_public",
  };
  return text(labels[code] ?? "issue_generic");
}

export function realtimeTaskHref(taskUuid: string) {
  return `/publish/tasks?detail=${encodeURIComponent(taskUuid)}`;
}
