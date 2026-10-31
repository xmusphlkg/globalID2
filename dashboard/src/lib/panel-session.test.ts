import { afterEach, describe, expect, it } from "vitest";

import { issueSession, passwordMatches, sessionValid } from "./panel-session";

const ORIGINAL = process.env.DASHBOARD_PANEL_PASSWORD;

afterEach(() => {
  if (ORIGINAL === undefined) delete process.env.DASHBOARD_PANEL_PASSWORD;
  else process.env.DASHBOARD_PANEL_PASSWORD = ORIGINAL;
});

describe("panel session", () => {
  it("accepts the configured password and a fresh session only", () => {
    process.env.DASHBOARD_PANEL_PASSWORD = "correct horse";
    expect(passwordMatches("correct horse")).toBe(true);
    expect(passwordMatches("wrong")).toBe(false);
    const token = issueSession(1_700_000_000_000);
    expect(sessionValid(token, 1_700_000_000_000)).toBe(true);
    expect(sessionValid(token, 1_700_000_000_000 + 8 * 24 * 60 * 60 * 1000)).toBe(false);
    expect(sessionValid("1.not-a-signature", 1_700_000_000_000)).toBe(false);
  });

  it("stays open when no password is configured", () => {
    delete process.env.DASHBOARD_PANEL_PASSWORD;
    expect(sessionValid(undefined)).toBe(true);
  });
});
