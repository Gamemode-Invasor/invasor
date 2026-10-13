import "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import { ui } from "../src/ui/controls";
import type { ModuleCtx } from "../src/module-api";

/** Just enough of a ctx for windowButton: it only listens to the room for windows. */
function ctxWithListeners() {
  const listeners = new Set<(canOpen: boolean) => void>();
  const ctx = {
    canOpenWindow: () => true,
    onSpaceChange(cb: (canOpen: boolean) => void) {
      listeners.add(cb);
      return () => void listeners.delete(cb);
    },
    toast() {},
    openWindow: () => null,
  } as unknown as ModuleCtx;
  const fire = (canOpen: boolean) => [...listeners].forEach((cb) => cb(canOpen));
  return { ctx, listeners, fire };
}

test("a windowButton that left the page stops listening to the room for windows", () => {
  const { ctx, listeners, fire } = ctxWithListeners();
  // A window's render builds a new button on every open, and the old ones are thrown away.
  for (let i = 0; i < 5; i++) {
    const button = ui.windowButton(ctx, { open: () => ({ label: "w", tabs: [] }) as never });
    document.body.append(button);
    fire(true); // seen on the page
    button.remove();
  }
  fire(true);
  assert.equal(listeners.size, 0);
});

test("a windowButton not yet on the page keeps listening", () => {
  const { ctx, listeners, fire } = ctxWithListeners();
  const button = ui.windowButton(ctx, { open: () => ({ label: "w", tabs: [] }) as never });
  fire(true); // the module hasn't appended it yet
  assert.equal(listeners.size, 1);
  document.body.append(button);
  fire(false);
  assert.equal(listeners.size, 1);
  assert.match(button.textContent ?? "", /🔒/); // and it repaints
});

test("a button whose async onClick rejects logs it, nothing is left unhandled", async () => {
  const unhandled: unknown[] = [];
  const onUnhandled = (e: unknown) => void unhandled.push(e);
  process.on("unhandledRejection", onUnhandled);
  const real = console.error;
  const logged: unknown[] = [];
  console.error = (...a: unknown[]) => void logged.push(a);
  try {
    const b = ui.button({
      label: "x",
      onClick: async () => {
        throw new Error("nope");
      },
    });
    b.click();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  } finally {
    console.error = real;
    process.off("unhandledRejection", onUnhandled);
  }
  assert.equal(logged.length, 1);
  assert.deepEqual(unhandled, []);
});

test("a select in a cover panel closes its list when the cover is dismissed", () => {
  const cover = document.body.appendChild(document.createElement("div"));
  cover.className = "cover-panel";
  const select = ui.select({ label: "S", value: 1, options: [{ label: "A", value: 1 }, { label: "B", value: 2 }] });
  cover.append(select);
  const list = select.querySelector<HTMLElement>(".select-list")!;
  select.click();
  assert.equal(list.hidden, false);
  cover.dispatchEvent(new CustomEvent("invasor:dismiss"));
  assert.equal(list.hidden, true);
});
