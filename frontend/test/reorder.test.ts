import "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import { deferred, settle } from "./dom";
import { reorderList } from "../src/ui/reorder";

function list(onCommit: (ids: string[]) => Promise<void>) {
  const parent = document.body.appendChild(document.createElement("div"));
  const rows = reorderList(
    ["a", "b", "c"].map((id) => ({ id, label: id.toUpperCase() })),
    onCommit,
  );
  parent.append(...rows);
  const press = (label: string) => parent.querySelector<HTMLElement>(`[aria-label="${label}"]`)!.click();
  const order = () => [...parent.querySelectorAll(".reorder")].map((r) => r.textContent!.trim()[0]);
  return { press, order };
}

test("two quick moves are saved one after the other, in the order they were made", async () => {
  const saved: string[][] = [];
  const gates: ReturnType<typeof deferred<void>>[] = [];
  const { press, order } = list((ids) => {
    saved.push(ids);
    const g = deferred<void>();
    gates.push(g);
    return g.promise;
  });
  press("Move down A");
  await settle(); // the first save is on its way
  press("Move down A");
  await settle();
  assert.equal(saved.length, 1, "the second save waits for the first");
  assert.deepEqual(saved[0], ["b", "a", "c"]);
  gates[0].resolve();
  await settle();
  assert.deepEqual(saved[1], ["b", "c", "a"]);
  gates[1].resolve();
  await settle();
  assert.deepEqual(order(), ["B", "C", "A"]);
});

test("a refused save puts the rows back", async () => {
  const { press, order } = list(async () => {
    throw new Error("no");
  });
  press("Move down A");
  await settle();
  assert.deepEqual(order(), ["A", "B", "C"]);
});

test("a row grabbed in a cover panel is let go when the cover is dismissed", () => {
  const cover = document.body.appendChild(document.createElement("div"));
  cover.className = "cover-panel";
  cover.append(...reorderList(["a", "b"].map((id) => ({ id, label: id })), async () => {}));
  const row = cover.querySelector<HTMLElement>(".reorder")!;
  row.click();
  assert.equal(row.classList.contains("grabbed"), true);
  cover.dispatchEvent(new CustomEvent("invasor:dismiss"));
  assert.equal(row.classList.contains("grabbed"), false);
});
