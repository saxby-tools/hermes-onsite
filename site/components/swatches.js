// Colour swatches above a table of colour tokens. Each swatch shows
// the token's light value on the left and its dark value on the
// right. The table stays, because it holds what each colour is for.
//
//   <!-- hands:swatches -->
//
//   | Token | Light | Dark | Used for |
//   | --- | --- | --- | --- |
//   | `--color-ground` | `#f2f2f2` | `#05053d` | The page |
//
//   <!-- /hands:swatches -->
//
// The table needs the columns "Token", "Light" and "Dark".

import { fail, plainCell } from "./shared.js";

// ---- Constants --------------------------------------------------------

const COLUMNS = ["Token", "Light", "Dark"];

const HEX = /^#[0-9a-f]{6}$/i;

// ---- Swatches ---------------------------------------------------------

function hexOf(block, row, column) {
  const value = plainCell(row[column]);

  if (!HEX.test(value)) {
    fail(block, `"${value}" in "${column}" is not a six-digit hex colour`);
  }

  return value;
}

export default function swatches(block) {
  if (!block.table) {
    fail(block, "needs a table inside the block");
  }

  for (const column of COLUMNS) {
    if (!block.table.columns.includes(column)) {
      fail(block, `the table needs a "${column}" column`);
    }
  }

  return {
    html: block.html,
    swatches: block.table.rows.map((row) => ({
      token: plainCell(row.Token),
      light: hexOf(block, row, "Light"),
      dark: hexOf(block, row, "Dark"),
    })),
  };
}
