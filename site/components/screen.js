// A drawing of one screen of the app, in a window frame, for guides.
//
//   <!-- onsite:screen {"shows": "chip"} -->
//
//   The bottom right corner of Hermes Desktop. A chip reads
//   "onsite: laptop".
//
//   <!-- /onsite:screen -->
//
// `shows` names a drawing in site/components/screens/. `caption` is
// optional. On GitHub the reader sees only the paragraph, so write it
// to describe the screen in words. On the site that paragraph sits
// under the drawing, folded away, for anyone who cannot see it.
//
// The drawings are simplified on purpose. Every word in them is a
// word the app really shows; onsite/desktop/plugin.js is the source.

import { fail } from "./shared.js";

// ---- Constants --------------------------------------------------------

const DEFAULT_CAPTION =
  "Drawing of the screen. Yours will look a little different.";

// Each drawing, and the name in its window's title bar.
const SCREENS = {
  "chip": "Hermes Desktop",
  "chip-states": "The chip, in each state",
  "pane": "Hermes Desktop: the onsite pane",
  "approval": "Hermes Desktop",
  "run-card": "Hermes Desktop",
  "machines": "Hermes Desktop: Settings",
  "machine-form": "Hermes Desktop: Settings",
};

// ---- Screen -----------------------------------------------------------

export default function screen(block) {
  const shows = block.options.shows;

  if (!Object.hasOwn(SCREENS, shows)) {
    const known = Object.keys(SCREENS).join(", ");

    fail(block, `"shows" must be one of ${known}, not "${shows}"`);
  }

  return {
    shows,
    app: SCREENS[shows],
    caption: block.options.caption ?? DEFAULT_CAPTION,
    html: block.html,
  };
}
