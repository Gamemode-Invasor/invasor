// Run with `npm test` (bundled by esbuild, run by node's built-in test runner).
import assert from "node:assert/strict";
import { test } from "node:test";
import { decimals, isVideo, optionIndex, snap, whenMet } from "../src/ui/values";

test("decimals", () => {
  assert.equal(decimals(5), 0);
  assert.equal(decimals(0.1), 1);
  assert.equal(decimals(0.25), 2);
  assert.equal(decimals(1e-7), 7);
  assert.equal(decimals(1.5e-7), 8);
});

test("snap: float steps have no noise", () => {
  assert.equal(snap(0.1 + 0.2, 0, 1, 0.1), 0.3);
  assert.equal(snap(0.75, 0, 1, 0.25), 0.75);
  assert.equal(snap(1.26, 1, 2, 0.05), 1.25);
});

test("snap: clamps and counts steps from min (same as the backend)", () => {
  assert.equal(snap(999, 15, 144, 5), 140);
  assert.equal(snap(-3, 15, 144, 5), 15);
  assert.equal(snap(17, 15, 144, 5), 15);
  assert.equal(snap(18, 15, 144, 5), 20);
  assert.equal(snap(NaN, 0, 10, 1), 0);
});

test("optionIndex is strict", () => {
  const opts = [{ value: 1 }, { value: "1" }, { value: true }];
  assert.equal(optionIndex(opts, "1"), 1);
  assert.equal(optionIndex(opts, true), 2);
  assert.equal(optionIndex(opts, 2), -1);
});

test("isVideo by path extension", () => {
  assert.equal(isVideo("https://cdn2.steamgriddb.com/thumb/abc.webm"), true);
  assert.equal(isVideo("/x/clip.MP4?v=2"), true);
  assert.equal(isVideo("https://cdn2.steamgriddb.com/thumb/abc.png"), false);
  assert.equal(isVideo("https://x/webm/abc.png?f=.webm"), false);
  assert.equal(isVideo(null), false);
});

test("whenMet", () => {
  const v: Record<string, unknown> = { adaptive: true, mode: "b", n: 3 };
  const get = (k: string) => v[k];
  assert.equal(whenMet(undefined, get), true);
  assert.equal(whenMet({ adaptive: true }, get), true);
  assert.equal(whenMet({ adaptive: false }, get), false);
  assert.equal(whenMet({ mode: "b", n: 3 }, get), true);
  assert.equal(whenMet({ mode: "b", n: 2 }, get), false);
  assert.equal(whenMet({ n: "3" }, get), false); // no loose equality
  assert.equal(whenMet({ mode: ["a", "b"] }, get), true); // a list: any of its values
  assert.equal(whenMet({ mode: ["a", "c"] }, get), false);
});
