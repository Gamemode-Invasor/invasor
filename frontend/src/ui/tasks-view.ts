// What the Tasks panel shows: the running tasks and the last ones that ended, as core.tasks()
// (backend/invasor/tasks.py) gives them, plus the little arithmetic the handle's dot needs.
// Plain DOM and plain functions, so it can be tested without Steam.

/** One task, as the backend's registry describes it (times in seconds since the epoch). */
export interface TaskInfo {
  id: number;
  /** A module id, or "core" for what Invasor itself does (updates, Market installs). */
  module: string;
  label: string;
  state: "running" | "done" | "failed";
  /** 0 to 1, or null when the task can't tell how far it is. */
  fraction: number | null;
  text: string;
  started: number;
  updated: number;
  finished: number | null;
}

export interface TasksSnapshot {
  active: TaskInfo[];
  /** The newest first. */
  recent: TaskInfo[];
}

export const EMPTY_TASKS: TasksSnapshot = { active: [], recent: [] };

/** A snapshot from the backend, or null if it isn't shaped like one (it comes over a wire: never trusted blindly). */
export function parseSnapshot(value: unknown): TasksSnapshot | null {
  const v = value as Partial<TasksSnapshot> | null;
  if (!v || !Array.isArray(v.active) || !Array.isArray(v.recent)) return null;
  const ok = (t: unknown): t is TaskInfo => {
    const x = t as Partial<TaskInfo> | null;
    return !!x && typeof x.id === "number" && typeof x.label === "string" && typeof x.module === "string" &&
      (x.state === "running" || x.state === "done" || x.state === "failed");
  };
  return { active: v.active.filter(ok), recent: v.recent.filter(ok) };
}

export interface TasksSummary {
  /** How many are running now. */
  active: number;
  /** Failed ones the user hasn't seen yet (the Tasks panel hasn't been open since). */
  unseenFailed: number;
}

export function summarize(snapshot: TasksSnapshot, seen: ReadonlySet<number>): TasksSummary {
  return {
    active: snapshot.active.length,
    unseenFailed: snapshot.recent.filter((t) => t.state === "failed" && !seen.has(t.id)).length,
  };
}

/** What the handle and the "Tasks" button show: red for a failure not seen yet, else a pulse while something runs. */
export function marker(summary: TasksSummary): "failed" | "running" | "" {
  return summary.unseenFailed > 0 ? "failed" : summary.active > 0 ? "running" : "";
}

/**
 * "just now", "3 min ago", "2 h ago", "4 d ago" (`then` and `now` in seconds). No seconds: the panel
 * refreshes it every 30 s, which couldn't keep them right.
 */
export function relativeTime(then: number, now: number): string {
  const s = Math.floor(now - then);
  if (!(s >= 60)) return "just now"; // also a clock a little behind, or a number that isn't one
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export interface RenderOptions {
  /** Seconds since the epoch. */
  now: number;
  /** The name to show for a task's owner (a module id, or "core"). */
  nameOf(owner: string): string;
  /** The user asked to forget the tasks that ended (the "Clear recent" button). */
  onClear?(): void;
}

interface Row {
  el: HTMLElement;
  label: HTMLElement;
  owner: HTMLElement;
  state: HTMLElement;
  bar: HTMLElement;
  fill: HTMLElement;
  text: HTMLElement;
  when: HTMLElement;
}

interface Group {
  title: HTMLElement;
  list: HTMLElement;
}

interface View {
  empty: HTMLElement;
  groups: { active: Group; recent: Group };
  /** Forgets the ones that ended; shown only when there are some. */
  clear: HTMLButtonElement;
  onClear: (() => void) | undefined;
  rows: Map<number, Row>;
}

const views = new WeakMap<HTMLElement, View>();

function el(tag: string, className: string, text = ""): HTMLElement {
  const e = document.createElement(tag);
  e.className = className;
  if (text) e.textContent = text;
  return e;
}

function build(content: HTMLElement): View {
  content.replaceChildren();
  const empty = el("p", "tasks-empty", "No tasks yet");
  const group = (title: string): Group => ({ title: el("h3", "tasks-group", title), list: el("div", "tasks-list") });
  const groups = { active: group("Running"), recent: group("Recent") };
  const clear = document.createElement("button");
  clear.className = "tasks-clear";
  clear.textContent = "Clear recent";
  clear.hidden = true;
  const view: View = { empty, groups, clear, onClear: undefined, rows: new Map() };
  clear.addEventListener("click", () => view.onClear?.());
  content.append(empty, groups.active.title, groups.active.list, groups.recent.title, groups.recent.list, clear);
  return view;
}

function newRow(): Row {
  const row = el("div", "task-row");
  const head = el("div", "task-head");
  const label = el("span", "task-label");
  const owner = el("span", "task-owner");
  const state = el("span", "task-state");
  head.append(label, owner, state);
  const bar = el("div", "task-bar");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", "100");
  const fill = el("div", "task-fill");
  bar.append(fill);
  const text = el("div", "task-text");
  const when = el("div", "task-when");
  row.append(head, bar, text, when);
  return { el: row, label, owner, state, bar, fill, text, when };
}

// Only writes what changed, so a screen that updates every second doesn't restart animations or lose its scroll.
function setText(node: HTMLElement, value: string) {
  if (node.textContent !== value) node.textContent = value;
}

function fillRow(row: Row, task: TaskInfo, { now, nameOf }: RenderOptions) {
  row.el.dataset.state = task.state;
  setText(row.label, task.label);
  setText(row.owner, nameOf(task.module));
  setText(row.state, task.state === "done" ? "✓" : task.state === "failed" ? "✕" : "");
  const known = typeof task.fraction === "number";
  if (!known && task.state === "running") row.bar.setAttribute("data-indeterminate", "");
  else row.bar.removeAttribute("data-indeterminate");
  const pct = known ? Math.round(Math.min(1, Math.max(0, task.fraction as number)) * 100) : task.state === "done" ? 100 : 0;
  const width = `${pct}%`;
  if (row.fill.style.width !== width) row.fill.style.width = width;
  if (known || task.state === "done") row.bar.setAttribute("aria-valuenow", String(pct));
  else row.bar.removeAttribute("aria-valuenow");
  setText(row.text, task.text);
  row.text.hidden = !task.text;
  setText(row.when, task.state === "running" ? "" : relativeTime(task.finished ?? task.updated, now));
  row.when.hidden = task.state === "running";
}

/**
 * Draw `snapshot` in `content` (the Tasks panel's scrolling area), updating the rows already
 * there in place: a row is the same element from the moment its task starts until it leaves the list.
 */
export function renderTasks(content: HTMLElement, snapshot: TasksSnapshot, options: RenderOptions) {
  const view = views.get(content) ?? (views.set(content, build(content)), views.get(content)!);
  const live = new Set<number>();
  for (const key of ["active", "recent"] as const) {
    const group = view.groups[key];
    const wanted: HTMLElement[] = [];
    for (const task of snapshot[key]) {
      live.add(task.id);
      let row = view.rows.get(task.id);
      if (!row) view.rows.set(task.id, (row = newRow()));
      fillRow(row, task, options);
      wanted.push(row.el);
    }
    // Put them in order, moving only what isn't where it should be.
    wanted.forEach((node, i) => {
      if (group.list.children[i] !== node) group.list.insertBefore(node, group.list.children[i] ?? null);
    });
    while (group.list.children.length > wanted.length) group.list.lastElementChild!.remove();
    group.title.hidden = group.list.hidden = !wanted.length;
  }
  for (const [id, row] of view.rows) {
    if (!live.has(id)) {
      row.el.remove();
      view.rows.delete(id);
    }
  }
  view.empty.hidden = live.size > 0;
  view.onClear = options.onClear;
  view.clear.hidden = !snapshot.recent.length;
}
