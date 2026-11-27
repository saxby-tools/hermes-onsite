// Real screenshots, in a row that scrolls sideways, the way Hermes
// Desktop shows a plugin's screenshots on its catalog page.
//
//   <!-- onsite:shots /-->
//
// The block holds nothing, so GitHub and the plugin catalog show
// nothing at all. The site reads the list in
// docs/screenshots/shots.json and draws one card for each entry:
//
//   [
//     {
//       "file": "01-chip.png",
//       "alt": "A chip in the corner of the app reads onsite: laptop.",
//       "caption": "The chip names the computer in use."
//     }
//   ]
//
// `file` is a picture beside the list: .png, .jpg, .jpeg, .webp or
// .gif. `alt` says what the picture shows, for a reader who cannot
// see it. `caption` is the line under it. All three are required.
// The width and height are read from the file.
//
// A short film is a .mp4 or .webm with a `poster`, a picture beside
// it that shows until the reader presses play:
//
//   { "file": "05-run.mp4", "poster": "05-run.png", "alt": "…",
//     "caption": "…" }
//
// An empty list, `[]`, draws nothing: no heading, no row.
//
// Options:
//
// - `heading` is the heading above the row, "Screenshots" unless set.
//   It is written the way a `##` heading is, so the layouts give it a
//   section and a place in "On this page". `false` draws none, for a
//   page that has its own heading above the block.
// - `from` is the folder that holds shots.json and the files, from
//   the repo's root. It must be one that eleventy.config.js copies to
//   the site.
//
// A mistake in the list stops the build and names the entry.

import { existsSync, readFileSync } from "node:fs";
import { basename, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { slugOf } from "../plugins/md-marks.js";
import imageSize from "./image-size.js";
import { fail } from "./shared.js";

// ---- Constants --------------------------------------------------------

const REPO_ROOT = fileURLToPath(new URL("../../", import.meta.url));

const DEFAULT_FOLDER = "docs/screenshots";

const MANIFEST = "shots.json";

const DEFAULT_HEADING = "Screenshots";

const PICTURES = [".png", ".jpg", ".jpeg", ".webp", ".gif"];

// Each film format, and the type a browser is told it is.
const FILMS = {
  ".mp4": "video/mp4",
  ".webm": "video/webm",
};

const REQUIRED = ["file", "alt", "caption"];

// ---- Reading ----------------------------------------------------------

function readManifest(block, folder) {
  const path = resolve(REPO_ROOT, folder, MANIFEST);

  if (!existsSync(path)) {
    fail(block, `${folder}/${MANIFEST} is missing; an empty list is []`);
  }

  let list = null;

  try {
    list = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    fail(block, `${folder}/${MANIFEST} is not valid JSON: ${error.message}`);
  }

  if (!Array.isArray(list)) {
    fail(block, `${folder}/${MANIFEST} must be a list: [ {...}, {...} ]`);
  }

  return list;
}

// ---- Checking ---------------------------------------------------------

function isText(value) {
  if (typeof value !== "string") {
    return false;
  }

  return value.trim() !== "";
}

function checkHeading(block, heading) {
  if (heading === false) {
    return;
  }

  if (isText(heading)) {
    return;
  }

  fail(block, '"heading" must be words, or false for no heading');
}

// A file name and nothing more, so every file sits beside the list.
function checkName(block, where, name) {
  if (basename(name) === name) {
    return;
  }

  fail(block, `${where}: "${name}" must be a file name with no folder`);
}

// The size of a picture beside the list, read from the file itself.
function sizeOf(block, where, folder, name) {
  checkName(block, where, name);

  if (!PICTURES.includes(extname(name).toLowerCase())) {
    fail(block, `${where}: "${name}" must end in ${PICTURES.join(", ")}`);
  }

  const path = resolve(REPO_ROOT, folder, name);

  if (!existsSync(path)) {
    fail(block, `${where}: there is no ${folder}/${name}`);
  }

  const size = imageSize(readFileSync(path));

  if (!size) {
    fail(block, `${where}: ${folder}/${name} is not a picture it can read`);
  }

  return size;
}

// ---- Shots ------------------------------------------------------------

function filmOf(block, where, folder, entry, address) {
  checkName(block, where, entry.file);

  if (!isText(entry.poster)) {
    fail(block, `${where}: a film needs a "poster", a picture beside it`);
  }

  if (!existsSync(resolve(REPO_ROOT, folder, entry.file))) {
    fail(block, `${where}: there is no ${folder}/${entry.file}`);
  }

  return {
    ...sizeOf(block, where, folder, entry.poster),
    src: address(entry.file),
    poster: address(entry.poster),
    type: FILMS[extname(entry.file).toLowerCase()],
    alt: entry.alt.trim(),
    caption: entry.caption.trim(),
  };
}

function pictureOf(block, where, folder, entry, address) {
  return {
    ...sizeOf(block, where, folder, entry.file),
    src: address(entry.file),
    poster: null,
    type: null,
    alt: entry.alt.trim(),
    caption: entry.caption.trim(),
  };
}

function shotOf(block, folder, entry, index, address) {
  const where = `${folder}/${MANIFEST}, entry ${index + 1}`;

  for (const key of REQUIRED) {
    if (!isText(entry?.[key])) {
      fail(block, `${where}: needs "${key}", in words`);
    }
  }

  if (Object.hasOwn(FILMS, extname(entry.file).toLowerCase())) {
    return filmOf(block, where, folder, entry, address);
  }

  return pictureOf(block, where, folder, entry, address);
}

// ---- Component --------------------------------------------------------

export default function shots(block) {
  const folder = block.options.from ?? DEFAULT_FOLDER;
  const heading = block.options.heading ?? DEFAULT_HEADING;
  const root = block.env?.root ?? "";

  checkHeading(block, heading);

  const address = (name) => `${root}${folder}/${encodeURI(name)}`;

  const list = readManifest(block, folder).map((entry, index) => {
    return shotOf(block, folder, entry, index, address);
  });

  return {
    heading,
    id: slugOf(heading || DEFAULT_HEADING),
    label: heading || DEFAULT_HEADING,
    shots: list,
  };
}
