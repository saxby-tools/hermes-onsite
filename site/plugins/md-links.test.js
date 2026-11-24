// Tests for the link plugin: Markdown links become page links, links
// and pictures in kept folders point at the copies, and everything
// else points at GitHub. A page is served from a folder of its own,
// so its links are one folder deeper than its Markdown file's.

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

function render(markdown, inputPath) {
  const md = new MarkdownIt().use(mdLinks, OPTIONS);

  return md.render(markdown, { page: { inputPath } });
}

function hrefOf(markdown, inputPath) {
  return render(markdown, inputPath).match(/href="([^"]*)"/)[1];
}

function srcOf(markdown, inputPath) {
  return render(markdown, inputPath).match(/src="([^"]*)"/)[1];
}

// ---- Tests ------------------------------------------------------------

test("a Markdown link points at the built page's folder", () => {
  const href = hrefOf("[d](DECISIONS.md#routing)", "./README.md");

  assert.equal(href, "DECISIONS/#routing");
});

test("a link between two pages climbs out of the page's own folder", () => {
  const href = hrefOf("[g](guide.md)", "./docs/design.md");

  assert.equal(href, "../guide/");
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
  assert.equal(hrefOf("[h](../README.md)", "./docs/design.md"), "../../");
  assert.equal(hrefOf("[h](README.md#use)", "./DECISIONS.md"), "../#use");
  assert.equal(hrefOf("[h](README.md)", "./README.md"), "./");
});

test("Markdown that is not a page points at GitHub", () => {
  const href = hrefOf("[s](../site/README.md)", "./docs/design.md");

  assert.equal(href, `${SOURCE_URL}site/README.md`);
});

test("a kept folder is reached from the page's own folder", () => {
  assert.equal(hrefOf("[m](docs/brand/mark.svg)", "./README.md"),
    "docs/brand/mark.svg");
  assert.equal(hrefOf("[m](brand/mark.svg)", "./docs/design.md"),
    "../brand/mark.svg");
  assert.equal(hrefOf("[b](brand/)", "./docs/design.md"), "../brand/");
});

test("a picture in a kept folder is reached the same way", () => {
  assert.equal(srcOf("![m](docs/brand/banner.png)", "./README.md"),
    "docs/brand/banner.png");
  assert.equal(srcOf("![m](brand/plate-head.png)", "./docs/design.md"),
    "../brand/plate-head.png");
});

test("absolute links and fragments are left alone", () => {
  assert.equal(hrefOf("[a](https://a.test/x)", "./README.md"),
    "https://a.test/x");
  assert.equal(hrefOf("[a](#limits)", "./README.md"), "#limits");
});
