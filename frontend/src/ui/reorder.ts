// The list that reorders the modules (⚙ Settings › Module order). Internal to the panel,
// not part of the `ui` kit modules get.
//
// Gamepad: A grabs a row, ↑↓ move it, A drops it (saved), B puts it back. Touch: a row has
// ▲▼ buttons that move it and save at once. Rows are `.ctl` navigation stops like any control.

import { hintsChanged, onButton, row } from "./controls";
import { moveItem } from "./values";

export interface ReorderItem {
  id: string;
  label: string;
  /** Shown dimmed (a module that is switched off or broken keeps its place). */
  dim?: boolean;
}

interface Line {
  id: string;
  el: HTMLElement;
  up: HTMLElement;
  down: HTMLElement;
}

const IDLE = "A move · ▲▼ with touch";
const GRABBED = "↑↓ move · A drop · B cancel";

/**
 * The rows, in order, to put in a section next to each other. `onCommit` gets the module
 * ids in their new order each time a move is dropped; if it throws, the rows go back.
 */
export function reorderList(items: ReorderItem[], onCommit: (ids: string[]) => Promise<void>): HTMLElement[] {
  const lines: Line[] = items.map((it) => {
    const { el, body } = row(it.label, IDLE);
    el.classList.add("reorder");
    el.classList.toggle("dim", !!it.dim);
    const mk = (text: string, name: string) => {
      const b = document.createElement("span");
      b.className = "reorder-btn";
      b.textContent = text;
      b.setAttribute("aria-label", `${name} ${it.label}`);
      body.appendChild(b);
      return b;
    };
    return { id: it.id, el, up: mk("▲", "Move up"), down: mk("▼", "Move down") };
  });

  let committed = lines.map((l) => l.id); // the order last saved
  let grabbed: Line | null = null;
  let dismissHost: Element | null = null;

  const ids = () => lines.map((l) => l.id);
  const paint = () => {
    lines.forEach((l, i) => {
      l.up.style.visibility = i === 0 ? "hidden" : "visible";
      l.down.style.visibility = i === lines.length - 1 ? "hidden" : "visible";
    });
  };

  /** Show `order` (module ids): reorder `lines` and the DOM, where the rows sit next to each other. */
  const apply = (order: string[]) => {
    const parent = lines[0].el.parentElement;
    const after = lines[lines.length - 1].el.nextSibling; // whatever follows the block of rows
    const byId = new Map(lines.map((l) => [l.id, l]));
    lines.splice(0, lines.length, ...order.map((id) => byId.get(id)!));
    for (const l of lines) parent?.insertBefore(l.el, after);
    paint();
  };
  const move = (from: number, to: number) => {
    const order = moveItem(ids(), from, to);
    if (order.join() !== ids().join()) apply(order);
  };
  const refocus = (l: Line) => l.el.dispatchEvent(new CustomEvent("invasor:refocus", { bubbles: true, detail: l.el }));

  const save = async () => {
    const order = ids();
    if (order.join() === committed.join()) return;
    try {
      await onCommit(order);
      committed = order;
    } catch {
      apply(committed); // onCommit already told the user
    }
  };
  const release = () => {
    if (!grabbed) return;
    grabbed.el.classList.remove("grabbed");
    grabbed.el.dataset.hint = IDLE;
    hintsChanged(grabbed.el);
    dismissHost?.removeEventListener("invasor:dismiss", cancel);
    dismissHost = null;
    grabbed = null;
  };
  const drop = () => {
    release();
    void save();
  };
  function cancel() {
    const line = grabbed;
    release();
    apply(committed);
    if (line) refocus(line);
  }
  const grab = (l: Line) => {
    grabbed = l;
    l.el.classList.add("grabbed");
    l.el.dataset.hint = GRABBED;
    dismissHost = l.el.closest(".iwin-box, .panel");
    dismissHost?.addEventListener("invasor:dismiss", cancel);
    hintsChanged(l.el);
  };

  for (const l of lines) {
    // A tap on the row grabs/drops it, like A.
    l.el.addEventListener("click", () => (grabbed === l ? drop() : grabbed ? undefined : grab(l)));
    // The arrows move the row and save at once (touch).
    for (const [btn, step] of [[l.up, -1], [l.down, 1]] as const) {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const i = lines.indexOf(l);
        move(i, i + step);
        refocus(l);
        if (!grabbed) void save();
      });
    }
    onButton(l.el, (b) => {
      if (grabbed !== l) return false; // A falls through to the click above
      if (b === "UP" || b === "DOWN") {
        const i = lines.indexOf(l);
        move(i, i + (b === "DOWN" ? 1 : -1));
        refocus(l);
      } else if (b === "A") drop();
      else if (b === "B") cancel();
      return true; // while grabbed, this row owns the pad
    });
  }
  paint();
  return lines.map((l) => l.el);
}
