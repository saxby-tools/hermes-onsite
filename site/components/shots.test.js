// Tests for the screenshot slideshow: what it draws from a list of
// shots, and that none of it reaches a reader on GitHub.
//
// Each test writes its own list and pictures to a folder that is
// thrown away afterwards, so no picture has to live in the repo. A
// picture here is only the head of a PNG, which is all the component
// reads.

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import ejs from "ejs";
import MarkdownIt from "markdown-it";

import shots from "./shots.js";

// ---- Constants --------------------------------------------------------

const TEMPLATE_PATH = new URL("./shots.ejs", import.meta.url);

const README_PATH = new URL("../../README.md", import.meta.url);

const GUIDE_PATH = new URL("../../docs/guide.md", import.meta.url);

const DESIGN_PATH = new URL("../../docs/design.md", import.meta.url);

const PNG_MAGIC = "89504e470d0a1a0a";

const COMMENT = /<!--[\s\S]*?-->/g;

const MARKER = /^<!-- onsite:shots (\{.*\} )?\/-->$/m;

// Anything only the slideshow writes.
const SLIDESHOW = /data-shots|class="shots|<dialog|<video/;

const THREE_SHOTS = [
  {
    file: "01-chip.png",
    alt: "A chip in the corner reads onsite: laptop.",
    caption: "The chip names the computer in use.",
  },
  {
    file: "02-pane.png",
    alt: "A pane lists two computers.",
    caption: "The pane lists every computer.",
  },
  {
    file: "03-tall.png",
    alt: "A tall, narrow window.",
    caption: 'A caption with a "quote" & a mark.',
  },
];

// ---- Helpers ----------------------------------------------------------

const template = ejs.compile(readFileSync(TEMPLATE_PATH, "utf8"));

function pngOf(width, height) {
  const head = Buffer.alloc(24);

  head.write(PNG_MAGIC, "hex");
  head.writeUInt32BE(width, 16);
  head.writeUInt32BE(height, 20);

  return head;
}

// A folder that holds a list and a picture for each name, gone when
// the test ends.
function folderOf(context, list, pictures) {
  const folder = mkdtempSync(join(tmpdir(), "onsite-shots-"));

  context.after(() => rmSync(folder, { recursive: true }));

  writeFileSync(join(folder, "shots.json"), JSON.stringify(list));

  for (const name of pictures) {
    writeFileSync(join(folder, name), pngOf(2560, 1600));
  }

  return folder;
}

// What the site draws for `<!-- onsite:shots {...} /-->`.
function draw(options) {
  const block = {
    name: "shots",
    line: 1,
    options,
    env: { root: "./", page: { inputPath: "./README.md" } },
  };

  return template(shots(block));
}

function count(html, pattern) {
  return [...html.matchAll(pattern)].length;
}

// GitHub renders the Markdown and drops every HTML comment.
function githubView(path) {
  const markdown = readFileSync(path, "utf8");

  return new MarkdownIt({ html: true })
    .render(markdown)
    .replace(COMMENT, "");
}

// ---- Tests ------------------------------------------------------------

test("an empty list draws nothing at all", (context) => {
  const folder = folderOf(context, [], []);

  assert.equal(draw({ from: folder }), "");
});

test("three shots draw three cards, each with alt text and a caption",
  (context) => {
    const names = THREE_SHOTS.map((shot) => shot.file);
    const folder = folderOf(context, THREE_SHOTS, names);
    const html = draw({ from: folder });

    assert.equal(count(html, /<li class="shots__card">/g), 3);
    assert.equal(count(html, /<img\s+class="shots__card-media"/g), 3);
    assert.equal(count(html, /width="2560"\s+height="1600"/g), 3);
    assert.equal(count(html, /loading="lazy"/g), 3);

    assert.match(html, /alt="A chip in the corner reads onsite: laptop\."/);
    assert.match(html, /alt="A pane lists two computers\."/);
    assert.match(html, /alt="A tall, narrow window\."/);

    assert.match(html, />The chip names the computer in use\.<\/figcaption>/);
    assert.match(html, />The pane lists every computer\.<\/figcaption>/);
    assert.match(html, /A caption with a &#34;quote&#34; &amp; a mark\./);
  });

test("the heading is one the layouts read as a section", (context) => {
  const folder = folderOf(context, THREE_SHOTS.slice(0, 1), ["01-chip.png"]);

  assert.match(
    draw({ from: folder }),
    /^<h2 id="screenshots">Screenshots<\/h2>\n<div class="shots"/,
  );
  assert.match(
    draw({ from: folder, heading: "The real app" }),
    /^<h2 id="the-real-app">The real app<\/h2>/,
  );
  assert.match(
    draw({ from: folder, heading: false }),
    /^<div class="shots" role="group" aria-label="Screenshots"/,
  );
});

test("a picture links to its own file, for a page with no script",
  (context) => {
    const folder = folderOf(context, THREE_SHOTS.slice(0, 1), ["01-chip.png"]);
    const html = draw({ from: folder });

    assert.match(html, /<a class="shots__card-open" href="[^"]*01-chip\.png"/);
    assert.equal(count(html, /<button[^>]*\shidden\s/g), 2);
  });

test("a film shows its poster and loads nothing until played",
  (context) => {
    const film = {
      file: "04-run.mp4",
      poster: "04-run.png",
      alt: "A command runs and passes.",
      caption: "A run card, start to finish.",
    };
    const folder = folderOf(context, [film], ["04-run.mp4", "04-run.png"]);
    const html = draw({ from: folder });

    assert.match(html, /<video[^>]*\scontrols\s[^>]*preload="none"/);
    assert.match(html, /poster="[^"]*04-run\.png"/);
    assert.match(html, /<source src="[^"]*04-run\.mp4" type="video\/mp4">/);
    assert.match(html, /aria-label="A command runs and passes\."/);
    assert.doesNotMatch(html, /autoplay/);
  });

test("a mistake in the list stops the build and names the entry",
  (context) => {
    const noAlt = { file: "01-chip.png", caption: "No alt text." };
    const noFile = { ...THREE_SHOTS[0], file: "missing.png" };
    const noPoster = { ...THREE_SHOTS[0], file: "04-run.mp4" };
    const notPicture = { ...THREE_SHOTS[0], file: "shots.json" };

    for (const [entry, message] of [
      [noAlt, /entry 1: needs "alt"/],
      [noFile, /entry 1: there is no .*missing\.png/],
      [noPoster, /entry 1: a film needs a "poster"/],
      [notPicture, /entry 1: "shots\.json" must end in \.png/],
    ]) {
      const folder = folderOf(context, [entry], ["01-chip.png", "04-run.mp4"]);

      assert.throws(() => draw({ from: folder }), message);
    }
  });

test("the repo's own list draws without a mistake", () => {
  assert.doesNotThrow(() => draw({}));
});

test("GitHub's view of each page with the block has no slideshow", () => {
  for (const path of [README_PATH, GUIDE_PATH, DESIGN_PATH]) {
    assert.match(readFileSync(path, "utf8"), MARKER);
    assert.doesNotMatch(githubView(path), SLIDESHOW);
  }
});
