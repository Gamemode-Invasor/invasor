// The Tasks panel: opened with Select from the Invasor panel (or its "Tasks" button), it takes
// the same place and size and covers it, and while it's open it is the layer the pad drives
// (see gamepad-nav.ts: B or Select close it and the pad goes back to the Invasor panel).
// For now it only holds the place: what long-running tasks show here comes with the tasks UI.

import { WINDOW_CLOSE_EVENT, type GamepadNav } from "./gamepad-nav";
import { EMPTY_TASKS, renderTasks, type TasksSnapshot } from "./tasks-view";

export interface TasksPanel {
  /** Cover the Invasor panel and take the pad. */
  open(): void;
  /** Give the pad back to the Invasor panel (`restoreFocus: false` when that panel is closing too). */
  close(restoreFocus?: boolean): void;
  toggle(): void;
  isOpen(): boolean;
  /** The tasks as they are now: drawn at once if the panel is open, else when it opens. */
  update(snapshot: TasksSnapshot): void;
  /** Where the tasks go. */
  readonly content: HTMLElement;
  destroy(): void;
}

interface TasksDeps {
  /** What the pad navigates: the Invasor panel, big windows and this panel live in it. */
  stage: HTMLElement;
  /** The Invasor panel this one covers. */
  panel: HTMLElement;
  nav: GamepadNav;
  /** The name to show for a task's owner (a module id, or "core"). */
  nameOf(owner: string): string;
  /** The panel was just opened (the user is about to see the tasks). */
  onOpen?(): void;
}

export function createTasksPanel({ stage, panel, nav, nameOf, onOpen }: TasksDeps): TasksPanel {
  const el = document.createElement("div");
  el.className = "tasks-panel";
  el.hidden = true;
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-label", "Tasks");
  // The pad's hint bar adds this while the panel is the top layer (see showHints in gamepad-nav.ts).
  el.dataset.extraHint = "Select back";
  el.innerHTML = `
    <header><span>Tasks</span><button class="close" aria-label="Close tasks">✕</button></header>
    <div class="content-wrap">
      <section class="content"></section>
      <div class="scrollbar"><div class="thumb"></div></div>
    </div>
    <div class="hints"></div>`;
  const content = el.querySelector<HTMLElement>(".content")!;
  stage.appendChild(el);

  const isOpen = () => !el.hidden;
  let latest: TasksSnapshot = EMPTY_TASKS;
  const draw = () => renderTasks(content, latest, { now: Date.now() / 1000, nameOf });
  draw();
  let opener: HTMLElement | null = null; // where the pad's ring was in the panel, to put it back

  function open() {
    if (isOpen()) return;
    // Same height as the panel it covers, so it looks like the panel changed rather than a second one appeared.
    const height = panel.getBoundingClientRect().height;
    if (height > 0) el.style.height = `${height}px`;
    opener = panel.querySelector<HTMLElement>(".nav-focus");
    panel.classList.add("covered");
    el.hidden = false;
    draw();
    nav.reset();
    onOpen?.();
  }

  function close(restoreFocus = true) {
    if (!isOpen()) return;
    el.hidden = true;
    panel.classList.remove("covered");
    if (!restoreFocus) return;
    if (opener?.isConnected) stage.dispatchEvent(new CustomEvent("invasor:refocus", { detail: opener }));
    nav.ensureFocus();
  }

  el.addEventListener(WINDOW_CLOSE_EVENT, () => close()); // B
  el.querySelector(".close")!.addEventListener("click", () => close());

  return {
    open,
    close,
    toggle: () => (isOpen() ? close() : open()),
    isOpen,
    update(snapshot) {
      latest = snapshot;
      if (isOpen()) draw();
    },
    content,
    destroy() {
      panel.classList.remove("covered");
      el.remove();
    },
  };
}
