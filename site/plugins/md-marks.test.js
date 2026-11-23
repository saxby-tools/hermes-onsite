// Tests for the plugin that draws site features from plain Markdown.

import assert from "node:assert/strict";
import { test } from "node:test";

import MarkdownIt from "markdown-it";

import mdMarks, { slugOf } from "./md-marks.js";

// ---- Helpers ----------------------------------------------------------

function render(markdown) {
  return new MarkdownIt().use(mdMarks).render(markdown);
}

// ---- Tests ------------------------------------------------------------

test("a heading gets the anchor GitHub gives it", () => {
  assert.equal(slugOf("What is tested"), "what-is-tested");
  assert.equal(slugOf("12. `/onsite <target>` drops it"),
    "12-onsite-target-drops-it");
  assert.match(render("## What is tested"), /<h2 id="what-is-tested">/);
});

test("a paragraph that opens with a bold Status is boxed", () => {
  const html = render("**Status: v0.1.0.** Not yet accepted.");

  assert.match(html, /<p class="status">/);
});

test("other bold openings are left alone", () => {
  assert.doesNotMatch(render("**No sandbox.** Keep them on."), /status/);
});

test("a table is wrapped, and result cells become chips", () => {
  const html = render(
    "| Phase | Result |\n| --- | --- |\n| 1 | Pass |\n| 5 | Not run |\n",
  );

  assert.match(html, /<div class="table-wrap"/);
  assert.match(html, /<span class="chip chip-pass">Pass<\/span>/);
  assert.match(html, /<span class="chip chip-open">Not run<\/span>/);
});

test("a cell that only mentions a status stays text", () => {
  const html = render("| A |\n| --- |\n| It did not pass twice |\n");

  assert.doesNotMatch(html, /chip/);
});

test("a shell block is framed, labelled and marked for copying", () => {
  const html = render("```bash\nhermes onsite status\n```\n");

  assert.match(html, /<div class="code" data-copy>/);
  assert.match(html, /<p class="code__label">bash<\/p>/);
});

test("a text block is framed but not marked for copying", () => {
  const html = render("```text\na --> b\n```\n");

  assert.match(html, /<div class="code">/);
  assert.match(html, /a --&gt; b/);
});
