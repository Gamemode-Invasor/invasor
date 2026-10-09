// A DOM for the tests (jsdom, a devDependency used only here). Import it FIRST in a test
// file: the modules under test read `document` and friends when they run.
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { pretendToBeVisual: true });
const g = globalThis as unknown as Record<string, unknown>;
for (const key of ["window", "document", "HTMLElement", "Element", "Node", "CustomEvent", "Event", "KeyboardEvent", "MutationObserver"]) {
  g[key] = (dom.window as unknown as Record<string, unknown>)[key];
}

/** Let pending promises (and zero-delay timers) run. */
export const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

/** A promise settled from outside, to control the order in which answers arrive. */
export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}
