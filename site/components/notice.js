// A boxed aside with a coloured bar and a label that says what kind
// of aside it is.
//
//   <!-- onsite:notice {"kind": "limit"} -->
//
//   No sandbox. An SSH target can do anything its user can.
//
//   <!-- /onsite:notice -->
//
// `kind` is "note" (the default), "limit" or "untested". On GitHub
// the reader sees the paragraph alone, so write it to stand without
// the label.

import { fail } from "./shared.js";

// ---- Constants --------------------------------------------------------

const KINDS = {
  note: { label: "Note", modifier: "" },
  limit: { label: "Limit", modifier: " callout-stop" },
  untested: { label: "Not tested", modifier: " callout-open" },
};

// ---- Notice -----------------------------------------------------------

export default function notice(block) {
  const kind = block.options.kind ?? "note";

  if (!Object.hasOwn(KINDS, kind)) {
    fail(block, `"kind" must be note, limit or untested, not "${kind}"`);
  }

  return {
    label: KINDS[kind].label,
    modifier: KINDS[kind].modifier,
    html: block.html,
  };
}
