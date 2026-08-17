import assert from "node:assert/strict";
import test from "node:test";

import {
  SITUATION_ALERT_JOB_SCHEMA_VERSION,
  isAllowedSituationPublicUrl,
  parseSituationAlertJob,
  parseSituationAlertPayload,
  situationAlertContent,
  situationAlertJob,
  SituationAlertValidationError,
} from "../src/lib/situation-alert.ts";

function validAlert() {
  return {
    schema_version: "situation-alert.v1",
    idempotency_key: "situation-v3-report-1:signal-1",
    report: {
      report_id: "situation-v3-daily-2026-08-17-r8",
      as_of: "2026-08-17T12:00:00Z",
      quality_gate_status: "degraded",
      publication_status: "published",
      public_url: "https://globalinfectiousdisease.com/situation/2026-08-17/",
    },
    signal: {
      signal_id: "signal-1",
      analysis_status: "analyzed",
      anomaly_state: "strong",
      signal_type: "statistical_signal",
      temporal_relevance: "current",
      data_status: "current",
      completeness: 0.98,
      q_value: 0.008,
      model: "robust_quasi_poisson_v1",
      fit_status: "completed",
      detector_tier: "common_count",
      effect_threshold_passed: true,
      verification_status: "verified",
      verification_basis: "analyst_review",
      verification_policy_version: null,
      verified_by: "analyst:reviewer-17",
      verified_at: "2026-08-17T11:55:00Z",
      observed_at: "2026-08-16T00:00:00Z",
      title: " Influenza surveillance signal ",
      summary: " A manually checked rise was observed. ",
      countries: [" us ", "US", "cn"],
      diseases: [" Influenza ", "influenza"],
      evidence_urls: ["http://insecure.test/evidence", "https://cdc.gov/example"],
    },
  };
}

test("accepts the versioned, published, analyzed analyst-review contract", () => {
  const alert = parseSituationAlertPayload(validAlert());
  assert.equal(alert.report.quality_gate_status, "degraded");
  assert.deepEqual(alert.signal.countries, ["US", "CN"]);
  assert.deepEqual(alert.signal.diseases, ["influenza"]);
  assert.deepEqual(alert.signal.evidence_urls, ["https://cdc.gov/example"]);
  assert.equal(alert.signal.title, "Influenza surveillance signal");
  assert.equal(alert.signal.verified_at, "2026-08-17T11:55:00.000Z");
});

test("rejects unknown verification bases, unverified, stale, unpublished, and non-alert inputs", () => {
  const cases: Array<[string, (payload: ReturnType<typeof validAlert>) => void]> = [
    ["invalid_verification_basis", (payload) => { payload.signal.verification_basis = "automatic"; }],
    ["verified_signal_required", (payload) => { payload.signal.verification_status = "under_review"; }],
    ["current_or_lagged_signal_required", (payload) => { payload.signal.temporal_relevance = "historical"; }],
    ["published_report_required", (payload) => { payload.report.publication_status = "shadow"; }],
    ["alert_or_strong_signal_required", (payload) => { payload.signal.anomaly_state = "watch"; }],
    ["opaque_verified_by_required", (payload) => { payload.signal.verified_by = "person@example.test"; }],
    ["signal_evidence_url_required", (payload) => { payload.signal.evidence_urls = ["http://example.test"]; }],
  ];
  for (const [message, mutate] of cases) {
    const payload = validAlert();
    mutate(payload);
    assert.throws(
      () => parseSituationAlertPayload(payload),
      (error: unknown) => error instanceof SituationAlertValidationError && error.message === message,
    );
  }
});

test("accepts guarded_auto_v1 only when every policy guard is present", () => {
  const payload = validAlert();
  payload.signal.verification_basis = "automated_policy";
  payload.signal.verification_policy_version = "guarded_auto_v1";
  payload.signal.verified_by = "policy:guarded_auto_v1";
  payload.signal.q_value = 0.01;
  const alert = parseSituationAlertPayload(payload);
  assert.equal(alert.signal.verification_basis, "automated_policy");
  assert.equal(alert.signal.verification_policy_version, "guarded_auto_v1");
  assert.equal(alert.signal.q_value, 0.01);
});

test("rejects unknown or weakened automated verification policies", () => {
  const automaticAlert = () => {
    const payload = validAlert();
    payload.signal.verification_basis = "automated_policy";
    payload.signal.verification_policy_version = "guarded_auto_v1";
    payload.signal.verified_by = "policy:guarded_auto_v1";
    return payload;
  };
  const cases: Array<[string, (payload: ReturnType<typeof validAlert>) => void]> = [
    ["guarded_auto_policy_required", (payload) => { payload.signal.verification_policy_version = "unknown_auto_v2"; }],
    ["guarded_auto_verifier_required", (payload) => { payload.signal.verified_by = "policy:unknown"; }],
    ["guarded_auto_current_signal_required", (payload) => { payload.signal.temporal_relevance = "lagged"; }],
    ["guarded_auto_current_signal_required", (payload) => { payload.signal.data_status = "held_back"; }],
    ["guarded_auto_q_threshold_required", (payload) => { payload.signal.q_value = 0.010001; }],
    ["guarded_auto_q_threshold_required", (payload) => { payload.signal.q_value = null; }],
    ["guarded_auto_primary_fit_required", (payload) => { payload.signal.model = "seasonal_empirical_fallback_v1"; }],
    ["guarded_auto_primary_fit_required", (payload) => { payload.signal.fit_status = "fallback_completed"; }],
    ["guarded_auto_detector_tier_required", (payload) => { payload.signal.detector_tier = "rate"; }],
    ["guarded_auto_effect_threshold_required", (payload) => { payload.signal.effect_threshold_passed = false; }],
    ["guarded_auto_completeness_required", (payload) => { payload.signal.completeness = 0.949; }],
  ];
  for (const [message, mutate] of cases) {
    const payload = automaticAlert();
    mutate(payload);
    assert.throws(
      () => parseSituationAlertPayload(payload),
      (error: unknown) => error instanceof SituationAlertValidationError && error.message === message,
    );
  }
});

test("analyst review may accept lagged data and a transparently identified fallback fit", () => {
  const payload = validAlert();
  payload.signal.temporal_relevance = "lagged";
  payload.signal.data_status = "held_back";
  payload.signal.q_value = null;
  payload.signal.model = "seasonal_empirical_fallback_v1";
  payload.signal.fit_status = "fallback_completed";
  const alert = parseSituationAlertPayload(payload);
  assert.equal(alert.signal.verification_basis, "analyst_review");
  assert.equal(alert.signal.verification_policy_version, null);
  assert.equal(alert.signal.fit_status, "fallback_completed");
});

test("prevents a verification timestamp later than the published report", () => {
  const payload = validAlert();
  payload.signal.verified_at = "2026-08-17T12:06:00Z";
  assert.throws(() => parseSituationAlertPayload(payload), /verification_after_report_as_of/);
});

test("allows public report links only from explicitly configured origins", () => {
  assert.equal(
    isAllowedSituationPublicUrl(
      "https://globalinfectiousdisease.com/situation/latest/",
      "https://globalinfectiousdisease.com,https://preview.example.test",
    ),
    true,
  );
  assert.equal(
    isAllowedSituationPublicUrl("https://globalinfectiousdisease.com.evil.test/phish"),
    false,
  );
  assert.equal(isAllowedSituationPublicUrl("javascript:alert(1)"), false);
});

test("builds bilingual wording with an explicit verification basis and policy", () => {
  const analystAlert = parseSituationAlertPayload(validAlert());
  const automaticPayload = validAlert();
  automaticPayload.signal.verification_basis = "automated_policy";
  automaticPayload.signal.verification_policy_version = "guarded_auto_v1";
  automaticPayload.signal.verified_by = "policy:guarded_auto_v1";
  const automaticAlert = parseSituationAlertPayload(automaticPayload);
  const english = situationAlertContent(automaticAlert, "en-US");
  const chinese = situationAlertContent(analystAlert, "zh-CN");
  assert.match(english.subject, /verified surveillance alert/i);
  assert.match(english.markdown, /guarded automated policy \(guarded_auto_v1\)/i);
  assert.match(english.markdown, /not a public-health risk rating/i);
  assert.match(chinese.subject, /已核验监测提醒/);
  assert.match(chinese.markdown, /人工分析员复核（无自动策略）/);
  assert.match(chinese.markdown, /不等同于公共卫生风险等级/);
});

test("uses a small versioned Queue message with no subscriber data", () => {
  const job = situationAlertJob("event-1");
  assert.deepEqual(job, {
    schema_version: SITUATION_ALERT_JOB_SCHEMA_VERSION,
    event_id: "event-1",
  });
  assert.deepEqual(parseSituationAlertJob(job), job);
  assert.deepEqual(parseSituationAlertJob({ ...job, email: "reader@example.test" }), job);
  assert.equal(parseSituationAlertJob({ schema_version: "old", event_id: "event-1" }), null);
});
