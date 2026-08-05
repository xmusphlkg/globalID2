import assert from "node:assert/strict";
import test from "node:test";

import { smtpConfig } from "../src/lib/smtp-config.ts";

test("treats incomplete or invalid SMTP settings as a non-delivery boundary", () => {
  assert.equal(smtpConfig({}), null);
  assert.equal(smtpConfig({
    SMTP_HOST: "smtp.test",
    SMTP_PORT: "invalid",
    SMTP_USERNAME: "user",
    SMTP_PASSWORD: "password",
    SMTP_FROM_EMAIL: "alerts@example.test",
  }), null);
  assert.equal(smtpConfig({
    SMTP_HOST: "smtp.test",
    SMTP_PORT: "587",
    SMTP_USERNAME: "user",
    SMTP_PASSWORD: "password",
    SMTP_FROM_EMAIL: "not-an-email",
  }), null);
});

test("normalizes a complete SMTP configuration without changing TLS semantics", () => {
  assert.deepEqual(smtpConfig({
    SMTP_HOST: " smtp.test ",
    SMTP_PORT: "465",
    SMTP_USERNAME: " user ",
    SMTP_PASSWORD: " password ",
    SMTP_FROM_EMAIL: " ALERTS@EXAMPLE.TEST ",
    SMTP_FROM_NAME: " GIDS Alerts ",
    SMTP_USE_TLS: "false",
  }), {
    host: " smtp.test ",
    port: 465,
    username: " user ",
    password: " password ",
    fromEmail: "alerts@example.test",
    fromName: "GIDS Alerts",
    useTls: false,
  });
});
