// The spacing scale as bars, from a table. Each bar is as wide as the
// token it names, read from the stylesheet, so the page cannot drift
// from it.
//
//   <!-- hands:scale -->
//
//   | Token | Value |
//   | --- | --- |
//   | `--space-3` | 1rem |
//
//   <!-- /hands:scale -->
//
// The table needs the columns "Token" and "Value". On the site the
// bars take the table's place.

import { fail, plainCell } from "./shared.js";

// ---- Constants --------------------------------------------------------

const COLUMNS = ["Token", "Value"];

const TOKEN = /^--[a-z0-9-]+$/;

// ---- Scale ------------------------------------------------------------

export default function scale(block) {
  if (!block.table) {
    fail(block, "needs a table inside the block");
  }

  for (const column of COLUMNS) {
    if (!block.table.columns.includes(column)) {
      fail(block, `the table needs a "${column}" column`);
    }
  }

  const steps = block.table.rows.map((row) => {
    const token = plainCell(row.Token);

    if (!TOKEN.test(token)) {
      fail(block, `"${token}" is not a token such as --space-3`);
    }

    return { token, value: plainCell(row.Value) };
  });

  return { steps };
}
