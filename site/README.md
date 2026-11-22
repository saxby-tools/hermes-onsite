#  The hermes-hands site

Eleventy builds <https://saxby-tools.github.io/hermes-hands/> from
the same Markdown files GitHub shows. One rule shapes everything
here: **a Markdown file must read as plain Markdown on GitHub.** The
Hermes plugin catalog shows the repo's README too, and drops any raw
HTML in it. So anything only the site needs lives in HTML comments,
which both of them hide.

The setup is adapted from the
[Saxby lab](https://github.com/saxby-tools/lab)'s site.

##  Why it lives in this folder

The repo's root is the plugin. A user installs it by cloning the repo
into a Hermes plugins folder, so the root stays free of site tooling.
The config, the packages and the build output are all in `site/`.
The one file outside it is `.github/workflows/pages.yml`, which
GitHub only reads from there.

##  Build it

Run these from `site/`:

```bash
npm ci
```

```bash
npm run serve
```

`npm test` runs the plugin tests and checks the design page against
the stylesheet. `npm run build` writes the site to `site/_site/`.
Pull requests build it in CI, and pushes to `build/v1` or `main`
deploy it.

It costs four dev dependencies, pinned exact: Eleventy, EJS,
markdown-it and markdown-it-comment-blocks. With their own
dependencies that is 115 packages on disk. None of them ships to a
reader's browser or to a plugin user.

##  Pages

| Markdown | Page | Layout |
| --- | --- | --- |
| `README.md` | `index.html` | `home.ejs` |
| `DECISIONS.md` | `DECISIONS.html` | `page.ejs` |
| `docs/*.md` | `docs/*.html` | `page.ejs` |

The README is the front page. `home.ejs` puts everything before its
first `##` heading in the hero, and builds the "On this page" list
from the `##` headings. That list is the one thing the site adds that
the Markdown does not hold.

Don't add front matter; GitHub shows it as a table. The layout,
address and title come from `eleventy.config.js`, and the title is
the file's first heading.

Links between Markdown files are rewritten to point at the pages.
A relative link to anything else in the repo, such as `rig/results/`,
points at that file on GitHub.

##  What plain Markdown gets for free

`plugins/md-marks.js` draws these with no comment block, from how the
pages are already written:

| Write | The site draws |
| --- | --- |
| Any heading | The same anchor GitHub gives it |
| A paragraph that opens with a bold "Status:" | The status box |
| A table | A table that scrolls sideways on a phone |
| A cell that says only "Pass" or "Not run" | A chip |
| A fenced code block | A framed block with its language as a label |
| A `bash` or `yaml` block | The same, with a Copy button |

##  Comment blocks

A comment block puts a component around ordinary Markdown:

```markdown
<!-- hands:notice {"kind": "limit"} -->

No sandbox. An SSH target can do anything its user can.

<!-- /hands:notice -->
```

On GitHub, a reader sees the paragraph. On the site, it is boxed and
labelled "Limit".

- `<!-- hands:name {...} -->` opens a block. The options are strict
  JSON: quote every key.
- `<!-- /hands:name -->` closes it.
- `<!-- hands:name {...} /-->` is a block with no content.
- Put each comment on its own line.

A mistake stops the build and names the file and line. The
[markdown-it-comment-blocks][comment-blocks] package reads the
blocks. Each block becomes one token with the tokens inside it in
`meta.children`, so a plugin that walks the tokens has to look there
too.

[comment-blocks]: https://github.com/nikolowry/markdown-it-comment-blocks

##  Components

Each component's own file lists its options at the top.
`docs/design.md` uses every one of them.

| Component | Wraps | Draws |
| --- | --- | --- |
| `notice` | Paragraphs | A callout: `note`, `limit` or `untested` |
| `chips` | A list | A row of status chips |
| `diagram` | A text drawing | The "how a tool call travels" drawing |
| `marks` | A table | Each brand file on its own ground |
| `swatches` | A table | Colour swatches, light and dark, above the table |
| `specimen` | A table | A sample in each type class |
| `scale` | A table | The spacing scale as bars |
| `motion` | Nothing | Two bars that move at the two speeds |

To add one, add `name.ejs` to `components/` to draw it, and `name.js`
to turn the block into the template's data if it needs more than the
block itself.

##  Styles, script and icons

Every layout includes `layouts/head.ejs`: the viewport, the share
tags, the icons, and the site's one stylesheet and one script,
`static/hands.css` and `static/hands.js`. There is no bundler.
`static/` is copied to the site's root as it is. Nothing loads from
another origin: the two fonts are self-hosted, with their licenses
beside them.

The tokens are at the top of `hands.css`. `design.test.js` fails if a
token there is missing from `docs/design.md`, or a colour differs.

Every page works with the script off. The script adds the Copy
buttons and the Theme button.

##  Brand files

The mark, the wordmark, the lockup and the plates are in
`docs/brand/`, because the README and the plugin catalog point at
them there. Three pictures are made from sources in that folder:

```bash
rsvg-convert docs/brand/banner.svg -o docs/brand/banner.png
```

```bash
rsvg-convert docs/brand/social.svg -o site/static/social.png
```

The plates come from two museum photos, by way of
`docs/brand/make-plates.sh`. The icons in `static/` are
`docs/brand/mark.svg` at 16, 32, 48, 180, 192 and 512 pixels.

##  Files

```text
eleventy.config.js      layout, addresses, titles, copying
build.sh                runs Eleventy from the repo's root
design.test.js          the design page against the stylesheet
plugins/md-links.js     links between pages and out to GitHub
plugins/md-marks.js     anchors, status, tables, chips, code blocks
components/             a .ejs, and often a .js, per component
layouts/home.ejs        the front page, from the repo's README.md
layouts/page.ejs        the page around every other Markdown file
layouts/head.ejs        the shared head, in every layout
layouts/top.ejs         the masthead, in every layout
layouts/foot.ejs        the footer, in every layout
static/hands.css        the one stylesheet
static/hands.js         the one script
static/                 copied to the site's root as is
```
