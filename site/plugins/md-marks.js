// A markdown-it plugin for the few things the site draws from plain
// Markdown with no comment block. Each one keys on how the repo's
// pages are already written, so the Markdown needs no change:
//
// - Every heading gets the anchor GitHub gives it, so `#limits` works
//   in both places.
// - A paragraph that opens with a bold "Status:" becomes the status
//   box.
// - A table is wrapped so it can scroll sideways on a phone, and a
//   cell that says only "Pass" or "Not run" becomes a chip.
// - A fenced code block gets a frame with its language as a label.
//   Shell and YAML blocks are marked so the script can add a Copy
//   button.

import { chipFor } from "../components/shared.js";

// ---- Constants --------------------------------------------------------

const STATUS_LEAD = /^Status:/;

const COPY_LANGUAGES = ["bash", "sh", "shell", "yaml", "yml"];

const TABLE_OPEN =
  '<div class="table-wrap" tabindex="0" role="region" ' +
  'aria-label="Table">\n<table class="table">\n';

const TABLE_CLOSE = "</table>\n</div>\n";

// ---- Walking ----------------------------------------------------------

// Comment blocks keep their content in `meta.children`, so every rule
// here has to look there too.
function eachList(tokens, visit) {
  visit(tokens);

  for (const token of tokens) {
    if (Array.isArray(token.meta?.children)) {
      eachList(token.meta.children, visit);
    }
  }
}

function plainText(inline) {
  return inline.children
    .filter((child) => child.type === "text" || child.type === "code_inline")
    .map((child) => child.content)
    .join("");
}

// ---- Headings ---------------------------------------------------------

// GitHub's rule: lower case, drop punctuation, spaces to hyphens.
export function slugOf(text) {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N} _-]/gu, "")
    .trim()
    .replace(/ /g, "-");
}

function anchorHeadings(tokens) {
  for (let index = 0; index < tokens.length; index += 1) {
    if (tokens[index].type !== "heading_open") {
      continue;
    }

    tokens[index].attrSet("id", slugOf(plainText(tokens[index + 1])));
  }
}

// ---- Status -----------------------------------------------------------

// markdown-it can leave an empty text token ahead of the bold.
function opensWithStatus(inline) {
  const [first, second] = inline.children.filter((child) => {
    return child.type !== "text" || child.content !== "";
  });

  if (first?.type !== "strong_open") {
    return false;
  }

  return STATUS_LEAD.test(second?.content ?? "");
}

function markStatus(tokens) {
  for (let index = 0; index < tokens.length; index += 1) {
    if (tokens[index].type !== "paragraph_open") {
      continue;
    }

    if (!opensWithStatus(tokens[index + 1])) {
      continue;
    }

    tokens[index].attrJoin("class", "status");
  }
}

// ---- Tables -----------------------------------------------------------

function chipCells(tokens, Token) {
  for (let index = 0; index < tokens.length; index += 1) {
    if (tokens[index].type !== "td_open") {
      continue;
    }

    const inline = tokens[index + 1];
    const chip = chipFor(inline.content.trim());

    if (!chip) {
      continue;
    }

    const html = new Token("html_inline", "", 0);

    html.content = chip;
    inline.children = [html];
  }
}

// ---- Code blocks ------------------------------------------------------

function renderFence(md, token) {
  const language = token.info.trim().split(/\s+/)[0];
  const code = md.utils.escapeHtml(token.content);
  const copy = COPY_LANGUAGES.includes(language) ? " data-copy" : "";

  let label = "";

  if (language) {
    label = `<p class="code__label">${md.utils.escapeHtml(language)}</p>\n`;
  }

  return (
    `<div class="code"${copy}>\n${label}` +
    `<pre class="code__body"><code>${code}</code></pre>\n</div>\n`
  );
}

// ---- Plugin -----------------------------------------------------------

export default function mdMarks(md) {
  md.core.ruler.push("md_marks", (state) => {
    eachList(state.tokens, (tokens) => {
      anchorHeadings(tokens);
      markStatus(tokens);
      chipCells(tokens, state.Token);
    });
  });

  md.renderer.rules.table_open = () => TABLE_OPEN;
  md.renderer.rules.table_close = () => TABLE_CLOSE;
  md.renderer.rules.fence = (tokens, index) => {
    return renderFence(md, tokens[index]);
  };
}
