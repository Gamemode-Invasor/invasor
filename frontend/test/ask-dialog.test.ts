import { settle } from "./dom";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { createAskDialog, type AskDialog } from "../src/ui/ask-dialog";
import { ui, setTopLayer } from "../src/ui/controls";
import type { TaskInfo, TasksSnapshot } from "../src/ui/tasks-view";

const made: AskDialog[] = [];
afterEach(() => {
  for (const d of made.splice(0)) d.destroy();
  document.body.replaceChildren();
});

const task = (id: number, question: string | null, state: TaskInfo["state"] = "waiting", updated = id): TaskInfo => ({
  id, module: "demo", label: `Task ${id}`, state, fraction: null, text: "", question, started: 0, updated, finished: null,
});
const snap = (...active: TaskInfo[]): TasksSnapshot => ({ active, recent: [] });

function setup() {
  const host = document.body.appendChild(document.createElement("div"));
  const answers: [number, boolean][] = [];
  const state = { open: true };
  const dialog = createAskDialog({
    host: () => host,
    canShow: () => state.open,
    nameOf: (owner) => owner.toUpperCase(),
    onAnswer: (id, accept) => answers.push([id, accept]),
  });
  made.push(dialog);
  const modal = () => host.querySelector<HTMLElement>(":scope > .modal");
  const text = () => modal()?.querySelector("p")?.textContent ?? null;
  const buttons = () => [...(modal()?.querySelectorAll<HTMLElement>("button") ?? [])];
  const press = (button: string) => modal()!.dispatchEvent(new CustomEvent("invasor:button", { detail: { button }, cancelable: true }));
  return { host, answers, state, dialog, modal, text, buttons, press };
}

test("a waiting task shows its question with the module's name, Deny first", () => {
  const { dialog, text, buttons } = setup();
  dialog.update(snap(task(1, "Download x?")));
  assert.equal(text(), "DEMO: Download x?");
  assert.deepEqual(buttons().map((b) => b.textContent), ["Deny", "Accept"]);
});

test("Accept and Deny answer through onAnswer, and the next question follows", async () => {
  const { dialog, text, buttons, answers } = setup();
  dialog.update(snap(task(1, "First?"), task(2, "Second?")));
  buttons()[1].click();
  await settle();
  assert.deepEqual(answers, [[1, true]]);
  assert.equal(text(), "DEMO: Second?"); // task 1 is answered: not shown again before the push
  buttons()[0].click();
  await settle();
  assert.deepEqual(answers, [[1, true], [2, false]]);
  assert.equal(text(), null);
});

test("an answer that didn't reach the backend is offered again with retry", async () => {
  const { dialog, text, buttons } = setup();
  dialog.update(snap(task(1, "Sure?")));
  buttons()[1].click();
  await settle();
  assert.equal(text(), null);
  dialog.update(snap(task(1, "Sure?"))); // a push: still waiting, but answered here
  assert.equal(text(), null);
  dialog.retry(1);
  assert.equal(text(), "DEMO: Sure?");
});

test("the oldest question comes first", () => {
  const { dialog, text } = setup();
  dialog.update(snap(task(5, "Newer?", "waiting", 20), task(9, "Older?", "waiting", 10)));
  assert.equal(text(), "DEMO: Older?");
});

test("B puts it off without answering, until the panel opens again", async () => {
  const { dialog, text, press, answers } = setup();
  dialog.update(snap(task(1, "Sure?")));
  press("B");
  await settle();
  assert.equal(text(), null);
  assert.deepEqual(answers, []);
  dialog.update(snap(task(1, "Sure?"))); // a push: still put off
  assert.equal(text(), null);
  dialog.panelOpened();
  assert.equal(text(), "DEMO: Sure?");
});

test("it doesn't show while it may not, and shows when it may", async () => {
  const { dialog, text, state } = setup();
  state.open = false;
  dialog.update(snap(task(1, "Sure?")));
  assert.equal(text(), null);
  state.open = true;
  dialog.refresh();
  assert.equal(text(), "DEMO: Sure?");
  state.open = false; // the Tasks panel opened over it: hidden, not answered, back when it closes
  dialog.refresh();
  await settle();
  assert.equal(text(), null);
  state.open = true;
  dialog.refresh();
  assert.equal(text(), "DEMO: Sure?");
});

test("it closes by itself when the task stops waiting", async () => {
  const { dialog, text, answers } = setup();
  dialog.update(snap(task(1, "Sure?")));
  dialog.update(snap(task(1, null, "running"))); // answered in the Tasks panel, or timed out
  await settle();
  assert.equal(text(), null);
  assert.deepEqual(answers, []);
});

test("closing the panel hides it without answering; it's back when the panel opens", async () => {
  const { dialog, host, text, answers, state } = setup();
  dialog.update(snap(task(1, "Sure?")));
  state.open = false; // the overlay hides the panel, then dismisses what's drawn in it
  host.dispatchEvent(new CustomEvent("invasor:dismiss"));
  await settle();
  assert.equal(text(), null);
  assert.deepEqual(answers, []);
  state.open = true;
  dialog.panelOpened();
  assert.equal(text(), "DEMO: Sure?");
});

test("closed with its window (the panel still open), it comes back at once, unanswered", async () => {
  const { dialog, host, text, answers } = setup();
  dialog.update(snap(task(1, "Sure?")));
  host.dispatchEvent(new CustomEvent("invasor:dismiss"));
  await settle();
  assert.equal(text(), "DEMO: Sure?");
  assert.deepEqual(answers, []);
});

test("the same task asking again is a new question, even with the same words", async () => {
  const { dialog, text, buttons, answers } = setup();
  dialog.update(snap(task(1, "Sure?", "waiting", 10)));
  buttons()[1].click();
  await settle();
  dialog.update(snap(task(1, "Sure?", "waiting", 20))); // asked again before any push saw it running
  assert.equal(text(), "DEMO: Sure?");
  buttons()[0].click();
  await settle();
  assert.deepEqual(answers, [[1, true], [1, false]]);
});

test("a new question of the same task replaces the old one on screen", async () => {
  const { dialog, text } = setup();
  dialog.update(snap(task(1, "First?", "waiting", 10)));
  dialog.update(snap(task(1, "Second?", "waiting", 20))); // the first timed out, the task asked again
  await settle();
  assert.equal(text(), "DEMO: Second?");
});

test("a module's ui.confirm comes first; the question steps aside and returns", async () => {
  const { dialog, host, text, answers } = setup();
  setTopLayer(() => host);
  dialog.update(snap(task(1, "Sure?")));
  const theirs = ui.confirm("Module's own?");
  await settle();
  assert.equal(text(), "Module's own?");
  host.querySelector<HTMLElement>(".modal button")!.click(); // its Cancel
  assert.equal(await theirs, false);
  await settle();
  assert.equal(text(), "DEMO: Sure?");
  assert.deepEqual(answers, []);
  setTopLayer(null);
});

test("it waits for a dialog already open, never answering it or the question", async () => {
  const { dialog, host, text } = setup();
  setTopLayer(() => host);
  const theirs = ui.confirm("Module's own?");
  dialog.update(snap(task(1, "Sure?")));
  assert.equal(text(), "Module's own?");
  host.querySelector<HTMLElement>(".modal button")!.click(); // Cancel of the module's dialog
  assert.equal(await theirs, false);
  await settle(); // the MutationObserver fires
  assert.equal(text(), "DEMO: Sure?");
  setTopLayer(null);
});

test("destroy removes it without answering", async () => {
  const { dialog, text, answers } = setup();
  dialog.update(snap(task(1, "Sure?")));
  dialog.destroy();
  await settle();
  assert.equal(text(), null);
  assert.deepEqual(answers, []);
});
