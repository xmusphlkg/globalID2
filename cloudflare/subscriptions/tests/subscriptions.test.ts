import assert from "node:assert/strict";
import test from "node:test";

import {
  escapeLike,
  maskEmail,
  normalizeFilters,
  normalizeListCodes,
  parseFilterGroups,
  pendingCutoff,
  rowsToCounts,
} from "../src/lib/subscriptions.ts";

test("normalizes subscription lists and filters with stable defaults and order", () => {
  assert.deepEqual(normalizeListCodes({}), ["reports"]);
  assert.deepEqual(normalizeListCodes({ list_codes: [" Reports ", "alerts,reports"] }), ["reports", "alerts"]);
  assert.deepEqual(normalizeFilters({
    countries: [" cn ", "CN"],
    disease: " Influenza ",
    report_types: "Weekly,weekly",
    severities: [" HIGH "],
    research_topics: [" Vaccination ", "vaccination"],
    study_types: [" Systematic-Review "],
    peer_review_statuses: [" Peer-Reviewed "],
  }), [
    { type: "country", value: "CN" },
    { type: "disease", value: "influenza" },
    { type: "report_type", value: "weekly" },
    { type: "severity", value: "high" },
    { type: "research_topic", value: "vaccination" },
    { type: "study_type", value: "systematic-review" },
    { type: "peer_review_status", value: "peer-reviewed" },
  ]);
});

test("projects subscription helper values without leaking full addresses", () => {
  assert.deepEqual(rowsToCounts([{ status: "active", count: 2 }, { status: "pending", count: 0 }]), {
    active: 2, pending: 0,
  });
  assert.deepEqual(parseFilterGroups("country:CN|disease:flu:a|invalid"), {
    country: ["CN"], disease: ["flu:a"],
  });
  assert.equal(maskEmail("reader@example.test"), "re****@example.test");
  assert.equal(maskEmail("a@example.test"), "a**@example.test");
  assert.equal(escapeLike("10%_\\"), "10\\%\\_\\\\");
});

test("uses the intended 14-day expiry default and clamps configured days", () => {
  const now = Date.UTC(2026, 7, 5, 0, 0, 0);
  assert.equal(pendingCutoff(undefined, now), "2026-07-22T00:00:00.000Z");
  assert.equal(pendingCutoff("1", now), "2026-08-04T00:00:00.000Z");
  assert.equal(pendingCutoff("999", now), "2025-08-05T00:00:00.000Z");
});
