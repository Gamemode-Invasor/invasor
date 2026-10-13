// The Tasks panel: opened with Select from the Invasor panel (or its "Tasks" button), it covers
// that panel (see cover-panel.ts) and lists what runs in the background and what ended lately.

import { createCoverPanel, type CoverPanel } from "./cover-panel";
import type { GamepadNav } from "./gamepad-nav";
import { EMPTY_TASKS, renderTasks, type TasksSnapshot } from "./tasks-view";

export interface TasksPanel extends CoverPanel {
  /** The tasks as they are now: drawn at once if the panel is open, else when it opens. */
  update(snapshot: TasksSnapshot): void;
}

interface TasksDeps {
  stage: HTMLElement;
  panel: HTMLElement;
  nav: GamepadNav;
  /** The name to show for a task's owner (a module id, or "core"). */
  nameOf(owner: string): string;
  /** The panel was just opened (the user is about to see the tasks). */
  onOpen?(): void;
}

export function createTasksPanel({ stage, panel, nav, nameOf, onOpen }: TasksDeps): TasksPanel {
  let latest: TasksSnapshot = EMPTY_TASKS;
  const draw = () => renderTasks(cover.content, latest, { now: Date.now() / 1000, nameOf });
  const cover = createCoverPanel({
    stage,
    panel,
    nav,
    kind: "tasks",
    title: "Tasks",
    backHint: "Select back",
    onOpen: () => {
      draw();
      onOpen?.();
    },
  });
  draw();
  return {
    ...cover,
    update(snapshot) {
      latest = snapshot;
      if (cover.isOpen()) draw();
    },
  };
}
