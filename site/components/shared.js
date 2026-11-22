// Helpers the components and the Markdown plugins share.

// ---- Constants --------------------------------------------------------

// What a short status phrase means. The site draws these as chips:
// blue for a pass, yellow for anything not yet proven, red for a
// hard limit.
const CHIP_KINDS = [
  { test: /^pass(ed)?$/i, kind: "pass" },
  { test: /^not (yet )?(run|tested|accepted)$/i, kind: "open" },
  { test: /^limit$/i, kind: "stop" },
];

// ---- Errors -----------------------------------------------------------

// Stops the build and names the block, the file and the line.
export function fail(block, message) {
  const file = block.env?.page?.inputPath ?? "a page";

  throw new Error(
    `hands:${block.name} in ${file}, line ${block.line}: ${message}`,
  );
}

// ---- Text -------------------------------------------------------------

export function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// A table cell as plain text: no backticks, no emphasis marks.
export function plainCell(cell) {
  return cell.replace(/[`*_]/g, "").trim();
}

// ---- Chips ------------------------------------------------------------

export function chipKind(text) {
  for (const entry of CHIP_KINDS) {
    if (entry.test.test(text)) {
      return entry.kind;
    }
  }

  return null;
}

// The chip for a status phrase, or null when the text is not one.
export function chipFor(text) {
  const kind = chipKind(text);

  if (!kind) {
    return null;
  }

  return `<span class="chip chip-${kind}">${escapeHtml(text)}</span>`;
}
