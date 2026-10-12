import "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import { settle } from "./dom";
import { esc, safe } from "../src/ui/util";

test("esc escapes what innerHTML would read as markup", () => {
  assert.equal(esc(`<b a="1">&'`), "&#60;b a=&#34;1&#34;&#62;&#38;&#39;");
});

test("safe logs a throw and a rejected promise instead of leaving them unhandled", async () => {
  const errors: unknown[][] = [];
  const real = console.error;
  console.error = (...a: unknown[]) => void errors.push(a);
  const unhandled: unknown[] = [];
  const onUnhandled = (e: unknown) => void unhandled.push(e);
  process.on("unhandledRejection", onUnhandled);
  try {
    safe("sync", () => {
      throw new Error("boom");
    });
    safe("async", async () => {
      throw new Error("later");
    });
    await settle();
    await settle();
  } finally {
    console.error = real;
    process.off("unhandledRejection", onUnhandled);
  }
  assert.equal(errors.length, 2);
  assert.deepEqual(unhandled, []);
});
