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
      automation_decision: null as null | {
        status: string;
        basis: string;
        policy_version: string;
        calibration_hash: string;
        gate_reasons: string[];
        matched_event_ids: string[];
        decided_at: string;
      },
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

function tieredAutoAlert() {
  const payload = validAlert();
  payload.signal.verification_basis = "automated_policy";
  payload.signal.verification_policy_version = "tiered_auto_v3.2";
  payload.signal.verified_by = "policy:tiered_auto_v3.2";
  payload.signal.model = "multi_horizon_gamma_poisson_v1";
  payload.signal.q_value = 0.025;
  payload.signal.automation_decision = {
    status: "auto_verified",
    basis: "calibrated_statistical",
    policy_version: "tiered_auto_v3.2",
    calibration_hash: "artifact-hash",
    gate_reasons: [],
    matched_event_ids: [],
    decided_at: "2026-08-17T11:55:00Z",
  };
  return payload;
}

test("accepts tiered_auto_v3.2 only with a structured calibrated decision", () => {
  const payload = tieredAutoAlert();
  const alert = parseSituationAlertPayload(payload);
  assert.equal(alert.signal.verification_basis, "automated_policy");
  assert.equal(alert.signal.verification_policy_version, "tiered_auto_v3.2");
  assert.equal(alert.signal.q_value, 0.025);
  assert.equal(alert.signal.automation_decision?.basis, "calibrated_statistical");
});

test("accepts official corroboration for a review-level rare or fallback signal", () => {
  const payload = tieredAutoAlert();
  payload.signal.signal_type = "officially_correlated_signal";
  payload.signal.model = "seasonal_empirical_fallback_v1";
  payload.signal.fit_status = "fallback_completed";
  payload.signal.detector_tier = "rare_count";
  payload.signal.q_value = 0.05;
  payload.signal.automation_decision!.basis = "official_corroboration";
  payload.signal.automation_decision!.matched_event_ids = ["event-cluster:one"];
  const alert = parseSituationAlertPayload(payload);
  assert.equal(alert.signal.automation_decision?.basis, "official_corroboration");
  assert.deepEqual(alert.signal.automation_decision?.matched_event_ids, ["event-cluster:one"]);
});

test("rejects unknown or weakened automated verification policies", () => {
  const cases: Array<[string, (payload: ReturnType<typeof validAlert>) => void]> = [
    ["tiered_auto_policy_required", (payload) => { payload.signal.verification_policy_version = "unknown_auto_v2"; }],
    ["tiered_auto_verifier_required", (payload) => { payload.signal.verified_by = "policy:unknown"; }],
    ["tiered_auto_current_signal_required", (payload) => { payload.signal.temporal_relevance = "lagged"; }],
    ["tiered_auto_current_signal_required", (payload) => { payload.signal.data_status = "held_back"; }],
    ["calibrated_statistical_q_required", (payload) => { payload.signal.q_value = 0.025001; }],
    ["calibrated_statistical_q_required", (payload) => { payload.signal.q_value = null; }],
    ["calibrated_statistical_primary_fit_required", (payload) => { payload.signal.model = "seasonal_empirical_fallback_v1"; }],
    ["calibrated_statistical_primary_fit_required", (payload) => { payload.signal.fit_status = "fallback_completed"; }],
    ["calibrated_statistical_primary_fit_required", (payload) => { payload.signal.detector_tier = "rate"; }],
    ["tiered_auto_effect_threshold_required", (payload) => { payload.signal.effect_threshold_passed = false; }],
    ["tiered_auto_completeness_required", (payload) => { payload.signal.completeness = 0.949; }],
    ["automation_gate_reasons_must_be_empty", (payload) => { payload.signal.automation_decision!.gate_reasons = ["failed"]; }],
  ];
  for (const [message, mutate] of cases) {
    const payload = tieredAutoAlert();
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
  const automaticPayload = tieredAutoAlert();
  const automaticAlert = parseSituationAlertPayload(automaticPayload);
  const english = situationAlertContent(automaticAlert, "en-US");
  const chinese = situationAlertContent(analystAlert, "zh-CN");
  assert.match(english.subject, /verified surveillance alert/i);
  assert.match(english.markdown, /tiered automated policy \(tiered_auto_v3.2\)/i);
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
