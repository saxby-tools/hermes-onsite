#!/usr/bin/env bash
#
# Runs Eleventy from the repo's root, where the Markdown is, with the
# config and the packages that live in this folder. Extra arguments
# pass through: `./build.sh --serve`.

set -euo pipefail

cd "$(dirname "$0")/.."

exec site/node_modules/.bin/eleventy \
  --config=site/eleventy.config.js \
  "$@"
