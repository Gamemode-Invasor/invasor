import "./dom";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { copyText } from "../src/ui/clipboard";

// Node's navigator has no clipboard and jsdom has no execCommand: each test sets the ones it needs.
const g = globalThis as unknown as Record<string, unknown>;
const navDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
const doc = document as unknown as Record<string, unknown>;

function setClipboard(clipboard: unknown) {
  Object.defineProperty(globalThis, "navigator", { value: clipboard === undefined ? {} : { clipboard }, configurable: true, writable: true });
}

function setExecCommand(fn: ((cmd: string) => boolean) | undefined) {
  doc.execCommand = fn;
}

afterEach(() => {
  if (navDescriptor) Object.defineProperty(globalThis, "navigator", navDescriptor);
  else delete g.navigator;
  delete doc.execCommand;
  document.body.replaceChildren();
});

function writer(ok: boolean) {
  const written: string[] = [];
  return {
    written,
    clipboard: {
      writeText: (text: string) => {
        written.push(text);
        return ok ? Promise.resolve() : Promise.reject(new Error("denied"));
      },
    },
  };
}

function execSpy(result: boolean | "throw") {
  const calls: { cmd: string; area: HTMLTextAreaElement | null; value: string | undefined }[] = [];
  setExecCommand((cmd: string) => {
    const area = document.querySelector("textarea");
    calls.push({ cmd, area, value: area?.value });
    if (result === "throw") throw new Error("nope");
    return result;
  });
  return calls;
}

test("the clipboard API is used when it works, no textarea", async () => {
  const w = writer(true);
  setClipboard(w.clipboard);
  const calls = execSpy(true);
  assert.equal(await copyText("DEMO=1 %command%"), true);
  assert.deepEqual(w.written, ["DEMO=1 %command%"]);
  assert.equal(calls.length, 0);
  assert.equal(document.querySelector("textarea"), null);
});

test("when the clipboard API rejects, execCommand copies from a hidden textarea", async () => {
  setClipboard(writer(false).clipboard);
  const calls = execSpy(true);
  assert.equal(await copyText("hello", document), true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].cmd, "copy");
  assert.ok(calls[0].area);
  assert.equal(calls[0].value, "hello");
  assert.equal(calls[0].area!.readOnly, true);
  assert.equal(calls[0].area!.style.position, "fixed");
  assert.equal(calls[0].area!.style.opacity, "0");
  assert.equal(calls[0].area!.style.pointerEvents, "none");
  assert.equal(document.querySelector("textarea"), null);
});

test("no navigator.clipboard goes straight to the fallback", async () => {
  setClipboard(undefined);
  const calls = execSpy(true);
  assert.equal(await copyText("x"), true);
  assert.equal(calls.length, 1);
});

test("a clipboard whose writeText throws synchronously falls back too", async () => {
  setClipboard({
    writeText() {
      throw new Error("sync");
    },
  });
  const calls = execSpy(true);
  assert.equal(await copyText("x"), true);
  assert.equal(calls.length, 1);
});

test("both failing gives false, never a throw", async () => {
  setClipboard(writer(false).clipboard);
  execSpy(false);
  assert.equal(await copyText("x"), false);

  setClipboard(writer(false).clipboard);
  execSpy("throw");
  assert.equal(await copyText("x"), false);
  assert.equal(document.querySelector("textarea"), null); // removed even when execCommand throws

  setClipboard(undefined);
  setExecCommand(undefined); // jsdom: no execCommand at all
  assert.equal(await copyText("x"), false);
  assert.equal(document.querySelector("textarea"), null);
});

test("non-strings and texts over 1 MB give false without touching the clipboard", async () => {
  for (const bad of [123, null, undefined, { toString: () => "x" }, "x".repeat((1 << 20) + 1)]) {
    const w = writer(true);
    setClipboard(w.clipboard);
    const calls = execSpy(true);
    assert.equal(await copyText(bad as string), false);
    assert.deepEqual(w.written, []);
    assert.equal(calls.length, 0);
  }
  const w = writer(true);
  setClipboard(w.clipboard);
  assert.equal(await copyText("x".repeat(1 << 20)), true); // exactly 1 MB is fine
});

test("the fallback gives focus back to what had it, without scrolling", async () => {
  const before = document.body.appendChild(document.createElement("button"));
  before.focus();
  assert.equal(document.activeElement, before);
  const focusCalls: unknown[] = [];
  const realFocus = before.focus.bind(before);
  before.focus = (opts?: FocusOptions) => {
    focusCalls.push(opts);
    realFocus(opts);
  };
  setClipboard(writer(false).clipboard);
  execSpy("throw");
  assert.equal(await copyText("x"), false);
  assert.equal(document.activeElement, before);
  assert.deepEqual(focusCalls.at(-1), { preventScroll: true });
});
