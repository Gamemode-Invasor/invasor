import assert from "node:assert/strict";
import { test } from "node:test";
import { hiddenInQam } from "../src/ui/qam";

const ids = async (entries: Parameters<typeof hiddenInQam>[0], timeout = 30) => [...(await hiddenInQam(entries, timeout))].sort();

test("only an explicit false hides", async () => {
  assert.deepEqual(await ids([{ id: "a", fn: () => false }]), ["a"]);
  for (const v of [true, undefined, null, "no", 0, "", NaN]) assert.deepEqual(await ids([{ id: "a", fn: () => v }]), [], String(v));
});

test("a promise is awaited", async () => {
  assert.deepEqual(await ids([{ id: "a", fn: async () => false }]), ["a"]);
  assert.deepEqual(await ids([{ id: "a", fn: async () => true }]), []);
});

test("a module without a hook is shown", async () => {
  assert.deepEqual(await ids([{ id: "a" }, { id: "b", fn: () => false }]), ["b"]);
});

test("an error or a rejection shows the module", async () => {
  const log = console.error;
  console.error = () => {};
  try {
    assert.deepEqual(await ids([{ id: "a", fn: () => { throw new Error("boom"); } }]), []);
    assert.deepEqual(await ids([{ id: "a", fn: () => Promise.reject(new Error("boom")) }]), []);
  } finally {
    console.error = log;
  }
});

test("a hook that never answers shows the module after the timeout", async () => {
  const started = Date.now();
  assert.deepEqual(await ids([{ id: "slow", fn: () => new Promise(() => {}) }, { id: "no", fn: () => false }], 40), ["no"]);
  assert.ok(Date.now() - started >= 30);
});

test("modules are decided independently", async () => {
  assert.deepEqual(
    await ids([
      { id: "a", fn: () => false },
      { id: "b", fn: () => true },
      { id: "c", fn: async () => false },
    ]),
    ["a", "c"],
  );
});
