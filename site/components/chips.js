// A row of status chips, from a list.
//
//   <!-- onsite:chips -->
//
//   - Pass
//   - Not run
//   - v0.1.0
//
//   <!-- /onsite:chips -->
//
// "Pass", "Not run", "Not tested", "Not accepted" and "Limit" take
// their colours from shared.js. Anything else is a plain chip.

import { chipFor, fail } from "./shared.js";

// ---- Constants --------------------------------------------------------

const ITEM = /<li>([\s\S]*?)<\/li>/g;

// ---- Chips ------------------------------------------------------------

function chipOf(html) {
  const text = html.replace(/<[^>]+>/g, "").trim();

  return chipFor(text) ?? `<span class="chip">${html.trim()}</span>`;
}

export default function chips(block) {
  const items = [...block.html.matchAll(ITEM)].map((match) => match[1]);

  if (items.length === 0) {
    fail(block, "needs a list inside the block");
  }

  return { chips: items.map(chipOf) };
}
