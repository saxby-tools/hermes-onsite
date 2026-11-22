// Type specimens, from a table. Each row's sample is set in the type
// class the row names, so the page shows the real styles and cannot
// drift from the stylesheet.
//
//   <!-- hands:specimen -->
//
//   | Class | Sample |
//   | --- | --- |
//   | `type-display` | One agent, two machines |
//
//   <!-- /hands:specimen -->
//
// The table needs the columns "Class" and "Sample". On the site the
// specimens take the table's place.

import { fail, plainCell } from "./shared.js";

// ---- Constants --------------------------------------------------------

const COLUMNS = ["Class", "Sample"];

const CLASS_NAME = /^type-[a-z-]+$/;

// ---- Specimen ---------------------------------------------------------

export default function specimen(block) {
  if (!block.table) {
    fail(block, "needs a table inside the block");
  }

  for (const column of COLUMNS) {
    if (!block.table.columns.includes(column)) {
      fail(block, `the table needs a "${column}" column`);
    }
  }

  const rows = block.table.rows.map((row, index) => {
    const name = plainCell(row.Class);

    if (!CLASS_NAME.test(name)) {
      fail(block, `"${name}" is not a type class such as type-body`);
    }

    return { name, sample: block.table.htmlRows[index].Sample };
  });

  return { rows };
}
