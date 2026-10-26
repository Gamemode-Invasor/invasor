// A task that asks (t.ask, or ctx.http.download without a checksum) also shows its question as a
// dialog where the user is, so pressing "Download" in a module's tab doesn't leave them wondering.
// The Tasks panel stays the source of truth: the dialog answers through the same core.task_answer,
// closes by itself when the question stops waiting (answered elsewhere, timed out, module unloaded),
// steps aside for a module's own ui.confirm and comes back after it, and B only puts it off until
// the panel opens again: it never answers Deny by itself.

import { YIELD_EVENT, drawChoice } from "./controls";
import type { TaskInfo, TasksSnapshot } from "./tasks-view";

export interface AskDialog {
  /** The tasks as they are now: shows the first waiting question, or closes one that's gone. */
  update(snapshot: TasksSnapshot): void;
  /** The Invasor panel was opened: the questions put off with B come back. */
  panelOpened(): void;
  /** Something changed whether it may show (the Tasks panel opened or closed): hide or show. */
  refresh(): void;
  /** The answer to task `id` didn't reach the backend: offer its question again. */
  retry(id: number): void;
  destroy(): void;
}

interface AskDeps {
  /** Where to draw it (the overlay's top layer), or null. */
  host(): HTMLElement | null;
  /** True while it may show: the Invasor panel is open and the Tasks panel isn't (it shows the question itself). */
  canShow(): boolean;
  /** The name to show for a task's owner (a module id, or "core"). */
  nameOf(owner: string): string;
  /** Accept or Deny was chosen in the dialog. */
  onAnswer(id: number, accept: boolean): void;
}

// A question is its task, when it started waiting and its text: the same task may ask again (even
// the same words), and that is a new question. While a task waits its `updated` doesn't move.
const keyOf = (t: TaskInfo) => `${t.id}\n${t.updated}\n${t.question}`;

const waitingOf = (snapshot: TasksSnapshot) =>
  snapshot.active
    .filter((t) => t.state === "waiting" && t.question)
    .sort((a, b) => a.updated - b.updated || a.id - b.id); // the oldest question first

export function createAskDialog({ host, canShow, nameOf, onAnswer }: AskDeps): AskDialog {
  let waiting: TaskInfo[] = [];
  const putOff = new Set<string>(); // B: not again until the panel opens
  const answered = new Set<string>(); // sent, until the backend says it no longer waits
  let current: { key: string; stop: AbortController } | null = null;
  let watcher: MutationObserver | null = null;
  let destroyed = false;

  const unwatch = () => {
    watcher?.disconnect();
    watcher = null;
  };

  function show() {
    if (current || destroyed || !canShow()) return;
    const task = waiting.find((t) => !putOff.has(keyOf(t)) && !answered.has(keyOf(t)));
    const where = task ? host() : null;
    if (!task || !where) return;
    // One dialog at a time: wait for the one open (a module's ui.confirm, say) to close.
    if (where.querySelector(":scope > .modal")) {
      if (!watcher) {
        watcher = new MutationObserver(() => {
          if (where.isConnected && where.querySelector(":scope > .modal")) return;
          unwatch();
          show();
        });
        watcher.observe(where, { childList: true });
        // A big window that closes with its dialog open takes `where` away: look again at the top layer.
        if (where.parentElement) watcher.observe(where.parentElement, { childList: true });
      }
      return;
    }
    unwatch();
    const key = keyOf(task);
    const stop = new AbortController();
    current = { key, stop };
    let back = false;
    const stepAside = () => stop.abort();
    where.addEventListener(YIELD_EVENT, stepAside);
    const choices = [{ label: "Deny", value: false }, { label: "Accept", value: true }];
    void drawChoice(where, `${nameOf(task.module)}: ${task.question}`, choices, {
      signal: stop.signal,
      yields: true,
      onBack: () => (back = true),
    }).then((answer) => {
      where.removeEventListener(YIELD_EVENT, stepAside);
      current = null;
      if (answer !== null) {
        answered.add(key);
        onAnswer(task.id, answer);
      } else if (back) {
        putOff.add(key);
      }
      // Closed any other way (its window closed, the question went, a module's dialog came first):
      // shown again where the user is, if it still waits.
      show();
    });
  }

  return {
    update(snapshot) {
      waiting = waitingOf(snapshot);
      const keys = new Set(waiting.map(keyOf));
      for (const set of [putOff, answered]) for (const key of set) if (!keys.has(key)) set.delete(key);
      if (current && !keys.has(current.key)) current.stop.abort(); // its .then shows the next one
      else show();
    },
    panelOpened() {
      putOff.clear();
      show();
    },
    retry(id) {
      for (const key of answered) if (key.startsWith(`${id}\n`)) answered.delete(key);
      show();
    },
    refresh() {
      if (current && !canShow()) current.stop.abort();
      else if (!canShow()) unwatch();
      else show();
    },
    destroy() {
      destroyed = true;
      unwatch();
      current?.stop.abort();
    },
  };
}
