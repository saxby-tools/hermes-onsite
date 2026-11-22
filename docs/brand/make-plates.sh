#!/usr/bin/env bash
#
# Turns the two Met Museum photos into the site's blue plates.
#
# Each photo becomes a picture in two colours: pale ink on a blue
# ground, drawn as broken horizontal lines, with a few rows knocked
# sideways and a few yellow blocks, so it reads as a damaged scan of
# an old plate.
#
# Usage: make-plates.sh <head.jpg> <hand.jpg> <out-dir>
#
# Needs ImageMagick 7 (`magick`). The photos are not kept in this
# repo. Both are public domain (CC0), from The Metropolitan Museum of
# Art's open-access collection:
#
#   Marble head from a herm, Roman, 1st to 2nd century. 13.231.2
#   https://www.metmuseum.org/art/collection/search/248804
#
#   Marble right hand and wrist with a supporting strut, Roman, 1st
#   or 2nd century. 28.57.14
#   https://www.metmuseum.org/art/collection/search/252966

set -euo pipefail

# ---- Constants --------------------------------------------------------

GROUND="#0000f2"
INK="#e9ebff"
SIGNAL="#ffe600"

# The picture is screened at CELL pixels, then doubled, so one dot of
# the screen is two pixels wide in the file.
CELL=560
SCALE=200%

# The screen: an 8x8 tile of thresholds, 0 to 255. Each value is
# 60% a four-row line screen and 40% a Bayer dot pattern, so flat
# tones draw as lines and the tones between break into dots.
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
HEAD_SLICES=("118 10 34" "396 6 -58" "404 26 16" "486 12 -22")
HAND_SLICES=("150 8 46" "318 20 -18" "392 6 70")

# Yellow blocks: "left top width height", in screen pixels.
HEAD_BLOCKS=("452 396 64 6" "0 118 22 10" "506 486 54 4")
HAND_BLOCKS=("474 150 86 8" "20 392 44 6" "0 318 12 20")

# ---- Arguments --------------------------------------------------------

HEAD_SRC="$1"
HAND_SRC="$2"
OUT_DIR="$3"

WORK_DIR="$(mktemp -d)"

# ---- Steps ------------------------------------------------------------

make_screen() {
  printf '%s\n' "${SCREEN_TILE}" > "${WORK_DIR}/screen.pgm"
}

# The head is already lit from one side against a dark wall. It only
# needs a square crop and a little more contrast.
make_head_gray() {
  magick "${HEAD_SRC}" \
    -colorspace Gray \
    -resize "${CELL}x${CELL}^" \
    -gravity center \
    -extent "${CELL}x${CELL}" \
    -sigmoidal-contrast 5,45% \
    "${WORK_DIR}/head-gray.png"
}

# The hand sits on a grey backdrop. Marble is warm and the backdrop
# is not, so colour saturation cuts the hand out.
make_hand_gray() {
  magick "${HAND_SRC}" \
    -resize 1120x \
    -colorspace HSL \
    -channel 1 \
    -separate \
    +channel \
    -blur 0x2 \
    -threshold 9% \
    -morphology Close Disk:9 \
    -morphology Open Disk:3 \
    -blur 0x1.2 \
    "${WORK_DIR}/hand-mask.png"

  magick "${HAND_SRC}" \
    -resize 1120x \
    -colorspace Gray \
    -blur 0x1.5 \
    -level 30%,93% \
    -sigmoidal-contrast 4,55% \
    "${WORK_DIR}/hand-tone.png"

  magick "${WORK_DIR}/hand-tone.png" "${WORK_DIR}/hand-mask.png" \
    -compose Multiply \
    -composite \
    -fuzz 4% \
    -trim \
    +repage \
    -resize "$((CELL - 20))x" \
    -compose Over \
    -gravity center \
    -background black \
    -extent "${CELL}x${CELL}" \
    "${WORK_DIR}/hand-gray.png"
}

# White where the picture is lighter than the screen row under it.
screen_picture() {
  local name="$1"

  magick "${WORK_DIR}/${name}-gray.png" \
    \( -size "${CELL}x${CELL}" "tile:${WORK_DIR}/screen.pgm" \) \
    -compose MinusSrc \
    -composite \
    -threshold 0 \
    "${WORK_DIR}/${name}-bits.png"
}

# Cut each slice out, roll it sideways and lay it back.
shift_slices() {
  local name="$1"
  shift

  local file="${WORK_DIR}/${name}-bits.png"
  local slice

  for slice in "$@"; do
    read -r top height offset <<< "${slice}"

    magick "${file}" \
      \( +clone \
        -crop "${CELL}x${height}+0+${top}" \
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
  local name="$1"
  shift

  local draw=()
  local block

  for block in "$@"; do
    read -r left top width height <<< "${block}"

    draw+=(
      -draw
      "rectangle ${left},${top} $((left + width - 1)),$((top + height - 1))"
    )
  done

  magick "${WORK_DIR}/${name}-bits.png" \
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
    "${OUT_DIR}/plate-${name}.png"
}

# ---- Run --------------------------------------------------------------

mkdir -p "${OUT_DIR}"

make_screen

make_head_gray
make_hand_gray

screen_picture head
screen_picture hand

shift_slices head "${HEAD_SLICES[@]}"
shift_slices hand "${HAND_SLICES[@]}"

colour_picture head "${HEAD_BLOCKS[@]}"
colour_picture hand "${HAND_BLOCKS[@]}"

rm -r "${WORK_DIR}"
