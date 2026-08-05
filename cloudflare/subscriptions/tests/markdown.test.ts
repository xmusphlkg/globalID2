import assert from "node:assert/strict";
import test from "node:test";

import {
  cleanHeaderValue,
  cleanMarkdown,
  markdownToHtml,
  renderInlineMarkdown,
  subjectFromMarkdown,
} from "../src/lib/markdown.ts";

test("renders supported block markdown", () => {
  const html = markdownToHtml("# Update\n\n- one\n- two\n\n```\n<a>\n```");
  assert.match(html, /<h2[^>]*>Update<\/h2>/);
  assert.match(html, /<ul[^>]*>/);
  assert.match(html, /<li[^>]*>one<\/li>/);
  assert.match(html, /<code>&lt;a&gt;<\/code>/);
});

test("escapes raw HTML while retaining safe supported inline markup", () => {
  const html = renderInlineMarkdown('<img src=x> **bold** [site](https://example.com/?a=1&b=2)');
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img src=x&gt;/);
  assert.match(html, /<strong>bold<\/strong>/);
  assert.match(html, /href="https:\/\/example\.com\/\?a=1&amp;b=2"/);
});

test("cleans headers and derives stable subjects", () => {
  assert.equal(cleanHeaderValue(" Hello\r\n Bcc: bad ", 200), "Hello Bcc: bad");
  assert.equal(cleanMarkdown("\u0000 body "), "body");
  assert.equal(subjectFromMarkdown("text\n# Later heading"), "Later heading");
  assert.equal(subjectFromMarkdown("plain first line"), "plain first line");
});
