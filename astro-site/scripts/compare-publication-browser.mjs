import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import sharp from 'sharp';

const [beforeRoot, afterRoot] = process.argv.slice(2).map(path => resolve(path));
if (!beforeRoot || !afterRoot) throw new Error('Usage: <before browser evidence> <after browser evidence>');
const before = JSON.parse(readFileSync(resolve(beforeRoot, 'baseline.json')));
const after = JSON.parse(readFileSync(resolve(afterRoot, 'baseline.json')));
const oldCases = new Map(before.results.map(row => [row.name, row]));
const fields = ['title', 'lang', 'canonical', 'hreflang', 'description', 'structured_data', 'h1', 'text', 'links',
  'controls', 'styles', 'charts', 'document_width', 'document_height', 'language_switch_target', 'language_switch_status'];
const results = [];
for (const row of after.results) {
  const old = oldCases.get(row.name);
  if (!old) throw new Error(`Unknown browser case ${row.name}`);
  const differences = fields.filter(key => JSON.stringify(old[key]) !== JSON.stringify(row[key]));
  const images = await Promise.all([resolve(beforeRoot, old.screenshot), resolve(afterRoot, row.screenshot)]
    .map(path => sharp(path).ensureAlpha().raw().toBuffer({ resolveWithObject: true })));
  let changedPixels = 0, absoluteDifference = 0;
  const [a, b] = images;
  if (a.data.length !== b.data.length) throw new Error('Screenshot dimensions changed');
  for (let offset = 0; offset < a.data.length; offset += 4) {
    let maximum = 0;
    for (let channel = 0; channel < 3; channel++) {
      const difference = Math.abs(a.data[offset + channel] - b.data[offset + channel]);
      absoluteDifference += difference; maximum = Math.max(maximum, difference);
    }
    if (maximum > 10) changedPixels++;
  }
  results.push({ name: row.name, passed: old.captured && row.captured && !differences.length && !row.page_errors.length
    && !row.http_errors.length && row.language_switch_status === 200,
    changed_fields: differences, previous_page_errors: old.page_errors.length, candidate_page_errors: row.page_errors,
    candidate_http_errors: row.http_errors, screenshot_changed_pixels: changedPixels,
    screenshot_changed_ratio: changedPixels / (a.info.width * a.info.height),
    screenshot_mean_absolute_rgb_difference: absoluteDifference / (a.info.width * a.info.height * 3) });
}
const expectedCases = after.expected_cases;
if (!Number.isInteger(expectedCases) || expectedCases < 1) throw new Error('Missing expected browser case count');
const receipt = { before: beforeRoot, after: afterRoot, expected_cases: expectedCases, cases: results.length,
  passed: results.length === expectedCases && results.every(row => row.passed),
  screenshot_note: 'Screenshot metrics are reported separately and require review when nonzero; contract pass is exact text/link/SEO/style/chart parity with no candidate errors', results };
writeFileSync(resolve(afterRoot, 'comparison.json'), JSON.stringify(receipt, null, 2));
console.log(JSON.stringify({ passed: receipt.passed, cases: results.length, differences: results.filter(row => !row.passed),
  maximum_screenshot_changed_ratio: Math.max(...results.map(row => row.screenshot_changed_ratio)) }));
if (!receipt.passed) process.exitCode = 1;
