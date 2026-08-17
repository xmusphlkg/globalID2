import { isRecord, normalizeCode, normalizeLocale } from "./input.ts";
import type { JsonValue } from "./http.ts";

export const SITUATION_ALERT_SCHEMA_VERSION = "situation-alert.v1";
export const SITUATION_ALERT_JOB_SCHEMA_VERSION = "situation-alert-job.v1";

const QUALITY_GATE_STATUSES = new Set(["passed", "degraded"]);
const ANOMALY_STATES = new Set(["alert", "strong"]);
const TEMPORAL_RELEVANCE = new Set(["current", "lagged"]);
const SIGNAL_TYPES = new Set(["statistical_signal", "officially_correlated_signal"]);
const DATA_STATUSES = new Set(["current", "held_back"]);
const DETECTOR_TIERS = new Set(["common_count", "rare_count", "rate", "context_only"]);
const GUARDED_AUTO_POLICY = "guarded_auto_v1";
const GUARDED_AUTO_MODEL = "robust_quasi_poisson_v1";
const GUARDED_AUTO_TIERS = new Set(["common_count", "rare_count"]);
const IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const OPAQUE_REVIEWER_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$/;

export interface SituationAlertPayload extends Record<string, JsonValue> {
  schema_version: typeof SITUATION_ALERT_SCHEMA_VERSION;
  idempotency_key: string;
  report: {
    report_id: string;
    as_of: string;
    quality_gate_status: "passed" | "degraded";
    publication_status: "published";
    public_url: string;
  };
  signal: {
    signal_id: string;
    analysis_status: "analyzed";
    anomaly_state: "alert" | "strong";
    signal_type: "statistical_signal" | "officially_correlated_signal";
    temporal_relevance: "current" | "lagged";
    data_status: "current" | "held_back";
    completeness: number;
    q_value: number | null;
    model: string;
    fit_status: string;
    detector_tier: "common_count" | "rare_count" | "rate" | "context_only";
    effect_threshold_passed: boolean;
    verification_status: "verified";
    verification_basis: "automated_policy" | "analyst_review";
    verification_policy_version: "guarded_auto_v1" | null;
    verified_by: string;
    verified_at: string;
    observed_at: string;
    title: string;
    summary: string;
    countries: string[];
    diseases: string[];
    evidence_urls: string[];
  };
}

export interface SituationAlertJob extends Record<string, JsonValue> {
  schema_version: typeof SITUATION_ALERT_JOB_SCHEMA_VERSION;
  event_id: string;
}

export class SituationAlertValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SituationAlertValidationError";
  }
}

export function parseSituationAlertPayload(value: unknown): SituationAlertPayload {
  if (!isRecord(value)) fail("situation_alert_object_required");
  if (value.schema_version !== SITUATION_ALERT_SCHEMA_VERSION) {
    fail("unsupported_situation_alert_schema");
  }

  const idempotencyKey = requiredText(value.idempotency_key, "idempotency_key_required", 160);
  if (!IDEMPOTENCY_KEY.test(idempotencyKey)) fail("invalid_idempotency_key");

  if (!isRecord(value.report)) fail("situation_alert_report_required");
  const reportId = requiredText(value.report.report_id, "report_id_required", 160);
  const asOf = requiredIsoDate(value.report.as_of, "invalid_report_as_of");
  const qualityGateStatus = requiredText(
    value.report.quality_gate_status,
    "quality_gate_status_required",
    20,
  );
  if (!QUALITY_GATE_STATUSES.has(qualityGateStatus)) fail("publishable_quality_gate_required");
  if (value.report.publication_status !== "published") fail("published_report_required");
  const publicUrl = requiredHttpsUrl(value.report.public_url, "valid_public_report_url_required");

  if (!isRecord(value.signal)) fail("situation_alert_signal_required");
  const signalId = requiredText(value.signal.signal_id, "signal_id_required", 200);
  if (value.signal.analysis_status !== "analyzed") fail("analyzed_signal_required");
  const anomalyState = requiredText(value.signal.anomaly_state, "anomaly_state_required", 20);
  if (!ANOMALY_STATES.has(anomalyState)) fail("alert_or_strong_signal_required");
  const signalType = requiredText(value.signal.signal_type, "signal_type_required", 60);
  if (!SIGNAL_TYPES.has(signalType)) fail("invalid_signal_type");
  const temporalRelevance = requiredText(
    value.signal.temporal_relevance,
    "temporal_relevance_required",
    20,
  );
  if (!TEMPORAL_RELEVANCE.has(temporalRelevance)) fail("current_or_lagged_signal_required");
  const dataStatus = requiredText(value.signal.data_status, "data_status_required", 20);
  if (!DATA_STATUSES.has(dataStatus)) fail("publishable_data_status_required");
  const completeness = requiredUnitInterval(value.signal.completeness, "invalid_completeness");
  const qValue = optionalUnitInterval(value.signal.q_value, "invalid_q_value");
  const model = requiredText(value.signal.model, "signal_model_required", 120);
  const fitStatus = requiredText(value.signal.fit_status, "fit_status_required", 80);
  const detectorTier = requiredText(value.signal.detector_tier, "detector_tier_required", 40);
  if (!DETECTOR_TIERS.has(detectorTier)) fail("invalid_detector_tier");
  if (typeof value.signal.effect_threshold_passed !== "boolean") {
    fail("effect_threshold_status_required");
  }
  const effectThresholdPassed = value.signal.effect_threshold_passed;
  if (value.signal.verification_status !== "verified") fail("verified_signal_required");
  const verificationBasis = requiredText(
    value.signal.verification_basis,
    "verification_basis_required",
    40,
  );
  if (verificationBasis !== "automated_policy" && verificationBasis !== "analyst_review") {
    fail("invalid_verification_basis");
  }
  const rawPolicyVersion = value.signal.verification_policy_version;
  if (rawPolicyVersion !== undefined && rawPolicyVersion !== null && typeof rawPolicyVersion !== "string") {
    fail("invalid_verification_policy_version");
  }
  const verificationPolicyVersion = typeof rawPolicyVersion === "string"
    ? requiredText(rawPolicyVersion, "verification_policy_version_required", 80)
    : null;
  const verifiedBy = requiredText(value.signal.verified_by, "verified_by_required", 120);
  if (!OPAQUE_REVIEWER_ID.test(verifiedBy) || verifiedBy.includes("@")) {
    fail("opaque_verified_by_required");
  }
  const verifiedAt = requiredIsoDate(value.signal.verified_at, "invalid_verified_at");
  const observedAt = requiredIsoDate(value.signal.observed_at, "invalid_observed_at");
  if (Date.parse(verifiedAt) > Date.parse(asOf) + 5 * 60 * 1000) {
    fail("verification_after_report_as_of");
  }

  const title = requiredText(value.signal.title, "signal_title_required", 200);
  const summary = requiredText(value.signal.summary, "signal_summary_required", 1200);
  const countries = normalizedStringArray(value.signal.countries, "country", 50);
  const diseases = normalizedStringArray(value.signal.diseases, "disease", 50);
  const evidenceUrls = normalizedHttpsUrls(value.signal.evidence_urls, 20);
  if (evidenceUrls.length === 0) fail("signal_evidence_url_required");

  if (verificationBasis === "automated_policy") {
    if (verificationPolicyVersion !== GUARDED_AUTO_POLICY) {
      fail("guarded_auto_policy_required");
    }
    if (verifiedBy !== `policy:${GUARDED_AUTO_POLICY}`) {
      fail("guarded_auto_verifier_required");
    }
    if (temporalRelevance !== "current" || dataStatus !== "current") {
      fail("guarded_auto_current_signal_required");
    }
    if (qValue === null || qValue > 0.01) fail("guarded_auto_q_threshold_required");
    if (model !== GUARDED_AUTO_MODEL || fitStatus !== "completed") {
      fail("guarded_auto_primary_fit_required");
    }
    if (!GUARDED_AUTO_TIERS.has(detectorTier)) fail("guarded_auto_detector_tier_required");
    if (!effectThresholdPassed) fail("guarded_auto_effect_threshold_required");
    if (completeness < 0.95) fail("guarded_auto_completeness_required");
  } else if (verificationPolicyVersion !== null) {
    fail("analyst_review_policy_must_be_null");
  } else if (verifiedBy.startsWith("policy:")) {
    fail("analyst_reviewer_required");
  }

  return {
    schema_version: SITUATION_ALERT_SCHEMA_VERSION,
    idempotency_key: idempotencyKey,
    report: {
      report_id: reportId,
      as_of: asOf,
      quality_gate_status: qualityGateStatus as "passed" | "degraded",
      publication_status: "published",
      public_url: publicUrl,
    },
    signal: {
      signal_id: signalId,
      analysis_status: "analyzed",
      anomaly_state: anomalyState as "alert" | "strong",
      signal_type: signalType as "statistical_signal" | "officially_correlated_signal",
      temporal_relevance: temporalRelevance as "current" | "lagged",
      data_status: dataStatus as "current" | "held_back",
      completeness,
      q_value: qValue,
      model,
      fit_status: fitStatus,
      detector_tier: detectorTier as "common_count" | "rare_count" | "rate" | "context_only",
      effect_threshold_passed: effectThresholdPassed,
      verification_status: "verified",
      verification_basis: verificationBasis as "automated_policy" | "analyst_review",
      verification_policy_version: verificationPolicyVersion as "guarded_auto_v1" | null,
      verified_by: verifiedBy,
      verified_at: verifiedAt,
      observed_at: observedAt,
      title,
      summary,
      countries,
      diseases,
      evidence_urls: evidenceUrls,
    },
  };
}

export function parseSituationAlertJob(value: unknown): SituationAlertJob | null {
  if (!isRecord(value)) return null;
  if (value.schema_version !== SITUATION_ALERT_JOB_SCHEMA_VERSION) return null;
  const eventId = typeof value.event_id === "string" ? value.event_id.trim() : "";
  if (!eventId || eventId.length > 160) return null;
  return {
    schema_version: SITUATION_ALERT_JOB_SCHEMA_VERSION,
    event_id: eventId,
  };
}

export function situationAlertJob(eventId: string): SituationAlertJob {
  return {
    schema_version: SITUATION_ALERT_JOB_SCHEMA_VERSION,
    event_id: eventId,
  };
}

export function isAllowedSituationPublicUrl(value: string, configuredOrigins?: string): boolean {
  let origin = "";
  try {
    origin = new URL(value).origin;
  } catch {
    return false;
  }
  const allowed = (configuredOrigins || "https://globalinfectiousdisease.com")
    .split(",")
    .map((item) => item.trim().replace(/\/+$/, ""))
    .filter(Boolean);
  return allowed.includes(origin);
}

export function situationAlertContent(
  alert: SituationAlertPayload,
  locale: string,
): { subject: string; markdown: string } {
  const language = normalizeLocale(locale, "en") === "zh" ? "zh" : "en";
  const locations = alert.signal.countries.join(", ");
  const diseases = alert.signal.diseases.join(", ");
  if (language === "zh") {
    const verification = alert.signal.verification_basis === "automated_policy"
      ? `受控自动策略（${alert.signal.verification_policy_version}）`
      : "人工分析员复核（无自动策略）";
    const lines = [
      alert.signal.summary,
      "",
      `- 信号强度：${alert.signal.anomaly_state}`,
      `- 时效：${alert.signal.temporal_relevance}`,
      `- 观测时间：${alert.signal.observed_at}`,
      locations ? `- 涉及地区：${locations}` : "",
      diseases ? `- 涉及疾病：${diseases}` : "",
      `- 验证依据：${verification}`,
      `- 模型/拟合：${alert.signal.model} / ${alert.signal.fit_status}`,
      "",
      `[查看已发布的 Situation 报告](${alert.report.public_url})`,
      "",
      "该提醒表示监测数据中的已核验统计信号，不等同于公共卫生风险等级。",
    ].filter((line) => line !== "");
    return { subject: `GIDS 已核验监测提醒：${alert.signal.title}`, markdown: lines.join("\n") };
  }

  const verification = alert.signal.verification_basis === "automated_policy"
    ? `guarded automated policy (${alert.signal.verification_policy_version})`
    : "analyst review (no automated policy)";
  const lines = [
    alert.signal.summary,
    "",
    `- Signal strength: ${alert.signal.anomaly_state}`,
    `- Timeliness: ${alert.signal.temporal_relevance}`,
    `- Observed at: ${alert.signal.observed_at}`,
    locations ? `- Locations: ${locations}` : "",
    diseases ? `- Diseases: ${diseases}` : "",
    `- Verification basis: ${verification}`,
    `- Model/fit: ${alert.signal.model} / ${alert.signal.fit_status}`,
    "",
    `[Open the published Situation report](${alert.report.public_url})`,
    "",
    "This alert is a verified statistical surveillance signal, not a public-health risk rating.",
  ].filter((line) => line !== "");
  return { subject: `GIDS verified surveillance alert: ${alert.signal.title}`, markdown: lines.join("\n") };
}

function normalizedStringArray(value: unknown, type: "country" | "disease", limit: number): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) fail(`invalid_${type}_filters`);
  if (value.length > limit) fail(`too_many_${type}_filters`);
  const normalized = value.map((item) => {
    if (typeof item !== "string") fail(`invalid_${type}_filters`);
    const code = normalizeCode(item);
    if (!code) fail(`invalid_${type}_filters`);
    return type === "country" ? code.toUpperCase() : code.toLowerCase();
  });
  return [...new Set(normalized)];
}

function normalizedHttpsUrls(value: unknown, limit: number): string[] {
  if (!Array.isArray(value)) fail("invalid_evidence_urls");
  if (value.length > limit) fail("too_many_evidence_urls");
  const urls: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") fail("invalid_evidence_urls");
    try {
      const url = new URL(item.trim());
      if (url.protocol === "https:") urls.push(url.toString());
    } catch {
      // Invalid evidence is excluded; at least one valid URL remains mandatory.
    }
  }
  return [...new Set(urls)];
}

function requiredHttpsUrl(value: unknown, error: string): string {
  const text = requiredText(value, error, 2048);
  try {
    const url = new URL(text);
    if (url.protocol !== "https:") fail(error);
    return url.toString();
  } catch (caught) {
    if (caught instanceof SituationAlertValidationError) throw caught;
    fail(error);
  }
}

function requiredIsoDate(value: unknown, error: string): string {
  const text = requiredText(value, error, 80);
  const timestamp = Date.parse(text);
  if (!Number.isFinite(timestamp)) fail(error);
  return new Date(timestamp).toISOString();
}

function requiredUnitInterval(value: unknown, error: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) {
    fail(error);
  }
  return value;
}

function optionalUnitInterval(value: unknown, error: string): number | null {
  if (value === undefined || value === null) return null;
  return requiredUnitInterval(value, error);
}

function requiredText(value: unknown, error: string, maxLength: number): string {
  if (typeof value !== "string") fail(error);
  const text = value.trim();
  if (!text) fail(error);
  if (text.length > maxLength) fail(error);
  return text;
}

function fail(message: string): never {
  throw new SituationAlertValidationError(message);
}
