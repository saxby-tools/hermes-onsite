// Builds the onsite site from the same Markdown GitHub shows.
//
// No Markdown file carries front matter, because GitHub would show it
// as a table and the Hermes marketplace would print it. Each page's
// layout, address and title come from here instead: the repo's
// README.md is the front page and uses site/layouts/home.ejs, every
// other page uses site/layouts/page.ejs, `findings.md` becomes
// `findings.html`, and the title is the file's first heading.
//
// Components live in site/components as a pair of files: `name.ejs`
// draws it and `name.js` turns the block into the template's data.
// A comment block such as `<!-- hands:name {...} -->` picks one.
//
// The repo's root is the plugin itself, so everything the site needs
// stays in this folder. Run it from here with `npm run build`; the
// script steps up to the repo root, which is Eleventy's input.

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { pathToFileURL } from "node:url";

import ejs from "ejs";
import commentBlocks from "markdown-it-comment-blocks";

import mdLinks from "./plugins/md-links.js";
import mdMarks from "./plugins/md-marks.js";

// ---- Constants --------------------------------------------------------

const NAMESPACE = "hands";

const SITE_NAME = "onsite";

const HOME_PAGE = "./README.md";

const COMPONENTS_DIR = "site/components";

const SOURCE_URL =
  "https://github.com/saxby-tools/hermes-onsite/blob/build/v1/";

const SITE_URL = "https://saxby-tools.github.io/hermes-onsite/";

const SITE_SUMMARY = "A community plugin for the Hermes agent. Open a " +
  "project folder on your own computer and your always-on Hermes " +
  "works in it, with Hermes' own approval prompts kept on.";

// Copied as they are. The README and the design page point at these.
const PASSTHROUGH = [
  "docs/brand",
];

// Everything that is not a page. Markdown in these folders is code,
// test data or this folder's own notes.
const IGNORES = [
  "site/**",
  "rig/**",
  "tests/**",
  "targets/**",
  "dashboard/**",
  "desktop/**",
  ".claude/**",
  ".rig/**",
  ".venv/**",
  ".pytest_cache/**",
  ".scratch/**",
];

// ---- Components -------------------------------------------------------

async function loadPrepare(name) {
  const path = join(COMPONENTS_DIR, `${name}.js`);

  if (!existsSync(path)) {
    return (block) => block;
  }

  const module = await import(pathToFileURL(path).href);

  return module.default;
}

async function loadComponents() {
  const components = {};

  for (const file of readdirSync(COMPONENTS_DIR)) {
    if (!file.endsWith(".ejs")) {
      continue;
    }

    const name = basename(file, ".ejs");
    const path = join(COMPONENTS_DIR, file);
    const template = ejs.compile(readFileSync(path, "utf8"), {
      filename: path,
    });
    const prepare = await loadPrepare(name);

    components[name] = (block) => template(prepare(block));
  }

  return components;
}

// ---- Page data --------------------------------------------------------

// The first heading, without the Markdown around it.
function titleOf(inputPath) {
  const text = readFileSync(inputPath, "utf8");
  const heading = text.match(/^#\s+(.+)$/m);

  if (!heading) {
    return SITE_NAME;
  }

  return heading[1]
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_]/g, "")
    .trim();
}

function layoutOf(inputPath) {
  if (inputPath === HOME_PAGE) {
    return "home.ejs";
  }

  return "page.ejs";
}

function permalinkOf(page) {
  if (page.inputPath === HOME_PAGE) {
    return "index.html";
  }

  return `${page.filePathStem}.html`;
}

// "../" once per folder, so links work under any base path.
function rootOf(page) {
  if (page.inputPath === HOME_PAGE) {
    return "./";
  }

  const depth = page.filePathStem.split("/").length - 2;

  return depth > 0 ? "../".repeat(depth) : "./";
}

function sourceOf(inputPath) {
  return SOURCE_URL + inputPath.replace(/^\.\//, "");
}

// The page's full address, for share tags; "index.html" is dropped.
function canonicalOf(url) {
  return SITE_URL + url.replace(/^\//, "").replace(/index\.html$/, "");
}

// ---- Config -----------------------------------------------------------

export default async function (eleventyConfig) {
  const components = await loadComponents();

  eleventyConfig.addExtension("ejs", {
    compile: (input, inputPath) => {
      const template = ejs.compile(input, { filename: inputPath });

      return (data) => template(data);
    },
  });

  eleventyConfig.amendLibrary("md", (md) => {
    md.use(commentBlocks, { namespace: NAMESPACE, components });
    md.use(mdLinks, {
      sourceUrl: SOURCE_URL,
      home: HOME_PAGE.replace(/^\.\//, ""),
      keep: PASSTHROUGH,
      skip: IGNORES.map((glob) => glob.replace(/\/\*\*$/, "")),
    });
    md.use(mdMarks);
  });

  for (const path of PASSTHROUGH) {
    eleventyConfig.addPassthroughCopy(path);
  }

  // The stylesheet, the script, fonts, icons and the share image sit
  // at the site's root.
  eleventyConfig.addPassthroughCopy({ "site/static": "/" });

  for (const glob of IGNORES) {
    eleventyConfig.ignores.add(glob);
  }

  eleventyConfig.addGlobalData("siteName", SITE_NAME);
  eleventyConfig.addGlobalData("siteUrl", SITE_URL);
  eleventyConfig.addGlobalData("eleventyComputed", {
    layout: (data) => layoutOf(data.page.inputPath),
    summary: (data) => data.description ?? SITE_SUMMARY,
    canonical: (data) => canonicalOf(data.page.url),
    permalink: (data) => permalinkOf(data.page),
    title: (data) => titleOf(data.page.inputPath),
    root: (data) => rootOf(data.page),
    source: (data) => sourceOf(data.page.inputPath),
  });

  return {
    dir: { input: ".", includes: "site/layouts", output: "site/_site" },
    templateFormats: ["md"],
    markdownTemplateEngine: false,
  };
}
