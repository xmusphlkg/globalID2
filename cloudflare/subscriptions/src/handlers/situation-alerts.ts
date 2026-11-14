import { deliveryMode } from "../lib/pilot.ts";

import { HttpError, json } from "../lib/http.ts";

import { secureTextEqual, sha256Hex } from "../lib/security.ts";
import { isAllowedSituationPublicUrl, SituationAlertValidationError } from "../lib/situation-alert.ts";

import type { Env } from "../types.ts";

import type { Dependencies, AlertEventRow, AlertProcessResult } from "../lib/situation-alert-types.ts";
import { structuredLog } from "../lib/situation-alert-observability.ts";
import { parseDispatchAlert, dispatchSituationAlert, type DispatchAlert } from "../lib/situation-alert-dispatch.ts";
import { fanOutSituationAlert, alertCounts } from "../lib/situation-alert-repository.ts";
import { processSituationAlertOutbox } from "../lib/situation-alert-delivery.ts";
const MAX_PAYLOAD_BYTES = 64 * 1024;

export function createSituationAlertHandlers(deps: Dependencies) {
  async function ingest(request: Request, env: Env): Promise<Response> {
    await requireSituationAlertIngest(request, env);
    const raw = await readBoundedJson(request);
    let alert: DispatchAlert;
    try {
      alert = parseDispatchAlert(raw);
    } catch (error) {
      if (error instanceof SituationAlertValidationError) {
        throw new HttpError(422, error.message);
      }
      throw error;
    }
    if (alert.idempotencyKey.startsWith("pilot:") !== (deliveryMode(env) === "pilot")) {
      throw new HttpError(409, "alert_delivery_mode_mismatch");
    }
    if (
      alert.basis === "automated_policy"
      && env.SITUATION_ALERT_AUTOMATED_POLICY_ENABLED?.trim().toLowerCase() !== "true"
    ) {
      // Keep a second deployment-level kill switch after the report's
      // structured v3.2 policy decision has passed contract validation.
      throw new HttpError(422, "automated_policy_dispatch_disabled");
    }
    if (!isAllowedSituationPublicUrl(alert.publicUrl, env.SITUATION_PUBLIC_ORIGINS)) {
      throw new HttpError(422, "untrusted_public_report_origin");
    }

    const payloadJson = JSON.stringify(alert.stored);
    const payloadSha256 = await sha256Hex(payloadJson);
    const now = new Date().toISOString();
    const proposedEventId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO situation_alert_events (
         id, idempotency_key, report_id, signal_id, verification_basis,
         verification_policy_version, payload_sha256, payload_json, status, received_at, updated_at
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?)
       ON CONFLICT(idempotency_key) DO NOTHING`,
    ).bind(
      proposedEventId,
      alert.idempotencyKey,
      alert.reportId,
      alert.signalId,
      alert.basis,
      alert.policyVersion,
      payloadSha256,
      payloadJson,
      now,
      now,
    ).run();

    const event = await env.DB.prepare(
      `SELECT id, idempotency_key, report_id, signal_id, verification_basis,
       verification_policy_version, payload_sha256, payload_json,
       status, received_at, updated_at, completed_at, queued_count, sent_count,
       skipped_count, failed_count, last_error
       FROM situation_alert_events WHERE idempotency_key = ?`,
    ).bind(alert.idempotencyKey).first<AlertEventRow>();
    if (!event) throw new HttpError(500, "situation_alert_not_persisted");
    if (event.payload_sha256 !== payloadSha256) {
      throw new HttpError(409, "idempotency_key_payload_conflict");
    }

    const duplicate = event.id !== proposedEventId;
    // Replays must not fan out to subscribers who joined after the event.
    // A received event may be a crash between persistence and its first fanout.
    const queued = !duplicate || event.status === "received"
      ? await fanOutSituationAlert(env, event.id, alert, now, event.received_at) : 0;
    const dispatch = queued > 0 ? await dispatchSituationAlert(env, event.id) : "not_required";
    structuredLog("info", "situation_alert_accepted", {
      event_id: event.id,
      report_id: event.report_id,
      signal_id: event.signal_id,
      verification_basis: alert.basis,
      verification_policy_version: alert.policyVersion,
      duplicate,
      queued,
      dispatch,
    });
    return json({
      ok: true,
      event_id: event.id,
      idempotency_key: event.idempotency_key,
      duplicate,
      queued_deliveries: queued,
      dispatch,
    }, request, env, duplicate ? 200 : 202);
  }

  async function process(request: Request, env: Env): Promise<Response> {
    await requireSituationAlertAdmin(request, env);
    const result = await processSituationAlertOutbox(env, deps);
    return json(result, request, env);
  }

  async function list(request: Request, env: Env): Promise<Response> {
    await requireSituationAlertAdmin(request, env);
    const url = new URL(request.url);
    const rawLimit = Number(url.searchParams.get("limit") || 50);
    const rawOffset = Number(url.searchParams.get("offset") || 0);
    const limit = Math.min(Math.max(Number.isFinite(rawLimit) ? Math.trunc(rawLimit) : 50, 1), 250);
    const offset = Math.max(Number.isFinite(rawOffset) ? Math.trunc(rawOffset) : 0, 0);
    const total = await env.DB.prepare("SELECT COUNT(*) AS count FROM situation_alert_events")
      .first<{ count: number }>();
    const rows = await env.DB.prepare(
      `SELECT id, idempotency_key, report_id, signal_id, verification_basis,
       verification_policy_version, status, received_at, updated_at,
       completed_at, queued_count, sent_count, skipped_count, failed_count, last_error
       FROM situation_alert_events ORDER BY received_at DESC LIMIT ? OFFSET ?`,
    ).bind(limit, offset).all<Omit<AlertEventRow, "payload_sha256" | "payload_json">>();
    return json({
      ok: true,
      alerts: (rows.results || []).map((row) => ({
        event_id: row.id,
        idempotency_key: row.idempotency_key,
        report_id: row.report_id,
        signal_id: row.signal_id,
        verification_basis: row.verification_basis,
        verification_policy_version: row.verification_policy_version || null,
        status: row.status,
        received_at: row.received_at,
        updated_at: row.updated_at,
        completed_at: row.completed_at || null,
        counts: alertCounts(row),
        last_error: row.last_error || null,
      })),
      pagination: { total: Number(total?.count || 0), limit, offset },
    }, request, env);
  }

  return { ingest, process, list };
}

async function requireSituationAlertIngest(request: Request, env: Env): Promise<void> {
  const expected = env.SITUATION_ALERT_INGEST_TOKEN || "";
  if (!expected) throw new HttpError(500, "SITUATION_ALERT_INGEST_TOKEN_not_configured");
  const authorization = request.headers.get("authorization") || "";
  const provided = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!(await secureTextEqual(provided, expected))) throw new HttpError(401, "unauthorized");
}

async function requireSituationAlertAdmin(request: Request, env: Env): Promise<void> {
  const expected = env.ADMIN_API_TOKEN || "";
  if (!expected) throw new HttpError(500, "ADMIN_API_TOKEN_not_configured");
  const authorization = request.headers.get("authorization") || "";
  const provided = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!(await secureTextEqual(provided, expected))) throw new HttpError(401, "unauthorized");
}

async function readBoundedJson(request: Request): Promise<unknown> {
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new HttpError(415, "application_json_required");
  }
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_PAYLOAD_BYTES) {
    throw new HttpError(413, "situation_alert_payload_too_large");
  }
  if (!request.body) throw new HttpError(400, "situation_alert_payload_required");

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_PAYLOAD_BYTES) {
      await reader.cancel("payload_too_large");
      throw new HttpError(413, "situation_alert_payload_too_large");
    }
    chunks.push(value);
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(body)) as unknown;
  } catch {
    throw new HttpError(400, "invalid_json");
  }
}

export { consumeSituationAlertBatch } from "../lib/situation-alert-queue.ts";
export { processSituationAlertOutbox } from "../lib/situation-alert-delivery.ts";
export { maintainSituationAlerts } from "../lib/situation-alert-maintenance.ts";
export { skipPendingSituationAlertsForSubscription } from "../lib/situation-alert-repository.ts";
export type { AlertProcessResult } from "../lib/situation-alert-types.ts";
