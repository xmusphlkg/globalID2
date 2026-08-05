import assert from "node:assert/strict";
import test from "node:test";

import {
  boundedText,
  escapeHtml,
  normalizeCode,
  normalizeEmail,
  normalizeLocale,
  parseJsonObject,
  toArray,
  valueAsString,
} from "../src/lib/input.ts";

test("normalizes locale variants and rejects unsupported locales", () => {
  assert.equal(normalizeLocale(" ZH_cn ", "en"), "zh");
  assert.equal(normalizeLocale("pt-BR", "en"), "pt");
  assert.equal(normalizeLocale("ru", "en"), "en");
});

test("normalizes email and codes without broadening accepted input", () => {
  assert.equal(normalizeEmail(" Reader@Example.COM "), "reader@example.com");
  assert.equal(normalizeEmail("not-an-email"), "");
  assert.equal(normalizeCode(" Reports /Weekly:@ "), "reportsweekly:");
});

test("converts payload values using the existing first-value and comma rules", () => {
  assert.equal(valueAsString(["first", "second"]), "first");
  assert.deepEqual(toArray([" CN, US ", ["JP"]]), ["CN", "US", "JP"]);
  assert.equal(boundedText("  abcdef  ", "fallback", 3), "abc");
  assert.equal(boundedText("   ", "fallback", 3), "fallback");
});

test("parses only JSON objects and escapes HTML-sensitive characters", () => {
  assert.deepEqual(parseJsonObject('{"ok":true}'), { ok: true });
  assert.deepEqual(parseJsonObject("[]"), {});
  assert.deepEqual(parseJsonObject("{"), {});
  assert.equal(escapeHtml('<a title="x">&'), "&lt;a title=&quot;x&quot;&gt;&amp;");
});
