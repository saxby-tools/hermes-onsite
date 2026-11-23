// The brand files, each on the ground it is made for, from a table.
//
//   <!-- onsite:marks -->
//
//   | File | Ground | Use |
//   | --- | --- | --- |
//   | ![The mark](brand/mark.svg) | any | Favicon, avatar |
//
//   <!-- /onsite:marks -->
//
// "Ground" is "light", "blue" or "any". The table needs the columns
// "File", "Ground" and "Use". On the site the tiles take the table's
// place.

import { fail, plainCell } from "./shared.js";

// ---- Constants --------------------------------------------------------

const COLUMNS = ["File", "Ground", "Use"];

const GROUNDS = ["light", "blue", "any"];

// ---- Marks ------------------------------------------------------------

export default function marks(block) {
  if (!block.table) {
    fail(block, "needs a table inside the block");
  }

  for (const column of COLUMNS) {
    if (!block.table.columns.includes(column)) {
      fail(block, `the table needs a "${column}" column`);
    }
  }

  const rows = block.table.rows.map((row, index) => {
    const ground = plainCell(row.Ground);

    if (!GROUNDS.includes(ground)) {
      fail(block, `"${ground}" is not a ground: light, blue or any`);
    }

    return {
      ground,
      image: block.table.htmlRows[index].File,
      use: block.table.htmlRows[index].Use,
    };
  });

  return { marks: rows };
}
