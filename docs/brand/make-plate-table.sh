#!/usr/bin/env bash
#
# Turns one engraving into the site's hero plate, "At your table".
#
# The picture becomes two colours: pale ink on a blue ground, drawn as
# broken horizontal lines, with a few rows knocked sideways and a few
# yellow blocks. It is the same treatment make-plates.sh gives the
# marble, at three to two and not square.
#
# Usage: make-plate-table.sh <engraving.jpg> <out-dir>
#
# Needs ImageMagick 7 (`magick`). The engraving is not kept in this
# repo. It is in the public domain (CC0), from the Cleveland Museum of
# Art's open-access collection. Use the "print" size, 3400 pixels wide:
#
#   Hendrik Goudt, after Adam Elsheimer. Jupiter and Mercury at the
#   House of Philemon and Baucis, 1613. Engraving. 1943.571
#   https://clevelandart.org/art/1943.571
#
# In Ovid's telling, the two gods travel as strangers and are turned
# away at every door in a town. An old couple takes them in. Mercury,
# who is Hermes, is the young man in the winged hat.

set -euo pipefail

# ---- Constants --------------------------------------------------------

GROUND="#0000f2"
INK="#e9ebff"
SIGNAL="#ffe600"

# The part of the engraving that is kept: the hostess, Mercury and
# Jupiter at the table. "width x height + left + top", in the pixels
# of the 3400 pixel wide file.
CROP="2340x1560+800+830"

# The picture is screened at this size, then doubled, so one dot of
# the screen is two pixels wide in the file.
CELL_WIDTH=840
CELL_HEIGHT=560
SCALE=200%

# The scene is lit by one lamp, so most of it is near black. These
# lift it before it is screened: the black and white points, then
# the mid-tones.
LEVELS="12%,56%"
GAMMA=1.1

# The screen: an 8x8 tile of thresholds, 0 to 255. Each value is
# 60% a four-row line screen and 40% a Bayer dot pattern, so flat
# tones draw as lines and the tones between break into dots. It is
# the tile make-plates.sh uses.
SCREEN_TILE="P2
8 8
255
20 71 33 84 23 74 36 87
135 84 147 96 138 87 151 100
116 167 103 154 119 170 106 157
230 179 218 167 233 182 221 170
25 76 37 88 22 73 34 85
139 88 152 101 136 85 149 98
120 171 108 159 117 168 104 155
235 184 222 171 232 181 219 168"

# Slices knocked sideways: "top height shift", in screen pixels.
SLICES=("40 8 40" "300 18 -22" "372 6 64" "498 10 -30")

# Yellow blocks: "left top width height", in screen pixels.
BLOCKS=("752 40 88 8" "0 300 26 18" "40 372 60 6")

# ---- Arguments --------------------------------------------------------

if [ "$#" -ne 2 ]; then
  echo "usage: make-plate-table.sh <engraving.jpg> <out-dir>" >&2
  exit 64
fi

SOURCE="$1"
OUT_DIR="$2"

WORK_DIR="$(mktemp -d)"

# ---- Steps ------------------------------------------------------------

make_screen() {
  printf '%s\n' "${SCREEN_TILE}" > "${WORK_DIR}/screen.pgm"
}

# Crop to the three figures, lift the dark scene, and add a little
# contrast so the lamp-lit faces and cloth carry the picture.
make_gray() {
  magick "${SOURCE}" \
    -crop "${CROP}" \
    +repage \
    -colorspace Gray \
    -resize "${CELL_WIDTH}x${CELL_HEIGHT}!" \
    -level "${LEVELS}" \
    -gamma "${GAMMA}" \
    -sigmoidal-contrast 4,50% \
    "${WORK_DIR}/gray.png"
}

# White where the picture is lighter than the screen row under it.
screen_picture() {
  magick "${WORK_DIR}/gray.png" \
    \( -size "${CELL_WIDTH}x${CELL_HEIGHT}" "tile:${WORK_DIR}/screen.pgm" \) \
    -compose MinusSrc \
    -composite \
    -threshold 0 \
    "${WORK_DIR}/bits.png"
}

# Cut each slice out, roll it sideways and lay it back.
shift_slices() {
  local file="${WORK_DIR}/bits.png"
  local slice

  for slice in "${SLICES[@]}"; do
    read -r top height offset <<< "${slice}"

    magick "${file}" \
      \( +clone \
        -crop "${CELL_WIDTH}x${height}+0+${top}" \
        +repage \
        -roll "+${offset}+0" \
      \) \
      -gravity NorthWest \
      -geometry "+0+${top}" \
      -compose Over \
      -composite \
      "${file}"
  done
}

# Two colours for the bits, yellow blocks on top, then doubled.
colour_picture() {
  local draw=()
  local block

  for block in "${BLOCKS[@]}"; do
    read -r left top width height <<< "${block}"

    draw+=(
      -draw
      "rectangle ${left},${top} $((left + width - 1)),$((top + height - 1))"
    )
  done

  magick "${WORK_DIR}/bits.png" \
    -type TrueColor \
    -colorspace sRGB \
    -fill "${GROUND}" \
    -opaque black \
    -fill "${INK}" \
    -opaque white \
    -fill "${SIGNAL}" \
    "${draw[@]}" \
    -filter point \
    -resize "${SCALE}" \
    -strip \
    -define png:compression-level=9 \
    "${OUT_DIR}/plate-table.png"
}

# ---- Run --------------------------------------------------------------

mkdir -p "${OUT_DIR}"

make_screen
make_gray
screen_picture
shift_slices
colour_picture

echo "wrote ${OUT_DIR}/plate-table.png"
