import assert from "node:assert/strict";
import { test } from "node:test";
import { panelOnLeft } from "../src/ui/side";

test("Quick Access is always on the left, whatever the setting", () => {
  for (const side of ["auto", "left", "right"] as const) assert.equal(panelOnLeft("quickaccess", side), true, side);
});

test("the library follows the setting", () => {
  assert.equal(panelOnLeft("main", "auto"), false);
  assert.equal(panelOnLeft("main", "right"), false);
  assert.equal(panelOnLeft("main", "left"), true);
});
