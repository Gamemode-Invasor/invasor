import { deferred, settle } from "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import { renderSettings, type SettingsDeps } from "../src/ui/settings";

const ANSWERS: Record<string, unknown> = {
  modules: [],
  prefs: { open_combo: ["L3", "R3"], panel_side: "right", handle_icon: "icon", accent_color: "blue", module_order: [], update_check: false, update_channel: "stable" },
  module_removed: [],
  update_status: null,
  info: { version: "0", python: "3" },
  pads: [],
};

/** A backend whose answers to core/modules are handed out by the test, one at a time. */
function deps(modules: Promise<unknown>[]): SettingsDeps {
  return {
    api: { call: async (_feature: string, method: string) => (method === "modules" ? modules.shift() : ANSWERS[method]) } as unknown as SettingsDeps["api"],
    version: "test",
    toast() {},
    onModulesChanged: async () => {},
    onPanelSide() {},
    onAccentColor() {},
    onHandleIcon() {},
    openWindow: () => null,
  };
}

const sections = (el: HTMLElement) => [...el.querySelectorAll(".section-title, summary")].map((s) => s.textContent);

test("a redraw while another is still waiting doesn't show the sections twice", async () => {
  const first = deferred<unknown>();
  const second = deferred<unknown>();
  const el = document.createElement("div");
  const d = deps([first.promise, second.promise]);
  const a = renderSettings(el, d);
  el.replaceChildren(); // what rerender() does before drawing again
  const b = renderSettings(el, d);
  second.resolve([]);
  first.resolve([]);
  await Promise.all([a, b]);
  await settle();
  const titles = sections(el);
  assert.ok(titles.length > 0, "something was drawn");
  assert.equal(new Set(titles).size, titles.length, `duplicated sections: ${titles.join(", ")}`);
});

test("a single draw shows every section once", async () => {
  const el = document.createElement("div");
  await renderSettings(el, deps([Promise.resolve([])]));
  const titles = sections(el);
  assert.ok(titles.includes("Controller") || titles.some((t) => /Controller/.test(t ?? "")), titles.join());
  assert.match(titles[0] ?? "", /Manage Invasor/, "Manage Invasor comes first");
});
