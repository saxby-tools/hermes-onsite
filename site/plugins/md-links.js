// A markdown-it plugin that keeps the repo's relative links working on
// the site. The Markdown is written for GitHub, where every file in
// the repo is one click away. The site only has the pages.
//
// Every page but the front page is served from a folder of its own:
// `docs/guide.md` becomes `docs/guide/index.html`, so its address is
// `docs/guide/`. A page therefore sits one folder deeper than its
// Markdown file, and every relative link and picture on it has to be
// written again from there.
//
// - A link to a Markdown file points at the page built from it:
//   `DECISIONS.md#routing` becomes `DECISIONS/#routing`.
// - A link to the front page's Markdown points at the site's root.
// - A link or a picture in a folder that is copied to the site, such
//   as `docs/brand/`, points at the copy.
// - Any other relative link, such as `rig/results/` or `LICENSE`,
//   points at that file on GitHub. So does a link to Markdown that
//   is not built into a page, such as `site/README.md`.
//
// Links with a scheme (`https:`, `mailto:`), a leading `/` or only a
// `#fragment` are left alone. It pairs with a permalink of
// `<file path without .md>/index.html`. Adapted from the Saxby lab's
// plugin of the same name.

import { posix } from "node:path";

// ---- Constants --------------------------------------------------------

const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

const MARKDOWN_PATH = /\.md$/i;

const BLOB = "/blob/";

const TREE = "/tree/";

// What each kind of token keeps its address in.
const ADDRESS_ATTRS = {
  link_open: "href",
  image: "src",
};

// ---- Paths ------------------------------------------------------------

function repoPathOf(pagePath) {
  return pagePath.replace(/^\.\//, "");
}

// The path from the repo's root, given the page the link is on.
function fromRoot(path, pagePath) {
  const folder = posix.dirname(repoPathOf(pagePath));

  return posix.normalize(posix.join(folder, path));
}

function isUnder(repoPath, folders) {
  return folders.some((prefix) => {
    return repoPath === prefix || repoPath.startsWith(`${prefix}/`);
  });
}

// The folder a page is served from, from the site's root. The front
// page is served from the root itself, which is "".
function folderOf(repoPath, home) {
  if (repoPath === home) {
    return "";
  }

  return repoPath.replace(MARKDOWN_PATH, "");
}

// The way from one folder of the site to a file or folder in it.
function wayTo(target, fromFolder) {
  const isFolder = target === "" || target.endsWith("/");
  const from = fromFolder === "" ? "." : fromFolder;
  const to = target === "" ? "." : target.replace(/\/$/, "");
  const way = posix.relative(from, to);

  if (!isFolder) {
    return way;
  }

  if (way === "") {
    return "./";
  }

  return `${way}/`;
}

function onGitHub(repoPath, sourceUrl) {
  if (repoPath.endsWith("/")) {
    return sourceUrl.replace(BLOB, TREE) + repoPath;
  }

  return sourceUrl + repoPath;
}

// ---- Rewriting --------------------------------------------------------

function rewriteAddress(address, pagePath, options) {
  if (SCHEME.test(address)) {
    return address;
  }

  if (address.startsWith("/")) {
    return address;
  }

  if (address.startsWith("#")) {
    return address;
  }

  const cut = address.search(/[?#]/);
  const path = cut === -1 ? address : address.slice(0, cut);
  const rest = cut === -1 ? "" : address.slice(cut);

  const repoPath = fromRoot(path, pagePath);
  const pageFolder = folderOf(repoPathOf(pagePath), options.home);

  if (repoPath.startsWith("..")) {
    return address;
  }

  if (isUnder(repoPath, options.keep)) {
    return wayTo(repoPath, pageFolder) + rest;
  }

  if (isUnder(repoPath, options.skip)) {
    return onGitHub(repoPath, options.sourceUrl) + rest;
  }

  if (repoPath === options.home) {
    return wayTo("", pageFolder) + rest;
  }

  if (MARKDOWN_PATH.test(repoPath)) {
    const target = `${folderOf(repoPath, options.home)}/`;

    return wayTo(target, pageFolder) + rest;
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

    if (!Object.hasOwn(ADDRESS_ATTRS, token.type)) {
      continue;
    }

    const attr = ADDRESS_ATTRS[token.type];
    const address = token.attrGet(attr);

    if (address === null) {
      continue;
    }

    token.attrSet(attr, rewriteAddress(address, pagePath, options));
  }
}

// ---- Plugin -----------------------------------------------------------

export default function mdLinks(md, options) {
  md.core.ruler.push("md_links", (state) => {
    const pagePath = state.env?.page?.inputPath ?? "./README.md";

    rewriteTokens(state.tokens, pagePath, options);
  });
}
