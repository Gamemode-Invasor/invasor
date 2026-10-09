import "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import type { GamepadNav } from "../src/ui/gamepad-nav";
import { createTabHost, type TabSpec } from "../src/ui/tabhost";

function host() {
  const log: string[] = [];
  let visible = true;
  const div = () => document.body.appendChild(document.createElement("div"));
  const nav = { ensureFocus() {}, reset() {} } as unknown as GamepadNav;
  const tabs = createTabHost({ content: div(), mainTabs: div(), subBar: div(), subTabs: div(), nav, visible: () => visible });
  const spec = (id: string): TabSpec => ({
    id,
    label: id,
    render: () => void log.push(`render ${id}`),
    onShow: () => void log.push(`show ${id}`),
    onHide: () => void log.push(`hide ${id}`),
  });
  return { tabs, log, spec, setVisible: (v: boolean) => (visible = v) };
}

test("the first tab is built and shown", async () => {
  const { tabs, log, spec } = host();
  await tabs.set([spec("a"), spec("b")]);
  assert.deepEqual(log, ["render a", "show a"]);
  assert.equal(tabs.activeId(), "a");
});

test("stepping hides one tab and shows the next, built lazily", async () => {
  const { tabs, log, spec } = host();
  await tabs.set([spec("a"), spec("b")]);
  tabs.step(1);
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(log, ["render a", "show a", "hide a", "render b", "show b"]);
});

test("a tab that stays on screen after hidden() gets its onShow back", async () => {
  // rebuildTabs() calls hidden() before replacing the list; the same tab then stays.
  const { tabs, log, spec } = host();
  const a = spec("a");
  const b = spec("b");
  await tabs.set([a, b]);
  log.length = 0;
  tabs.hidden();
  await tabs.set([a, b], "a");
  assert.deepEqual(log, ["hide a", "show a"]);
});

test("a tab that stays without a hidden() is left alone", async () => {
  const { tabs, log, spec } = host();
  const a = spec("a");
  await tabs.set([a, spec("b")]);
  log.length = 0;
  await tabs.set([a, spec("c")], "a");
  assert.deepEqual(log, []); // no hide/show churn
});

test("while the host isn't visible no hook fires, and shown() fires onShow once", async () => {
  const { tabs, log, spec, setVisible } = host();
  setVisible(false);
  await tabs.set([spec("a")]);
  assert.deepEqual(log, ["render a"]);
  setVisible(true);
  tabs.shown();
  tabs.shown();
  assert.deepEqual(log, ["render a", "show a"]);
});

test("a render that throws shows the error in its own pane only", async () => {
  const { tabs, spec } = host();
  const bad: TabSpec = { ...spec("bad"), render: () => { throw new Error("boom"); } };
  const log = console.error;
  console.error = () => {};
  try {
    await tabs.set([bad, spec("ok")]);
  } finally {
    console.error = log;
  }
  assert.match(document.body.textContent ?? "", /boom/);
  assert.equal(tabs.count(), 2);
});

test("the L1/R1 labels beside a tab row step the tabs when tapped", async () => {
  const log: string[] = [];
  const div = () => document.body.appendChild(document.createElement("div"));
  const nav = { ensureFocus() {}, reset() {} } as unknown as GamepadNav;
  const row = div();
  row.innerHTML = '<span class="tab-key">L1</span><div class="tabs"></div><span class="tab-key">R1</span>';
  const tabs = createTabHost({ content: div(), mainTabs: row.querySelector<HTMLElement>(".tabs"), subBar: div(), subTabs: div(), nav, visible: () => true });
  const spec = (id: string): TabSpec => ({ id, label: id, render: () => void log.push(id) });
  await tabs.set([spec("a"), spec("b"), spec("c")]);
  const [l1, r1] = row.querySelectorAll<HTMLElement>(".tab-key");
  r1.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(tabs.activeId(), "b");
  l1.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(tabs.activeId(), "a");
});
