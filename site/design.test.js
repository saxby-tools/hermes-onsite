// Checks that docs/design.md and the stylesheet agree. The design
// page promises to show every token, so a token added to one and not
// the other fails here, and so does a colour whose value has drifted.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// ---- Constants --------------------------------------------------------

const CSS_PATH = new URL("./static/hands.css", import.meta.url);

const PAGE_PATH = new URL("../docs/design.md", import.meta.url);

const DECLARED = /^\s*(--[a-z0-9-]+):\s*([^;]+);/gm;

const NAMED = /`(--[a-z0-9-]+)`/g;

const COLOUR_ROW =
  /^\| `(--color-[a-z-]+)` \| `(#[0-9a-f]{6})` \| `(#[0-9a-f]{6})` \|/gm;

// ---- Reading ----------------------------------------------------------

const css = readFileSync(CSS_PATH, "utf8");

const page = readFileSync(PAGE_PATH, "utf8");

// The tokens are the custom properties in the first `:root` rule.
function declaredTokens() {
  const root = css.slice(css.indexOf(":root {"), css.indexOf("}\n"));
  const tokens = new Map();

  for (const [, name, value] of root.matchAll(DECLARED)) {
    tokens.set(name, value.replace(/\s+/g, " ").trim());
  }

  return tokens;
}

function expectedColour(light, dark) {
  if (light === dark) {
    return light;
  }

  return `light-dark(${light}, ${dark})`;
}

// ---- Tests ------------------------------------------------------------

const tokens = declaredTokens();

test("the stylesheet declares tokens", () => {
  assert.ok(tokens.size > 30);
});

test("every token in the stylesheet is on the design page", () => {
  const named = new Set([...page.matchAll(NAMED)].map((match) => match[1]));
  const missing = [...tokens.keys()].filter((name) => !named.has(name));

  assert.deepEqual(missing, []);
});

test("every token on the design page is in the stylesheet", () => {
  const named = [...page.matchAll(NAMED)].map((match) => match[1]);
  const unknown = named.filter((name) => !tokens.has(name));

  assert.deepEqual(unknown, []);
});

test("every colour on the design page matches the stylesheet", () => {
  const rows = [...page.matchAll(COLOUR_ROW)];

  assert.ok(rows.length > 10);

  for (const [, name, light, dark] of rows) {
    assert.equal(tokens.get(name), expectedColour(light, dark), name);
  }
});
