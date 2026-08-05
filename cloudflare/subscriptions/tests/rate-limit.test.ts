import assert from "node:assert/strict";
import test from "node:test";

import { confirmationRateLimit, submissionRateLimited } from "../src/lib/rate-limit.ts";

const NOW = Date.UTC(2026, 7, 5, 12, 0, 0);

test("enforces the submission threshold including string D1 counts", () => {
  assert.equal(submissionRateLimited(undefined, 30), false);
  assert.equal(submissionRateLimited("29", 30), false);
  assert.equal(submissionRateLimited("30", 30), true);
  assert.equal(submissionRateLimited(31, 30), true);
});

test("calculates confirmation retry windows and clamps stale rows", () => {
  assert.deepEqual(confirmationRateLimit(1, null, 2, NOW), {
    allowed: true, retryAfterSeconds: 0,
  });
  assert.deepEqual(confirmationRateLimit("2", new Date(NOW - 2 * 60 * 1000).toISOString(), 2, NOW), {
    allowed: false, retryAfterSeconds: 480,
  });
  assert.deepEqual(confirmationRateLimit(2, new Date(NOW - 20 * 60 * 1000).toISOString(), 2, NOW), {
    allowed: false, retryAfterSeconds: 60,
  });
  assert.deepEqual(confirmationRateLimit(2, "invalid", 2, NOW), {
    allowed: false, retryAfterSeconds: 60,
  });
});
