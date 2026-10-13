import "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import type { GamepadNav } from "../src/ui/gamepad-nav";
import { createSettingsPanel } from "../src/ui/settings-panel";
import { settle } from "./dom";

function setup(render: (el: HTMLElement) => Promise<void>) {
  const stage = document.body.appendChild(document.createElement("div"));
  const panel = stage.appendChild(document.createElement("div"));
  const nav = { reset() {}, ensureFocus() {} } as unknown as GamepadNav;
  const settings = createSettingsPanel({ stage, panel, nav, render });
  return { settings, stage };
}

test("nothing is built until it opens", () => {
  let renders = 0;
  setup(async () => void renders++);
  assert.equal(renders, 0);
});

test("it is titled Settings and says Start is the way back", () => {
  const { stage } = setup(async () => {});
  const el = stage.querySelector<HTMLElement>('.cover-panel[data-kind="settings"]')!;
  assert.equal(el.querySelector(".cover-title")!.textContent, "Settings");
  assert.equal(el.dataset.extraHint, "Start back");
});

test("it is built the first time and kept when it closes and opens again", async () => {
  let renders = 0;
  const { settings } = setup(async (el) => {
    renders++;
    el.append(document.createElement("hr"));
  });
  settings.open();
  await settle();
  settings.close();
  settings.open();
  await settle();
  assert.equal(renders, 1);
  assert.equal(settings.content.querySelectorAll("hr").length, 1);
});

test("a failure is shown in the panel and the next opening tries again", async () => {
  const log = console.error;
  console.error = () => {};
  try {
    let renders = 0;
    const { settings } = setup(async (el) => {
      renders++;
      if (renders === 1) throw new Error("backend down");
      el.append(document.createElement("hr"));
    });
    settings.open();
    await settle();
    assert.match(settings.content.textContent ?? "", /Error in Settings: backend down/);
    settings.close();
    settings.open();
    await settle();
    assert.equal(renders, 2);
    assert.equal(settings.content.querySelectorAll("hr").length, 1);
  } finally {
    console.error = log;
  }
});
