import { parseSituationAlertPayload, situationAlertContent, situationAlertJob, type SituationAlertPayload } from "./situation-alert.ts";

import type { Env } from "../types.ts";
import { officialNoticeContent, parseOfficialNoticeAlert, type OfficialNoticeAlert } from "./official-notice-alert.ts";
import { isRecord } from "./input.ts";

import { errorMessage, structuredLog } from "./situation-alert-observability.ts";
export type DispatchAlert = {
  stored: SituationAlertPayload | OfficialNoticeAlert;
  idempotencyKey: string; reportId: string; signalId: string;
  basis: string; policyVersion: string | null; publicUrl: string;
  countries: string[]; diseases: string[]; severity: string[];
  content(locale: string): { subject: string; markdown: string };
};

export function parseDispatchAlert(value: unknown): DispatchAlert {
  if (isRecord(value) && value.schema_version === "official-notice-alert.v1") {
    const notice = parseOfficialNoticeAlert(value);
    return {
      stored: notice, idempotencyKey: notice.idempotency_key,
      reportId: notice.release_id, signalId: notice.alert_id,
      basis: "official_authority_notice", policyVersion: null,
      publicUrl: notice.public_url, countries: notice.countries, diseases: notice.diseases,
      severity: [], content: (locale) => officialNoticeContent(notice, locale),
    };
  }
  const alert = parseSituationAlertPayload(value);
  return {
    stored: alert, idempotencyKey: alert.idempotency_key,
    reportId: alert.report.report_id, signalId: alert.signal.signal_id,
    basis: alert.signal.verification_basis, policyVersion: alert.signal.verification_policy_version,
    publicUrl: alert.report.public_url, countries: alert.signal.countries,
    diseases: alert.signal.diseases, severity: [alert.signal.anomaly_state],
    content: (locale) => situationAlertContent(alert, locale),
  };
}

export async function dispatchSituationAlert(
  env: Env,
  eventId: string,
  delaySeconds?: number,
): Promise<"cloudflare_queue" | "d1_outbox"> {
  if (!env.SITUATION_ALERT_QUEUE) return "d1_outbox";
  try {
    await env.SITUATION_ALERT_QUEUE.send(situationAlertJob(eventId), {
      contentType: "json",
      ...(delaySeconds ? { delaySeconds } : {}),
    });
    return "cloudflare_queue";
  } catch (error) {
    structuredLog("error", "situation_alert_queue_dispatch_failed", {
      event_id: eventId,
      error: errorMessage(error),
    });
    return "d1_outbox";
  }
}
