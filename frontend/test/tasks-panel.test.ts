import "./dom";
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import { WINDOW_CLOSE_EVENT, type GamepadNav } from "../src/ui/gamepad-nav";
import { REFRESH_MS, createTasksPanel } from "../src/ui/tasks-panel";

// An open panel keeps its 30 s timer: every test ends by destroying what it made, or the run never ends.
const made: { destroy(): void }[] = [];
afterEach(() => {
  for (const p of made.splice(0)) p.destroy();
});

function setup(onOpen?: () => void) {
  const calls: string[] = [];
  const stage = document.body.appendChild(document.createElement("div"));
  const panel = stage.appendChild(document.createElement("div"));
  panel.className = "panel";
  const nav = { reset: () => calls.push("reset"), ensureFocus: () => calls.push("ensure") } as unknown as GamepadNav;
  const tasks = createTasksPanel({ stage, panel, nav, nameOf: (owner) => owner.toUpperCase(), onOpen });
  made.push(tasks);
  const el = stage.querySelector<HTMLElement>(".cover-panel")!;
  return { calls, stage, panel, tasks, el };
}

test("it starts closed and out of the way", () => {
  const { tasks, el, panel } = setup();
  assert.equal(tasks.isOpen(), false);
  assert.equal(el.hidden, true);
  assert.equal(panel.classList.contains("covered"), false);
});

test("opening covers the Invasor panel and takes the pad", () => {
  const { tasks, el, panel, calls } = setup();
  tasks.open();
  assert.equal(tasks.isOpen(), true);
  assert.equal(el.hidden, false);
  assert.equal(panel.classList.contains("covered"), true);
  assert.deepEqual(calls, ["reset"]);
  tasks.open(); // already open: nothing happens again
  assert.deepEqual(calls, ["reset"]);
});

test("closing uncovers the panel and gives the pad back", () => {
  const { tasks, el, panel, calls } = setup();
  tasks.open();
  tasks.close();
  assert.equal(el.hidden, true);
  assert.equal(panel.classList.contains("covered"), false);
  assert.deepEqual(calls, ["reset", "ensure"]);
  tasks.close(); // already closed
  assert.deepEqual(calls, ["reset", "ensure"]);
});

test("toggle opens and closes", () => {
  const { tasks } = setup();
  tasks.toggle();
  assert.equal(tasks.isOpen(), true);
  tasks.toggle();
  assert.equal(tasks.isOpen(), false);
});

test("closing with the panel itself leaves the pad alone", () => {
  const { tasks, calls } = setup();
  tasks.open();
  tasks.close(false);
  assert.deepEqual(calls, ["reset"]);
  assert.equal(tasks.isOpen(), false);
});

test("B (the close event) and the ✕ close it", () => {
  const { tasks, el } = setup();
  tasks.open();
  el.dispatchEvent(new CustomEvent(WINDOW_CLOSE_EVENT));
  assert.equal(tasks.isOpen(), false);
  tasks.open();
  el.querySelector<HTMLElement>(".close")!.click();
  assert.equal(tasks.isOpen(), false);
});

test("the ring goes back to the control it was on", () => {
  const { tasks, panel, stage } = setup();
  const control = panel.appendChild(document.createElement("button"));
  control.classList.add("nav-focus");
  const asked: unknown[] = [];
  stage.addEventListener("invasor:refocus", (e) => asked.push((e as CustomEvent).detail));
  tasks.open();
  control.classList.remove("nav-focus"); // nav.reset() clears the ring
  tasks.close();
  assert.deepEqual(asked, [control]);
});

test("it holds a place for the tasks and says there are none yet", () => {
  const { tasks } = setup();
  assert.match(tasks.content.textContent ?? "", /No tasks yet/);
  assert.ok(tasks.content.matches(".content"));
});

const sample = (label: string) => ({
  active: [{ id: 1, module: "rakun", label, state: "running" as const, fraction: 0.5, text: "", started: 1, updated: 1, finished: null }],
  recent: [],
});

test("what arrives while it's closed is drawn when it opens", () => {
  const { tasks } = setup();
  tasks.update(sample("Installing"));
  assert.equal(tasks.content.querySelectorAll(".task-row").length, 0); // nobody is looking: nothing drawn
  tasks.open();
  assert.equal(tasks.content.querySelectorAll(".task-row").length, 1);
  assert.equal(tasks.content.querySelector(".task-owner")!.textContent, "RAKUN"); // names go through nameOf
});

test("while it's open the updates show at once", () => {
  const { tasks } = setup();
  tasks.open();
  tasks.update(sample("First"));
  assert.equal(tasks.content.querySelector(".task-label")!.textContent, "First");
  tasks.update(sample("Second"));
  assert.equal(tasks.content.querySelector(".task-label")!.textContent, "Second");
});

test("opening tells the overlay, closing doesn't", () => {
  let opened = 0;
  const { tasks } = setup(() => opened++);
  tasks.open();
  tasks.open();
  assert.equal(opened, 1);
  tasks.close();
  assert.equal(opened, 1);
});

test("destroy removes it and uncovers the panel", () => {
  const { tasks, panel, stage } = setup();
  tasks.open();
  tasks.destroy();
  assert.equal(stage.querySelector(".cover-panel"), null);
  assert.equal(panel.classList.contains("covered"), false);
});

test("while it's open the ages are redrawn every 30 s, with nothing else changing", () => {
  mock.timers.enable({ apis: ["setInterval", "Date"], now: 1_000_000 });
  try {
    const { tasks } = setup();
    tasks.update({ active: [], recent: [{ id: 1, module: "rakun", label: "Done", state: "done", fraction: 1, text: "", started: 990, updated: 1000, finished: 1000 }] });
    tasks.open();
    assert.equal(tasks.content.querySelector(".task-when")!.textContent, "just now");
    mock.timers.tick(REFRESH_MS * 3); // 90 s later
    assert.equal(tasks.content.querySelector(".task-when")!.textContent, "1 min ago");
    mock.timers.tick(REFRESH_MS * 10);
    assert.equal(tasks.content.querySelector(".task-when")!.textContent, "6 min ago");
  } finally {
    mock.timers.reset();
  }
});

test("the timer exists only while the panel is open", () => {
  mock.timers.enable({ apis: ["setInterval", "Date"], now: 1_000_000 });
  try {
    const { tasks } = setup();
    tasks.update({ active: [], recent: [{ id: 1, module: "m", label: "Done", state: "done", fraction: 1, text: "", started: 990, updated: 1000, finished: 1000 }] });
    tasks.open();
    tasks.close();
    mock.timers.tick(REFRESH_MS * 4);
    assert.equal(tasks.content.querySelector(".task-when")!.textContent, "just now"); // not redrawn while closed
    tasks.open(); // opening draws at once, with the time it is now
    assert.equal(tasks.content.querySelector(".task-when")!.textContent, "2 min ago");
    tasks.destroy();
    mock.timers.tick(REFRESH_MS * 4); // nothing left to fire (would throw on a removed panel's content otherwise)
  } finally {
    mock.timers.reset();
  }
});

test("the Clear recent button reaches whoever made the panel", () => {
  let cleared = 0;
  const stage = document.body.appendChild(document.createElement("div"));
  const panel = stage.appendChild(document.createElement("div"));
  const nav = { reset() {}, ensureFocus() {} } as unknown as GamepadNav;
  const tasks = createTasksPanel({ stage, panel, nav, nameOf: (o) => o, onClear: () => void cleared++ });
  made.push(tasks);
  tasks.update({ active: [], recent: [{ id: 1, module: "m", label: "Done", state: "done", fraction: 1, text: "", started: 1, updated: 1, finished: 1 }] });
  tasks.open();
  tasks.content.querySelector<HTMLButtonElement>(".tasks-clear")!.click();
  assert.equal(cleared, 1);
});
