"""Draws the onsite brand files from the two fonts and the plate.

Every letter is written out as a shape, so the files need no font
installed. Run it from the repo's root:

    uv run --no-project --with fonttools --with brotli \
        --with uharfbuzz python docs/brand/make-brand.py

It writes these files into docs/brand/:

    mark.svg            "On, lit" on its blue tile, for any ground
    mark-blue.svg       the mark in one colour, for light grounds
    mark-pale.svg       the mark in one colour, for blue and dark
    wordmark.svg        the word, o lit, for blue and dark grounds
    wordmark-paper.svg  the word, o in blue, for pale grounds
    wordmark-blue.svg   the word in one colour, for light grounds
    wordmark-pale.svg   the word in one colour, for blue and dark
    banner.svg          the plugin catalog's picture, 1200 by 600
    social.svg          the picture shown when a page is shared

make-icons.sh then turns the mark and the two pictures into the PNG
and ICO files the site and the catalog need.
"""

from __future__ import annotations

import io

from pathlib import Path

import uharfbuzz as hb

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont


# ---- Constants --------------------------------------------------------

BRAND = Path(__file__).parent

FONTS = BRAND.parent.parent / "site" / "static" / "fonts"

MONO_FILE = FONTS / "geist-mono-latin-500-normal.woff2"

DISPLAY_FILE = FONTS / "oswald-latin-wght-normal.woff2"

PLATE_FILE = "plate-head.png"

# Colours, from site/static/onsite.css.
FIELD = "#0000f2"
PAPER = "#f2f2f2"
PALE = "#f2f2f2"
SIGNAL = "#ffe600"
NAVY = "#06066b"

# The mark: a tile of 64 units. The n is 28.5 units tall. The gap
# between the two letters is half of what the font sets.
TILE = 64
MARK_N_HEIGHT = 28.5
MARK_GAP_SHARE = 0.5

# What the site's stylesheet and footer use to draw the same mark in
# live type. This script fails if its own numbers drift from them.
SITE_MARK_SIZE = 52.39
SITE_MARK_BASELINE = 46.25
SITE_MARK_O_X = 3.0
SITE_MARK_N_X = 30.92
SITE_MARK_SLACK = 0.1

WORD = "onsite"
WORD_SIZE = 40

BANNER_WIDTH = 1200
BANNER_HEIGHT = 600
SOCIAL_HEIGHT = 630

HEADLINE = ["OPEN A FOLDER.", "HERMES WORKS", "THERE."]
HEADLINE_SIZE = 84
HEADLINE_LEADING = 94
HEADLINE_WEIGHT = 300
HEADLINE_TRACK = 1

LABEL = "COMMUNITY PLUGIN"
LABEL_SIZE = 24
LABEL_WEIGHT = 500
LABEL_TRACK = 3

MARGIN = 64

LICENSE_NOTE = (
    "\n\n    The letters are outlines of Geist Mono Medium, which is"
    "\n    under the SIL Open Font License 1.1."
)


# ---- Fonts ------------------------------------------------------------

def load_font(path: Path, weight: int | None = None) -> hb.Font:
    """Open a WOFF2 file for shaping, at one weight if it has many."""
    packed = TTFont(path)
    packed.flavor = None

    plain = io.BytesIO()
    packed.save(plain)

    font = hb.Font(hb.Face(hb.Blob(plain.getvalue())))

    if weight is not None:
        font.set_variations({"wght": weight})

    return font


def number(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")

    if text == "-0":
        return "0"

    return text


def shape(font: hb.Font, text: str) -> list[tuple[int, float, float]]:
    """Each glyph's id, where it starts and how far it advances."""
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()

    hb.shape(font, buffer)

    glyphs = []
    cursor = 0.0

    for info, position in zip(buffer.glyph_infos, buffer.glyph_positions):
        glyphs.append((
            info.codepoint,
            cursor + position.x_offset,
            position.x_advance,
        ))
        cursor += position.x_advance

    return glyphs


def draw(
    font: hb.Font,
    text: str,
    size: float,
    x: float,
    baseline: float,
    track: float = 0.0,
) -> tuple[str, tuple[float, float, float, float], float]:
    """Text as one SVG path, with its ink box and its full advance.

    The ink box is (left, top, right, bottom) in the picture's own
    units, where y grows downward.
    """
    scale = size / font.face.upem
    path = SVGPathPen(None, ntos=number)
    bounds = BoundsPen(None)
    extra = 0.0
    advance = 0.0

    for glyph, start, step in shape(font, text):
        place = (scale, 0, 0, -scale, x + start * scale + extra, baseline)

        font.draw_glyph_with_pen(glyph, TransformPen(path, place))
        font.draw_glyph_with_pen(glyph, TransformPen(bounds, place))

        advance = (start + step) * scale + extra
        extra += track

    left, top, right, bottom = bounds.bounds

    return path.getCommands(), (left, top, right, bottom), advance


# ---- Writing ----------------------------------------------------------

def svg(
    name: str,
    width: float,
    height: float,
    title: str,
    note: str,
    body: str,
    sized: bool = False,
) -> None:
    size = ""

    if sized:
        size = f'\n  width="{number(width)}"\n  height="{number(height)}"'

    text = f"""<svg
  xmlns="http://www.w3.org/2000/svg"{size}
  viewBox="0 0 {number(width)} {number(height)}"
  role="img"
>
  <title>{title}</title>
  <!--
    {note}

    Made by docs/brand/make-brand.py. Do not edit by hand.
  -->
{body}</svg>
"""

    (BRAND / name).write_text(text)
    print(f"wrote docs/brand/{name}")


def shape_tag(path: str, fill: str) -> str:
    return f'  <path fill="{fill}" d="{path}" />\n'


# ---- The mark ---------------------------------------------------------

def mark_letters(mono: hb.Font) -> tuple[str, str]:
    """The o and the n, placed on the tile.

    Both letters share one size and one baseline. The size makes the
    n 28.5 units tall. The pair is centred across the tile, and
    centred up and down on the n, which has a flat top and foot.
    """
    probe = 1000.0
    _, n_box, _ = draw(mono, "n", probe, 0, 0)
    size = MARK_N_HEIGHT * probe / (n_box[3] - n_box[1])

    _, o_box, o_advance = draw(mono, "o", size, 0, 0)
    _, n_box, _ = draw(mono, "n", size, 0, 0)

    natural_gap = (o_advance - o_box[2]) + n_box[0]
    gap = natural_gap * MARK_GAP_SHARE

    o_width = o_box[2] - o_box[0]
    n_width = n_box[2] - n_box[0]
    start = (TILE - (o_width + gap + n_width)) / 2

    o_x = start - o_box[0]
    n_x = start + o_width + gap - n_box[0]
    baseline = TILE / 2 + MARK_N_HEIGHT / 2

    check_site_numbers(size, baseline, o_x, n_x)

    o_path, _, _ = draw(mono, "o", size, o_x, baseline)
    n_path, _, _ = draw(mono, "n", size, n_x, baseline)

    return o_path, n_path


def check_site_numbers(
    size: float,
    baseline: float,
    o_x: float,
    n_x: float,
) -> None:
    pairs = [
        ("size", size, SITE_MARK_SIZE),
        ("baseline", baseline, SITE_MARK_BASELINE),
        ("o x", o_x, SITE_MARK_O_X),
        ("n x", n_x, SITE_MARK_N_X),
    ]

    for label, here, there in pairs:
        if abs(here - there) > SITE_MARK_SLACK:
            raise SystemExit(
                f"The mark's {label} is {here:.2f} here and {there} in "
                "site/static/onsite.css and site/layouts/foot.ejs. "
                "Change both or neither."
            )


def write_marks(mono: hb.Font) -> tuple[str, str]:
    o_path, n_path = mark_letters(mono)
    tile = f'  <rect width="{TILE}" height="{TILE}" fill="{FIELD}" />\n'

    svg(
        "mark.svg", TILE, TILE, "onsite",
        'The onsite mark, "On, lit": the letters o and n on a blue\n'
        "    tile, with the o lit in yellow. Full colour, on its own\n"
        "    tile, for any ground." + LICENSE_NOTE,
        tile + shape_tag(o_path, SIGNAL) + shape_tag(n_path, PALE),
    )
    svg(
        "mark-blue.svg", TILE, TILE, "onsite",
        "The onsite mark in one colour, for light grounds. Nothing is\n"
        "    lit in one colour." + LICENSE_NOTE,
        shape_tag(o_path + n_path, FIELD),
    )
    svg(
        "mark-pale.svg", TILE, TILE, "onsite",
        "The onsite mark in one colour, for blue and dark grounds.\n"
        "    Nothing is lit in one colour." + LICENSE_NOTE,
        shape_tag(o_path + n_path, PALE),
    )

    return o_path, n_path


# ---- The wordmark -----------------------------------------------------

def word_letters(
    mono: hb.Font,
    size: float,
    x: float,
    baseline: float,
) -> tuple[str, str, tuple[float, float, float, float]]:
    """The lit o, the rest of the word, and the whole word's ink box."""
    first, first_box, advance = draw(mono, WORD[0], size, x, baseline)
    rest, rest_box, _ = draw(mono, WORD[1:], size, x + advance, baseline)

    box = (
        min(first_box[0], rest_box[0]),
        min(first_box[1], rest_box[1]),
        max(first_box[2], rest_box[2]),
        max(first_box[3], rest_box[3]),
    )

    return first, rest, box


def write_wordmarks(mono: hb.Font) -> None:
    # Draw once to learn the ink box, then again with that box's top
    # left corner at the picture's corner.
    _, _, box = word_letters(mono, WORD_SIZE, 0, 0)
    first, rest, box = word_letters(mono, WORD_SIZE, -box[0], -box[1])

    width = box[2]
    height = box[3]

    versions = [
        ("wordmark.svg", SIGNAL, PALE,
         "The onsite wordmark, for blue and dark grounds. The o is lit\n"
         "    in the signal yellow."),
        ("wordmark-paper.svg", FIELD, NAVY,
         "The onsite wordmark, for pale grounds. Yellow cannot be read\n"
         "    there, so the o is lit in blue and the rest is navy."),
        ("wordmark-blue.svg", FIELD, FIELD,
         "The onsite wordmark in one colour, for light grounds."),
        ("wordmark-pale.svg", PALE, PALE,
         "The onsite wordmark in one colour, for blue and dark grounds."),
    ]

    for name, lit, plain, note in versions:
        svg(
            name, width, height, "onsite",
            note + LICENSE_NOTE,
            shape_tag(first, lit) + shape_tag(rest, plain),
        )


# ---- The pictures -----------------------------------------------------

def pillar(left: float, top: float, width: float, height: float) -> str:
    """The herm's pillar, drawn as the same broken lines as the plate."""
    rows = ""
    row = top

    while row < top + height:
        rows += (
            f'  <rect x="{number(left)}" y="{number(row)}" '
            f'width="{number(width)}" height="6" fill="{FIELD}" />\n'
        )
        row += 12

    return rows


def picture(name: str, height: int, note: str, fonts: dict) -> None:
    """The herm on its spot, on paper, with the promise beside it.

    The plate is pale ink on a blue ground. Its red channel is 0 on
    the ground and near 1 on the ink, so the filter turns red into
    alpha and paints the ink blue. That lets it sit on paper.
    """
    drop = height - BANNER_HEIGHT
    floor = 536 + drop
    body = (
        f'  <rect width="{BANNER_WIDTH}" height="{height}" '
        f'fill="{PAPER}" />\n'
    )

    body += (
        '  <defs>\n'
        '    <filter id="ink" color-interpolation-filters="sRGB"\n'
        '      x="0" y="0" width="1" height="1">\n'
        '      <feColorMatrix type="matrix"\n'
        '        values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0.949  1 0 0 0 0" />\n'
        '    </filter>\n'
        '  </defs>\n'
    )

    # Two knocked-out rows at the left edge, as on the plates.
    body += f'  <rect x="0" y="150" width="34" height="12" fill="{FIELD}" />\n'
    body += (
        f'  <rect x="0" y="{470 + drop}" width="20" height="26" '
        f'fill="{FIELD}" opacity="0.5" />\n'
    )

    label, _, _ = draw(
        fonts["label"], LABEL, LABEL_SIZE, MARGIN, 92, LABEL_TRACK,
    )
    body += shape_tag(label, FIELD)

    body += pillar(806, floor - 112, 212, 112)
    body += (
        f'  <image href="{PLATE_FILE}" x="660" y="{-20 + drop}" '
        'width="500" height="500" filter="url(#ink)" />\n'
    )
    body += (
        f'  <rect x="610" y="{floor}" width="590" height="18" '
        f'fill="{SIGNAL}" />\n'
    )
    body += (
        f'  <rect x="610" y="{floor + 18}" width="590" height="4" '
        f'fill="{FIELD}" />\n'
    )

    for index, line in enumerate(HEADLINE):
        text, _, _ = draw(
            fonts["headline"], line, HEADLINE_SIZE, MARGIN,
            250 + drop / 2 + index * HEADLINE_LEADING, HEADLINE_TRACK,
        )
        body += shape_tag(text, FIELD)

    first, rest, _ = word_letters(fonts["mono"], WORD_SIZE, MARGIN, floor)
    body += shape_tag(first, FIELD) + shape_tag(rest, NAVY)

    svg(name, BANNER_WIDTH, height, "onsite", note, body, sized=True)


def write_pictures(fonts: dict) -> None:
    picture(
        "banner.svg", BANNER_HEIGHT,
        "The source of banner.png, the picture the Hermes plugin\n"
        "    catalog shows for onsite. The catalog wants 2 to 1 and lays\n"
        "    the listing's title over the left side, so the herm stands\n"
        "    on the right. The ground is paper, because the catalog draws\n"
        "    a blue halftone for entries with no picture.",
        fonts,
    )
    picture(
        "social.svg", SOCIAL_HEIGHT,
        "The source of site/static/social.png, the picture shown when\n"
        "    a page of the site is shared.",
        fonts,
    )


# ---- Run --------------------------------------------------------------

def main() -> None:
    fonts = {
        "mono": load_font(MONO_FILE),
        "headline": load_font(DISPLAY_FILE, HEADLINE_WEIGHT),
        "label": load_font(DISPLAY_FILE, LABEL_WEIGHT),
    }

    write_marks(fonts["mono"])
    write_wordmarks(fonts["mono"])
    write_pictures(fonts)


main()
