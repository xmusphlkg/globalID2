import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { createQrDataUrl } from "./qr-code";

// These SVG fixtures were independently rasterized and decoded with jsQR 1.4.0.
// They protect valid format-bit placement and the existing 106-byte URL policy.
const fixtures = [
  ["https://globalinfectiousdisease.com/data-updates/", "102b6bb424b457856a9bf5f1740985565d3401a3077ab60401e307e54f503bc2"],
  ["https://example.org/疫情?地区=中国", "c5f7c534ba5b6ccb605d083a25130d712077c62586996862a22d66c7c935a9ea"],
  ["https://example.org/dashboard?filter=" + "x".repeat(160), "ee83b9d6c340059798a5b429c9d4c5f8082bab6cabdc9467fa8f24d7cc9d79a0"],
  ["https://example.org/" + "a".repeat(130), "f53adf50600dda73edb5f6da88636bf037ead91a89943434f49851a92b53d41b"],
];

describe("validated dashboard QR destinations", () => {
  it.each(fixtures)("retains the independently decoded symbol for %s", (url, expectedHash) => {
    expect(createHash("sha256").update(createQrDataUrl(url)).digest("hex")).toBe(expectedHash);
  });
});
