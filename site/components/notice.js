// A boxed aside with a coloured bar and a label that says what kind
// of aside it is.
//
//   <!-- onsite:notice {"kind": "limit"} -->
//
//   No sandbox. An SSH target can do anything its user can.
//
//   <!-- /onsite:notice -->
//
// `kind` is "note" (the default), "limit", "untested" or "terms". On
// GitHub the reader sees the paragraph alone, so write it to stand
// without the label.
//
// "terms" is a glossary card. Write it as a list, one term an item,
// with the term in bold at the start:
//
//   <!-- onsite:notice {"kind": "terms"} -->
//
//   - **Host.** The server Hermes runs on.
//   - **Target.** A computer its tools can run on.
//
//   <!-- /onsite:notice -->

import { fail } from "./shared.js";

// ---- Constants --------------------------------------------------------

const KINDS = {
  note: { label: "Note", modifier: "" },
  limit: { label: "Limit", modifier: " callout-stop" },
  untested: { label: "Not tested", modifier: " callout-open" },
  terms: { label: "Terms you need to know", modifier: " callout-terms" },
};

// ---- Notice -----------------------------------------------------------

export default function notice(block) {
  const kind = block.options.kind ?? "note";

  if (!Object.hasOwn(KINDS, kind)) {
    fail(
      block,
      `"kind" must be note, limit, untested or terms, not "${kind}"`,
    );
  }

  return {
    label: KINDS[kind].label,
    modifier: KINDS[kind].modifier,
    html: block.html,
  };
}
