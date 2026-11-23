#!/usr/bin/env bash
#
# Turns the onsite mark and the two pictures into the PNG and ICO
# files the site and the plugin catalog need.
#
# Run make-brand.py first: it draws the SVG files this reads. Then,
# from the repo's root:
#
#   docs/brand/make-icons.sh
#
# Needs rsvg-convert (librsvg) and ImageMagick 7 (`magick`).

set -euo pipefail

# ---- Constants --------------------------------------------------------

BRAND="docs/brand"
STATIC="site/static"

MARK="$BRAND/mark.svg"

# The sizes inside favicon.ico.
ICO_SIZES=(16 32 48)

# ---- Pictures ---------------------------------------------------------

rsvg-convert "$BRAND/banner.svg" -o "$BRAND/banner.png"
rsvg-convert "$BRAND/social.svg" -o "$STATIC/social.png"

# ---- Icons ------------------------------------------------------------

cp "$MARK" "$STATIC/favicon.svg"

rsvg-convert -w 180 -h 180 "$MARK" -o "$STATIC/apple-touch-icon.png"
rsvg-convert -w 192 -h 192 "$MARK" -o "$STATIC/icon-192.png"
rsvg-convert -w 512 -h 512 "$MARK" -o "$STATIC/icon-512.png"

work="$(mktemp -d)"
layers=()

for size in "${ICO_SIZES[@]}"; do
  rsvg-convert -w "$size" -h "$size" "$MARK" -o "$work/$size.png"
  layers+=("$work/$size.png")
done

magick "${layers[@]}" "$STATIC/favicon.ico"

echo "wrote the banner, the share picture and the icons"
