import assert from "node:assert/strict";
import test from "node:test";

import {
  secureTextEqual,
  sha256Hex,
  signSubscriptionToken,
  verifySubscriptionToken,
} from "../src/lib/security.ts";

const SECRET = "a-test-secret-that-is-not-used-in-production";
const NOW = Date.UTC(2026, 7, 5, 0, 0, 0);

test("round-trips a signed subscription token", async () => {
  const token = await signSubscriptionToken(SECRET, "confirm", "sub-123", 600, NOW);
  assert.deepEqual(
    await verifySubscriptionToken(SECRET, token, "confirm", NOW + 599_000),
    { subscriptionId: "sub-123" },
  );
});

test("rejects wrong purpose, secret, tampering, and expiration", async () => {
  const token = await signSubscriptionToken(SECRET, "confirm", "sub-123", 600, NOW);
  assert.equal(await verifySubscriptionToken(SECRET, token, "unsubscribe", NOW), null);
  assert.equal(await verifySubscriptionToken("wrong-secret", token, "confirm", NOW), null);
  assert.equal(await verifySubscriptionToken(SECRET, `${token.slice(0, -1)}x`, "confirm", NOW), null);
  assert.equal(await verifySubscriptionToken(SECRET, token, "confirm", NOW + 601_000), null);
});

test("produces the expected SHA-256 digest", async () => {
  assert.equal(
    await sha256Hex("abc"),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});

test("compares ingest secrets after fixed-length hashing", async () => {
  assert.equal(await secureTextEqual("same-secret", "same-secret"), true);
  assert.equal(await secureTextEqual("short", "a-different-and-longer-secret"), false);
  assert.equal(await secureTextEqual("", "expected"), false);
});
