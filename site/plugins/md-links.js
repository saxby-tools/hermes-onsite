// A markdown-it plugin that keeps the repo's relative links working on
// the site. The Markdown is written for GitHub, where every file in
// the repo is one click away. The site only has the pages.
//
// - A link to a Markdown file points at the page built from it:
//   `DECISIONS.md#routing` becomes `DECISIONS.html#routing`.
// - A link to the front page's Markdown points at the site's root.
// - A link to a folder that is copied to the site, such as
//   `docs/brand/`, is left alone.
// - Any other relative link, such as `rig/results/` or `LICENSE`,
//   points at that file on GitHub. So does a link to Markdown that
//   is not built into a page, such as `site/README.md`.
//
// Links with a scheme (`https:`, `mailto:`), a leading `/` or only a
// `#fragment` are left alone. It pairs with a permalink of
// `<file path without .md>.html`. Adapted from the Saxby lab's plugin
// of the same name.

import { posix } from "node:path";

// ---- Constants --------------------------------------------------------

const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

const MARKDOWN_PATH = /\.md$/i;

const BLOB = "/blob/";

const TREE = "/tree/";

// ---- Rewriting --------------------------------------------------------

// The path from the repo's root, given the page the link is on.
function fromRoot(path, pagePath) {
  const folder = posix.dirname(pagePath.replace(/^\.\//, ""));

  return posix.normalize(posix.join(folder, path));
}

function isUnder(repoPath, folders) {
  return folders.some((prefix) => {
    return repoPath === prefix || repoPath.startsWith(`${prefix}/`);
  });
}

function onGitHub(repoPath, sourceUrl) {
  if (repoPath.endsWith("/")) {
    return sourceUrl.replace(BLOB, TREE) + repoPath;
  }

  return sourceUrl + repoPath;
}

function rewriteHref(href, pagePath, options) {
  if (SCHEME.test(href)) {
    return href;
  }

  if (href.startsWith("/")) {
    return href;
  }

  if (href.startsWith("#")) {
    return href;
  }

  const cut = href.search(/[?#]/);
  const path = cut === -1 ? href : href.slice(0, cut);
  const rest = cut === -1 ? "" : href.slice(cut);

  const repoPath = fromRoot(path, pagePath);

  if (repoPath.startsWith("..")) {
    return href;
  }

  if (isUnder(repoPath, options.keep)) {
    return href;
  }

  if (isUnder(repoPath, options.skip)) {
    return onGitHub(repoPath, options.sourceUrl) + rest;
  }

  if (repoPath === options.home) {
    return path.slice(0, -options.home.length) + "./" + rest;
  }

  if (MARKDOWN_PATH.test(path)) {
    return path.replace(MARKDOWN_PATH, ".html") + rest;
  }

  return onGitHub(repoPath, options.sourceUrl) + rest;
}

// Comment blocks keep their content in `meta.children`, so look there
// too, or links inside a block would be missed.
function rewriteTokens(tokens, pagePath, options) {
  for (const token of tokens) {
    if (token.children) {
      rewriteTokens(token.children, pagePath, options);
    }

    if (Array.isArray(token.meta?.children)) {
      rewriteTokens(token.meta.children, pagePath, options);
    }

    if (token.type !== "link_open") {
      continue;
    }

    const href = token.attrGet("href");

    if (href === null) {
      continue;
    }

    token.attrSet("href", rewriteHref(href, pagePath, options));
  }
}

// ---- Plugin -----------------------------------------------------------

export default function mdLinks(md, options) {
  md.core.ruler.push("md_links", (state) => {
    const pagePath = state.env?.page?.inputPath ?? "./README.md";

    rewriteTokens(state.tokens, pagePath, options);
  });
}
