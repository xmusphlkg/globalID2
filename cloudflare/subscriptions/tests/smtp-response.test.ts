import assert from "node:assert/strict";
import test from "node:test";

import { parseSmtpProviderMessageId } from "../src/lib/smtp-response.ts";

test("extracts an SES SMTP acceptance ID without mistaking generic OK responses for IDs", () => {
  const id = "01000174f87e0fb4-81b97243-56b0-4cd1-90c7-d6987a00fdc1-000000";
  assert.equal(parseSmtpProviderMessageId(`250 Ok ${id}`), id);
  assert.equal(parseSmtpProviderMessageId("250 Ok accepted"), "");
});

test("extracts provider queue identifiers without mistaking generic OK text", () => {
  assert.equal(
    parseSmtpProviderMessageId("250 2.0.0 Ok: queued as 4F2AB123456"),
    "4F2AB123456",
  );
  assert.equal(parseSmtpProviderMessageId("250 message-id=<delivery.123@example.test>"), "delivery.123@example.test");
  assert.equal(parseSmtpProviderMessageId("250 2.0.0 Ok"), "");
});
