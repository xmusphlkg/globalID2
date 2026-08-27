import assert from "node:assert/strict";
import test from "node:test";

import { parseSmtpProviderMessageId } from "../src/lib/smtp-response.ts";

test("extracts provider queue identifiers without mistaking generic OK text", () => {
  assert.equal(
    parseSmtpProviderMessageId("250 2.0.0 Ok: queued as 4F2AB123456"),
    "4F2AB123456",
  );
  assert.equal(parseSmtpProviderMessageId("250 message-id=<delivery.123@example.test>"), "delivery.123@example.test");
  assert.equal(parseSmtpProviderMessageId("250 2.0.0 Ok"), "");
});
