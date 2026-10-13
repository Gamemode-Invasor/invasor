import "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import { createCoverPanel, toastHostOf, topLayerOf } from "../src/ui/cover-panel";
import { WINDOW_CLOSE_EVENT, type GamepadNav } from "../src/ui/gamepad-nav";
import { settle } from "./dom";

function setup(onOpen?: (content: HTMLElement) => void | Promise<void>, onClose?: () => void) {
  const calls: string[] = [];
  const stage = document.body.appendChild(document.createElement("div"));
  const panel = stage.appendChild(document.createElement("div"));
  panel.className = "panel";
  const nav = { reset: () => calls.push("reset"), ensureFocus: () => calls.push("ensure") } as unknown as GamepadNav;
  const cover = createCoverPanel({ stage, panel, nav, kind: "demo", title: "Demo", backHint: "Select back", onOpen, onClose });
  const el = stage.querySelector<HTMLElement>('.cover-panel[data-kind="demo"]')!;
  return { calls, stage, panel, cover, el };
}

test("it starts closed, titled, with a place for content, a toast and the pad's hints", () => {
  const { cover, el } = setup();
  assert.equal(cover.isOpen(), false);
  assert.equal(el.hidden, true);
  assert.equal(el.querySelector(".cover-title")!.textContent, "Demo");
  assert.equal(el.dataset.extraHint, "Select back");
  assert.ok(cover.content.matches(".content-wrap > .content"));
  assert.ok(el.querySelector(".toast"));
  assert.ok(el.querySelector(".hints"));
});

test("a title is text, never markup", () => {
  const stage = document.body.appendChild(document.createElement("div"));
  const panel = stage.appendChild(document.createElement("div"));
  createCoverPanel({ stage, panel, nav: { reset() {}, ensureFocus() {} } as unknown as GamepadNav, kind: "x", title: "<img src=x onerror=boom()>", backHint: "" });
  assert.equal(stage.querySelector("img"), null);
});

test("opening covers the panel, takes the pad and tells the content where it goes", () => {
  const seen: HTMLElement[] = [];
  const { cover, el, panel, calls } = setup((content) => void seen.push(content));
  cover.open();
  assert.equal(el.hidden, false);
  assert.equal(panel.classList.contains("covered"), true);
  assert.deepEqual(calls, ["reset"]);
  assert.deepEqual(seen, [cover.content]);
  cover.open();
  assert.equal(seen.length, 1);
});

test("closing uncovers it and puts the ring back on what it was on", () => {
  const { cover, panel, stage, calls } = setup();
  const control = panel.appendChild(document.createElement("button"));
  control.classList.add("nav-focus");
  const asked: unknown[] = [];
  stage.addEventListener("invasor:refocus", (e) => asked.push((e as CustomEvent).detail));
  cover.open();
  control.classList.remove("nav-focus");
  cover.close();
  assert.equal(panel.classList.contains("covered"), false);
  assert.deepEqual(asked, [control]);
  assert.deepEqual(calls, ["reset", "ensure"]);
  cover.close();
  assert.deepEqual(calls, ["reset", "ensure"]); // closed already
});

test("closing together with the panel leaves the pad alone", () => {
  const { cover, calls } = setup();
  cover.open();
  cover.close(false);
  assert.deepEqual(calls, ["reset"]);
});

test("B (the close event) and the ✕ close it", () => {
  const { cover, el } = setup();
  cover.open();
  el.dispatchEvent(new CustomEvent(WINDOW_CLOSE_EVENT));
  assert.equal(cover.isOpen(), false);
  cover.open();
  el.querySelector<HTMLElement>(".close")!.click();
  assert.equal(cover.isOpen(), false);
});

test("the ring goes to the first control when what onOpen builds is ready", async () => {
  let ready!: () => void;
  const { cover, calls } = setup(() => new Promise<void>((resolve) => (ready = resolve)));
  cover.open();
  assert.deepEqual(calls, ["reset"]);
  ready();
  await settle();
  assert.deepEqual(calls, ["reset", "ensure"]);
});

test("a build that ends after the panel closed does not move the ring", async () => {
  let ready!: () => void;
  const { cover, calls } = setup(() => new Promise<void>((resolve) => (ready = resolve)));
  cover.open();
  cover.close(false);
  ready();
  await settle();
  assert.deepEqual(calls, ["reset"]);
});

test("a build that fails is logged and the pad still gets its ring", async () => {
  const log = console.error;
  const logged: unknown[][] = [];
  console.error = (...a: unknown[]) => void logged.push(a);
  try {
    const { cover, calls } = setup(() => Promise.reject(new Error("boom")));
    cover.open();
    await settle();
    assert.equal(logged.length, 1);
    assert.deepEqual(calls, ["reset", "ensure"]);
  } finally {
    console.error = log;
  }
});

test("destroy removes it and uncovers the panel", () => {
  const { cover, panel, stage } = setup();
  cover.open();
  cover.destroy();
  assert.equal(stage.querySelector(".cover-panel"), null);
  assert.equal(panel.classList.contains("covered"), false);
});

function layers() {
  const stage = document.body.appendChild(document.createElement("div"));
  const panel = stage.appendChild(document.createElement("div"));
  const panelToast = panel.appendChild(document.createElement("div"));
  panelToast.className = "toast";
  const cover = stage.appendChild(document.createElement("div"));
  cover.className = "cover-panel";
  cover.hidden = true;
  const coverToast = cover.appendChild(document.createElement("div"));
  coverToast.className = "toast";
  const windowBox = (n: number) => {
    const w = stage.appendChild(document.createElement("div"));
    w.className = "iwin";
    const box = w.appendChild(document.createElement("div"));
    box.className = "iwin-box";
    const toast = box.appendChild(document.createElement("div"));
    toast.className = "toast";
    return { box, toast, n };
  };
  return { stage, panel, panelToast, cover, coverToast, windowBox };
}

test("dialogs and toasts go to the panel when nothing covers it", () => {
  const { stage, panel, panelToast } = layers();
  assert.equal(topLayerOf(stage, panel), panel);
  assert.equal(toastHostOf(stage, panelToast), panelToast);
});

test("dialogs and toasts go to the panel that covers it, not to the hidden one", () => {
  const { stage, panel, panelToast, cover, coverToast } = layers();
  cover.hidden = false;
  assert.equal(topLayerOf(stage, panel), cover);
  assert.equal(toastHostOf(stage, panelToast), coverToast);
});

test("a big window is above a panel that covers; the last window is the top one", () => {
  const { stage, panel, panelToast, cover, windowBox } = layers();
  cover.hidden = false;
  const first = windowBox(1);
  const second = windowBox(2);
  assert.equal(topLayerOf(stage, panel), second.box);
  assert.equal(toastHostOf(stage, panelToast), second.toast);
  second.box.parentElement!.remove();
  assert.equal(topLayerOf(stage, panel), first.box);
});

test("onClose is called when it closes, however it closes, and never when it was already closed", () => {
  let closed = 0;
  const { cover, el } = setup(undefined, () => void closed++);
  cover.close();
  assert.equal(closed, 0); // not open
  cover.open();
  cover.close();
  assert.equal(closed, 1);
  cover.open();
  cover.close(false); // with the Invasor panel closing
  assert.equal(closed, 2);
  cover.open();
  el.dispatchEvent(new CustomEvent(WINDOW_CLOSE_EVENT)); // B
  assert.equal(closed, 3);
});

test("destroying it while open calls onClose; destroying it closed does not", () => {
  let closed = 0;
  const first = setup(undefined, () => void closed++);
  first.cover.destroy();
  assert.equal(closed, 0);
  const second = setup(undefined, () => void closed++);
  second.cover.open();
  second.cover.destroy();
  assert.equal(closed, 1);
});

test("closing or destroying it resolves a dialog that was drawn in it", async () => {
  const { ui, setTopLayer } = await import("../src/ui/controls");
  for (const how of ["close", "destroy"] as const) {
    const { cover, el } = setup();
    setTopLayer(() => el);
    cover.open();
    const answer = ui.confirm("Sure?");
    assert.ok(el.querySelector(".modal"));
    cover[how]();
    // A dialog that never resolves must fail the test, not hang the suite.
    const gone = new Promise<string>((r) => setTimeout(() => r("never resolved"), 500));
    assert.equal(await Promise.race([answer, gone]), false);
    assert.equal(el.querySelector(".modal"), null);
  }
  setTopLayer(null);
});
