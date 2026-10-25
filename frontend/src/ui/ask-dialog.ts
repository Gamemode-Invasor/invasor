// A task that asks (t.ask, or ctx.http.download without a checksum) also shows its question as a
// dialog where the user is, so pressing "Download" in a module's tab doesn't leave them wondering.
// The Tasks panel stays the source of truth: the dialog answers through the same core.task_answer,
// closes by itself when the task stops waiting (answered elsewhere, timed out, module unloaded), and
// B or closing the panel only puts it off (it shows again when the panel opens), never answers Deny.

import { drawChoice } from "./controls";
import type { TaskInfo, TasksSnapshot } from "./tasks-view";

export interface AskDialog {
  /** The tasks as they are now: shows the first waiting question, or closes one that's gone. */
  update(snapshot: TasksSnapshot): void;
  /** The Invasor panel was opened: the questions put off with B come back. */
  panelOpened(): void;
  /** Something changed whether it may show (the Tasks panel opened or closed): hide or show. */
  refresh(): void;
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

const waitingOf = (snapshot: TasksSnapshot) =>
  snapshot.active
    .filter((t) => t.state === "waiting" && t.question)
    .sort((a, b) => a.updated - b.updated || a.id - b.id); // the oldest question first

export function createAskDialog({ host, canShow, nameOf, onAnswer }: AskDeps): AskDialog {
  let waiting: TaskInfo[] = [];
  const putOff = new Set<number>(); // B or the panel closing: not again until the panel opens
  const answered = new Set<number>(); // sent, until the backend's push says it's no longer waiting
  let current: { id: number; stop: AbortController } | null = null;
  let watcher: MutationObserver | null = null;
  let destroyed = false;

  const unwatch = () => {
    watcher?.disconnect();
    watcher = null;
  };

  function show() {
    if (current || destroyed || !canShow()) return;
    const task = waiting.find((t) => !putOff.has(t.id) && !answered.has(t.id));
    const where = task ? host() : null;
    if (!task || !where) return;
    // One dialog at a time: wait for the one open (a module's ui.confirm, say) to close.
    if (where.querySelector(":scope > .modal")) {
      if (!watcher) {
        watcher = new MutationObserver(() => {
          if (where.querySelector(":scope > .modal")) return;
          unwatch();
          show();
        });
        watcher.observe(where, { childList: true });
      }
      return;
    }
    unwatch();
    const stop = new AbortController();
    current = { id: task.id, stop };
    const choices = [{ label: "Deny", value: false }, { label: "Accept", value: true }];
    void drawChoice(where, `${nameOf(task.module)}: ${task.question}`, choices, { signal: stop.signal }).then((answer) => {
      current = null;
      if (answer !== null) {
        answered.add(task.id);
        onAnswer(task.id, answer);
      } else if (!stop.signal.aborted) {
        putOff.add(task.id);
      }
      show();
    });
  }

  return {
    update(snapshot) {
      waiting = waitingOf(snapshot);
      const ids = new Set(waiting.map((t) => t.id));
      for (const set of [putOff, answered]) for (const id of set) if (!ids.has(id)) set.delete(id);
      if (current && !ids.has(current.id)) current.stop.abort(); // its .then shows the next one
      else show();
    },
    panelOpened() {
      putOff.clear();
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
