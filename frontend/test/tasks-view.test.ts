import "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import { marker, parseSnapshot, relativeTime, renderTasks, summarize, type TaskInfo, type TasksSnapshot } from "../src/ui/tasks-view";

const task = (over: Partial<TaskInfo> = {}): TaskInfo => ({
  id: 1, module: "rakun", label: "Installing", state: "running", fraction: 0.5, text: "halfway",
  started: 100, updated: 100, finished: null, ...over,
});
const snap = (active: TaskInfo[] = [], recent: TaskInfo[] = []): TasksSnapshot => ({ active, recent });
const options = { now: 200, nameOf: (owner: string) => (owner === "core" ? "Invasor" : owner.toUpperCase()) };
const draw = (content: HTMLElement, s: TasksSnapshot) => renderTasks(content, s, options);
const content = () => document.body.appendChild(document.createElement("section"));
const rows = (c: HTMLElement) => [...c.querySelectorAll<HTMLElement>(".task-row")];
const shown = (e: Element | null) => !!e && !(e as HTMLElement).hidden;

test("relativeTime", () => {
  assert.equal(relativeTime(100, 100), "just now");
  assert.equal(relativeTime(100, 159.9), "just now"); // no seconds: the panel redraws every 30 s
  assert.equal(relativeTime(100, 160), "1 min ago");
  assert.equal(relativeTime(100, 100 + 180), "3 min ago");
  assert.equal(relativeTime(100, 100 + 7300), "2 h ago");
  assert.equal(relativeTime(100, 100 + 4 * 86400), "4 d ago");
  assert.equal(relativeTime(500, 100), "just now"); // a clock a little behind
  assert.equal(relativeTime(NaN, 100), "just now");
});

test("a snapshot from the wire is checked", () => {
  assert.equal(parseSnapshot(null), null);
  assert.equal(parseSnapshot({ active: [] }), null);
  assert.equal(parseSnapshot("x"), null);
  assert.deepEqual(parseSnapshot({ active: [task(), { nonsense: 1 }, null], recent: [task({ id: 2, state: "bogus" as never })] }),
    { active: [task()], recent: [] });
});

test("the summary counts what runs and the failures not seen yet", () => {
  const s = snap([task({ id: 9 })], [task({ id: 3, state: "failed" }), task({ id: 2, state: "done" }), task({ id: 1, state: "failed" })]);
  assert.deepEqual(summarize(s, new Set()), { active: 1, unseenFailed: 2 });
  assert.deepEqual(summarize(s, new Set([1])), { active: 1, unseenFailed: 1 });
  assert.deepEqual(summarize(s, new Set([1, 3])), { active: 1, unseenFailed: 0 });
  assert.deepEqual(summarize(snap(), new Set()), { active: 0, unseenFailed: 0 });
});

test("the marker: a failure not seen outranks what is running", () => {
  assert.equal(marker({ active: 0, unseenFailed: 0 }), "");
  assert.equal(marker({ active: 2, unseenFailed: 0 }), "running");
  assert.equal(marker({ active: 2, unseenFailed: 1 }), "failed");
  assert.equal(marker({ active: 0, unseenFailed: 1 }), "failed");
});

test("nothing running or recent says so", () => {
  const c = content();
  draw(c, snap());
  assert.equal(shown(c.querySelector(".tasks-empty")), true);
  assert.equal(rows(c).length, 0);
  assert.equal(shown(c.querySelector(".tasks-group")), false);
});

test("a running task shows its label, owner, bar and text", () => {
  const c = content();
  draw(c, snap([task({ module: "core", label: "Updating Invasor", fraction: 0.4, text: "Downloading" })]));
  const [row] = rows(c);
  assert.equal(row.querySelector(".task-label")!.textContent, "Updating Invasor");
  assert.equal(row.querySelector(".task-owner")!.textContent, "Invasor");
  assert.equal(row.querySelector<HTMLElement>(".task-fill")!.style.width, "40%");
  assert.equal(row.querySelector(".task-bar")!.getAttribute("aria-valuenow"), "40");
  assert.equal(row.querySelector(".task-bar")!.hasAttribute("data-indeterminate"), false);
  assert.equal(row.querySelector(".task-text")!.textContent, "Downloading");
  assert.equal(shown(row.querySelector(".task-when")), false);
  assert.equal(shown(c.querySelector(".tasks-empty")), false);
});

test("a task that can't tell how far it is gets an indeterminate bar", () => {
  const c = content();
  draw(c, snap([task({ fraction: null, text: "" })]));
  const bar = rows(c)[0].querySelector(".task-bar")!;
  assert.equal(bar.hasAttribute("data-indeterminate"), true);
  assert.equal(bar.hasAttribute("aria-valuenow"), false);
  assert.equal(shown(rows(c)[0].querySelector(".task-text")), false);
});

test("ended tasks go under Recent with their state and age", () => {
  const c = content();
  draw(c, snap([], [
    task({ id: 3, state: "failed", fraction: 0.3, text: "no network", finished: 195 }),
    task({ id: 2, state: "done", fraction: 1, text: "", finished: 20 }),
  ]));
  const [failed, done] = rows(c);
  assert.equal(failed.dataset.state, "failed");
  assert.equal(failed.querySelector(".task-state")!.textContent, "✕");
  assert.equal(failed.querySelector(".task-when")!.textContent, "just now");
  assert.equal(failed.querySelector<HTMLElement>(".task-fill")!.style.width, "30%");
  assert.equal(done.querySelector(".task-state")!.textContent, "✓");
  assert.equal(done.querySelector(".task-when")!.textContent, "3 min ago");
  assert.equal(done.querySelector<HTMLElement>(".task-fill")!.style.width, "100%");
  const titles = [...c.querySelectorAll<HTMLElement>(".tasks-group")].map((t) => [t.textContent, t.hidden]);
  assert.deepEqual(titles, [["Running", true], ["Recent", false]]);
});

test("an update changes the same row, not a new one", () => {
  const c = content();
  draw(c, snap([task({ fraction: 0.1 })]));
  const first = rows(c)[0];
  draw(c, snap([task({ fraction: 0.8, text: "almost" })]));
  assert.equal(rows(c).length, 1);
  assert.equal(rows(c)[0], first);
  assert.equal(first.querySelector<HTMLElement>(".task-fill")!.style.width, "80%");
  assert.equal(first.querySelector(".task-text")!.textContent, "almost");
});

test("a task that ends moves to Recent as the same element", () => {
  const c = content();
  draw(c, snap([task()]));
  const row = rows(c)[0];
  draw(c, snap([], [task({ state: "done", fraction: 1, finished: 150 })]));
  assert.equal(rows(c).length, 1);
  assert.equal(rows(c)[0], row);
  assert.equal(row.parentElement, c.querySelectorAll(".tasks-list")[1]);
  assert.equal(row.dataset.state, "done");
});

test("a task that leaves the list leaves the screen", () => {
  const c = content();
  draw(c, snap([task({ id: 1 }), task({ id: 2, label: "B" })]));
  draw(c, snap([task({ id: 2, label: "B" })]));
  assert.deepEqual(rows(c).map((r) => r.querySelector(".task-label")!.textContent), ["B"]);
  draw(c, snap());
  assert.equal(rows(c).length, 0);
  assert.equal(shown(c.querySelector(".tasks-empty")), true);
});

test("the rows keep the order of the list", () => {
  const c = content();
  draw(c, snap([task({ id: 1, label: "A" }), task({ id: 2, label: "B" })]));
  const [a, b] = rows(c);
  draw(c, snap([task({ id: 3, label: "C" }), task({ id: 2, label: "B" }), task({ id: 1, label: "A" })]));
  assert.deepEqual(rows(c).map((r) => r.querySelector(".task-label")!.textContent), ["C", "B", "A"]);
  assert.equal(rows(c)[1], b);
  assert.equal(rows(c)[2], a);
});

test("text from a module is text, never markup", () => {
  const c = content();
  draw(c, snap([task({ label: "<img src=x onerror=boom()>", text: "<b>bold</b>" })]));
  assert.equal(c.querySelector("img"), null);
  assert.equal(c.querySelector("b"), null);
  assert.equal(rows(c)[0].querySelector(".task-label")!.textContent, "<img src=x onerror=boom()>");
});

test("a fraction outside 0..1 is cut to the bar", () => {
  const c = content();
  draw(c, snap([task({ fraction: 7 })]));
  assert.equal(rows(c)[0].querySelector<HTMLElement>(".task-fill")!.style.width, "100%");
  draw(c, snap([task({ fraction: -2 })]));
  assert.equal(rows(c)[0].querySelector<HTMLElement>(".task-fill")!.style.width, "0%");
});

test("Clear recent: hidden with nothing to clear, shown with something, and a click asks for it", () => {
  const c = content();
  let cleared = 0;
  const withClear = (snapshot: TasksSnapshot) => renderTasks(c, snapshot, { ...options, onClear: () => void cleared++ });
  withClear(snap([task()]));
  const button = c.querySelector<HTMLButtonElement>(".tasks-clear")!;
  assert.equal(button.hidden, true); // something is running but nothing ended
  withClear(snap([], [task({ id: 2, state: "done", fraction: 1, finished: 150 })]));
  assert.equal(button.hidden, false);
  assert.equal(button.textContent, "Clear recent");
  button.click();
  assert.equal(cleared, 1);
  withClear(snap());
  assert.equal(button.hidden, true);
  assert.equal(c.querySelector(".tasks-clear"), button); // always the same button
});

test("Clear recent is after the last row of Recent, so the pad reaches it at the end of the list", () => {
  const c = content();
  draw(c, snap([], [task({ id: 2, state: "done", finished: 150 })]));
  assert.equal(c.lastElementChild, c.querySelector(".tasks-clear"));
});

test("a click with no handler does nothing", () => {
  const c = content();
  draw(c, snap([], [task({ id: 2, state: "done", finished: 150 })]));
  c.querySelector<HTMLButtonElement>(".tasks-clear")!.click();
});
