import "./dom";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { attachGamepadNav, type GamepadNav } from "../src/ui/gamepad-nav";
import { createTasksPanel, type TasksPanel } from "../src/ui/tasks-panel";

// jsdom has no layout: the pad only stops on controls that have boxes, so every element gets one.
Element.prototype.getClientRects = function () {
  return [{}] as unknown as DOMRectList;
};

const SELECT = 13;
const B = 2;
const L1 = 5;
const R2 = 8;
const A = 1;

let nav: GamepadNav | null = null;
afterEach(() => {
  nav?.destroy();
  nav = null;
  document.body.innerHTML = "";
});

function setup() {
  const log: string[] = [];
  const stage = document.body.appendChild(document.createElement("div"));
  const panel = stage.appendChild(document.createElement("div"));
  panel.className = "panel";
  panel.innerHTML = `<div class="content"><button id="one">One</button></div><div class="hints"></div>`;
  let tasks: TasksPanel;
  nav = attachGamepadNav(stage, panel, () => log.push("close"), {
    onTab: (dir) => log.push(`tab ${dir}`),
    onSubTab: (dir) => log.push(`subtab ${dir}`),
    onSelect: () => (log.push("select"), tasks.toggle()),
  });
  tasks = createTasksPanel({ stage, panel, nav });
  const press = (button: number, extra: Record<string, unknown> = {}) => {
    const e = new CustomEvent("vgp_onbuttondown", { detail: { button, ...extra }, bubbles: true, cancelable: true });
    window.dispatchEvent(e);
    return e;
  };
  return { log, stage, panel, tasks, press };
}

test("Select is taken from Steam and asks for the Tasks panel", () => {
  const { log, press, tasks } = setup();
  const e = press(SELECT);
  assert.equal(e.defaultPrevented, true); // Steam's UI underneath doesn't see it
  assert.deepEqual(log, ["select"]);
  assert.equal(tasks.isOpen(), true);
});

test("Select again hides it", () => {
  const { log, press, tasks } = setup();
  press(SELECT);
  press(SELECT);
  assert.deepEqual(log, ["select", "select"]);
  assert.equal(tasks.isOpen(), false);
});

test("B closes the Tasks panel first and the panel only after", () => {
  const { log, press, tasks } = setup();
  press(SELECT);
  press(B);
  assert.equal(tasks.isOpen(), false);
  assert.deepEqual(log, ["select"]); // the panel is still open
  press(B);
  assert.deepEqual(log, ["select", "close"]);
});

test("tabs don't change under the Tasks panel", () => {
  const { log, press } = setup();
  press(L1);
  press(R2);
  assert.deepEqual(log, ["tab -1", "subtab 1"]);
  log.length = 0;
  press(SELECT);
  press(L1);
  press(R2);
  assert.deepEqual(log, ["select"]);
});

test("holding Select doesn't flicker the panel", () => {
  const { log, press } = setup();
  press(SELECT, { is_repeat: true });
  assert.deepEqual(log, []);
});

test("Select does nothing under a big window or a dialog", () => {
  const { log, press, stage, panel } = setup();
  const win = stage.appendChild(document.createElement("div"));
  win.className = "iwin";
  press(SELECT);
  assert.deepEqual(log, []);
  win.remove();
  const modal = panel.appendChild(document.createElement("div"));
  modal.className = "modal";
  press(SELECT);
  assert.deepEqual(log, []);
  modal.remove();
  press(SELECT);
  assert.deepEqual(log, ["select"]);
});

test("Select with the panel closed is left to Steam", () => {
  const { log, press, panel } = setup();
  panel.hidden = true;
  const e = press(SELECT);
  assert.equal(e.defaultPrevented, false);
  assert.deepEqual(log, []);
});

test("a control can take Select before the Tasks panel does", () => {
  const { log, press, panel } = setup();
  nav!.reset(); // the ring goes to the first control
  const button = panel.querySelector<HTMLElement>("#one")!;
  const seen: string[] = [];
  button.addEventListener("invasor:button", (e) => {
    seen.push((e as CustomEvent).detail.button);
    e.preventDefault();
  });
  press(SELECT);
  assert.deepEqual(seen, ["SELECT"]);
  assert.deepEqual(log, []);
});

test("the pad hint bar follows the Tasks panel", () => {
  const { press, stage } = setup();
  nav!.reset();
  press(SELECT);
  const hints = stage.querySelector(".tasks-panel .hints")!.textContent;
  assert.match(hints ?? "", /Select back/);
});

test("the touch button into Tasks is not a stop for the pad", () => {
  const { panel, press } = setup();
  const touch = panel.querySelector(".content")!.appendChild(document.createElement("button"));
  touch.className = "tasks-btn";
  nav!.reset();
  press(A); // activates the ring's control
  assert.equal(document.querySelector(".nav-focus")?.id, "one");
});
