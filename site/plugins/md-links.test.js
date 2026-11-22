// Tests for the link plugin: Markdown links become page links, kept
// folders stay, and everything else points at GitHub.

import assert from "node:assert/strict";
import { test } from "node:test";

import MarkdownIt from "markdown-it";

import mdLinks from "./md-links.js";

// ---- Constants --------------------------------------------------------

const SOURCE_URL = "https://example.test/repo/blob/main/";

const OPTIONS = {
  sourceUrl: SOURCE_URL,
  home: "README.md",
  keep: ["docs/brand"],
  skip: ["site"],
};

// ---- Helpers ----------------------------------------------------------

function hrefOf(markdown, inputPath) {
  const md = new MarkdownIt().use(mdLinks, OPTIONS);
  const html = md.render(markdown, { page: { inputPath } });

  return html.match(/href="([^"]*)"/)[1];
}

// ---- Tests ------------------------------------------------------------

test("a Markdown link points at the built page", () => {
  const href = hrefOf("[d](DECISIONS.md#routing)", "./README.md");

  assert.equal(href, "DECISIONS.html#routing");
});

test("a file that is not a page points at GitHub", () => {
  const href = hrefOf("[l](LICENSE)", "./README.md");

  assert.equal(href, `${SOURCE_URL}LICENSE`);
});

test("a folder points at GitHub's tree view", () => {
  const href = hrefOf("[r](rig/results/)", "./README.md");

  assert.equal(href, "https://example.test/repo/tree/main/rig/results/");
});

test("a link from a subfolder is resolved from the repo's root", () => {
  const href = hrefOf("[s](../rig/run.py)", "./docs/findings.md");

  assert.equal(href, `${SOURCE_URL}rig/run.py`);
});

test("the front page's Markdown points at the site's root", () => {
  assert.equal(hrefOf("[h](../README.md)", "./docs/design.md"), ".././");
  assert.equal(hrefOf("[h](README.md#use)", "./DECISIONS.md"), "./#use");
});

test("Markdown that is not a page points at GitHub", () => {
  const href = hrefOf("[s](../site/README.md)", "./docs/design.md");

  assert.equal(href, `${SOURCE_URL}site/README.md`);
});

test("a kept folder is left alone", () => {
  const href = hrefOf("[m](docs/brand/mark.svg)", "./README.md");

  assert.equal(href, "docs/brand/mark.svg");
});

test("absolute links and fragments are left alone", () => {
  assert.equal(hrefOf("[a](https://a.test/x)", "./README.md"),
    "https://a.test/x");
  assert.equal(hrefOf("[a](#limits)", "./README.md"), "#limits");
});
