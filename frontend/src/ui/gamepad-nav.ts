import { keepVisible } from "./scroll";

// Gamepad navigation for the panel and its big windows.
//
// STEAM TOUCHPOINT (see README "What Invasor relies on in Steam").
// In Game Mode Steam delivers controller input to its UI as DOM events
// (vgp_onbuttondown / vgp_onbuttonup, detail.button). While our panel is open we
// catch them in the capture phase and stop them, so Steam's UI underneath doesn't
// react, and use them to move a focus ring through our controls.
// If Steam ever stops sending these events, nothing breaks: touch, F10 and the
// L3+R3 combo still work, the panel just isn't navigable with the pad.
//
// Layers: the panel, a big window (.iwin) over it, a dialog (.modal) over either.
// Only the top layer is navigable; it has its own scroller, scrollbar and hint bar.

// Button roles: A/B/L1/R1/L2/R2 and the D-pad navigate, X folds/unfolds sections,
// Y is each control's "special value" (e.g. back to default). Controls get first say.
const BTN = { A: 1, B: 2, X: 3, L1: 5, R1: 6, L2: 7, R2: 8, UP: 9, DOWN: 10, LEFT: 11, RIGHT: 12 } as const;
// Steam's button numbers (detail.button), confirmed in Game Mode logs. Unknown ones go out as "BTN_<n>".
const NAMES: Record<number, string> = {
  1: "A", 2: "B", 3: "X", 4: "Y", 5: "L1", 6: "R1", 7: "L2", 8: "R2",
  9: "UP", 10: "DOWN", 11: "LEFT", 12: "RIGHT", 15: "L3", 16: "R3",
};
const BASE_HINT = "A select · B close";
// Steam button numbers the panel handles (A B X Y, L1 R1 L2 R2, D-pad, L3 R3).
const NAV_CODES = new Set([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16]);
const EVENTS = ["vgp_onbuttondown", "vgp_onbuttonup", "keydown", "keyup"];
const ARROWS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", "Escape", "Backspace"]);
// .close is skipped: B already closes, and A right after opening shouldn't.
const FOCUSABLE = "button:not(.handle):not(.close), summary, input, select, textarea, [data-nav]";

export interface GamepadNav {
  /** Put the focus ring on the first control of the top layer (and scroll it to the top). */
  reset(): void;
  /** Recompute the hint bar (e.g. tabs changed). */
  refreshHints(): void;
  /** If the ring isn't on a reachable control (e.g. content just finished building), put it on the first. */
  ensureFocus(): void;
  destroy(): void;
}

export interface NavOptions {
  /** Panel layer, L1/R1: previous/next tab (dir -1/+1). */
  onTab?(dir: -1 | 1): void;
  /** Panel layer, L2/R2: previous/next sub-tab. */
  onSubTab?(dir: -1 | 1): void;
  /** Panel layer: extra always-on hint, e.g. "L1/R1 module". (Windows use data-extra-hint.) */
  extraHint?(): string;
}

/** Sent to a big window (.iwin) for its own tabs: detail {level: 1 | 2, dir: -1 | 1}. */
export const WINDOW_TAB_EVENT = "invasor:window-tab";
/** Sent to a big window when B (with nothing else taking it) should close it. */
export const WINDOW_CLOSE_EVENT = "invasor:window-close";

const MIN_THUMB = 24; // px
const CONTEXT = 72; // px of "what comes next" kept visible around the selected control

// Paging: when the next control is further than this share of the visible height,
// scroll by PAGE of it instead of jumping, so long text in between can be read.
const FAR = 0.8;
const PAGE = 0.6;

export function attachGamepadNav(
  stage: HTMLElement,
  panel: HTMLElement,
  close: () => void,
  opts: NavOptions = {},
): GamepadNav {
  let current: HTMLElement | null = null;
  let lastIndex = 0; // where the ring was, for when its control gets re-rendered away

  /** The topmost open big window, if any. */
  const topWindow = () => {
    const wins = stage.querySelectorAll<HTMLElement>(".iwin");
    return wins.length ? wins[wins.length - 1] : null;
  };
  /** The layer that owns the pad: a big window or the panel. */
  const layer = () => topWindow() ?? panel;
  /** Where the ring can go: the layer's open dialog (ui.confirm), else the layer. */
  const scope = () => layer().querySelector<HTMLElement>(".modal") ?? layer();
  const scroller = () => layer().querySelector<HTMLElement>(".content");
  const thumb = () => layer().querySelector<HTMLElement>(".scrollbar .thumb");

  // Inside a folded <details> (other than being its own summary)? Chromium still gives
  // those elements layout boxes, so visibility checks alone don't catch it.
  function folded(el: HTMLElement): boolean {
    for (let p = el.parentElement; p && p !== stage; p = p.parentElement) {
      if (p instanceof HTMLDetailsElement && !p.open && !(el.tagName === "SUMMARY" && el.parentElement === p)) {
        return true;
      }
    }
    return false;
  }

  function items(): HTMLElement[] {
    return [...scope().querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (el) =>
        el.getClientRects().length > 0 &&
        !(el as HTMLInputElement).disabled &&
        !folded(el) &&
        // A kit control (.ctl, see controls.ts) is one stop: skip the inputs inside it.
        (el.classList.contains("ctl") || !el.closest(".ctl")),
    );
  }

  function focus(el: HTMLElement | null) {
    current?.classList.remove("nav-focus");
    current = el;
    if (!el) return showHints();
    // Visual only. Never el.focus(): taking browser focus away from Steam makes it drop
    // its own selection and silently re-take the next D-pad press (it leaks through).
    el.classList.add("nav-focus");
    reveal(el);
    updateScrollHints();
    showHints();
  }

  function showHints() {
    const lay = layer();
    const hints = lay.querySelector<HTMLElement>(".hints");
    if (!hints) return;
    // A control's own hint replaces the generic one; "B close" only while B still closes.
    const holder = current?.closest<HTMLElement>("[data-hint]");
    const own = holder?.dataset.hint;
    // A control asked for its own text only (navHints: false): a description, no button help.
    if (holder && holder.dataset.hintPlain !== undefined) {
      hints.textContent = own ?? "";
      return;
    }
    let text = !own ? BASE_HINT : /(^|· )B /.test(own) ? own : `${own} · B close`;
    // Tab hints only when tabs can actually be switched: not under a dialog, nor while a
    // control is in an editing mode that owns the pad (its hint then says "B cancel/done").
    const editing = lay.querySelector(".modal") || /B (cancel|done)/.test(own ?? "");
    const extra = editing ? "" : lay === panel ? opts.extraHint?.() : lay.dataset.extraHint;
    if (extra) text += ` · ${extra}`;
    hints.textContent = text;
  }

  /** Offer the button to the selected control first; true if a module handled it. */
  function offer(code: number, repeat: boolean): boolean {
    if (!current?.isConnected) return false;
    const ev = new CustomEvent("invasor:button", {
      detail: { button: NAMES[code] ?? `BTN_${code}`, code, repeat },
      bubbles: true,
      cancelable: true,
    });
    current.dispatchEvent(ev);
    return ev.defaultPrevented;
  }

  /** Show `el` plus ~3 lines of what comes next, so the end never arrives by surprise. */
  function reveal(el: HTMLElement) {
    const sc = scroller();
    if (sc) keepVisible(sc, el, "y", Math.min(CONTEXT, sc.clientHeight / 4));
  }

  let thumbFade: number | undefined;

  function updateScrollHints() {
    const sc = scroller();
    if (!sc) return;
    sc.classList.toggle("more-above", sc.scrollTop > 2);
    sc.classList.toggle("more-below", sc.scrollTop + sc.clientHeight < sc.scrollHeight - 2);
    const th = thumb();
    const track = th?.parentElement;
    if (!th || !track) return;
    const scrollable = sc.scrollHeight - sc.clientHeight;
    track.hidden = scrollable <= 2;
    if (track.hidden) return;
    const trackH = track.clientHeight;
    const h = Math.max(MIN_THUMB, (trackH * sc.clientHeight) / sc.scrollHeight);
    th.style.height = `${h}px`;
    th.style.transform = `translateY(${((trackH - h) * sc.scrollTop) / scrollable}px)`;
    // Brighter while moving, then back to discreet.
    th.classList.add("active");
    window.clearTimeout(thumbFade);
    thumbFade = window.setTimeout(() => th.classList.remove("active"), 700);
  }

  /** Scroll the content one page up/down; false if already at that end. */
  function page(dir: number): boolean {
    const sc = scroller();
    if (!sc) return false;
    const before = sc.scrollTop;
    sc.scrollTop += dir * sc.clientHeight * PAGE;
    updateScrollHints();
    return sc.scrollTop !== before;
  }

  /** Is there more than FAR of a screen of content between the ring and `el`? */
  function farAway(el: HTMLElement, dir: number): boolean {
    const sc = scroller();
    if (!sc || !current) return false;
    const cur = current.getBoundingClientRect();
    const next = el.getBoundingClientRect();
    const gap = dir > 0 ? next.top - cur.bottom : cur.top - next.bottom;
    return gap > sc.clientHeight * FAR && !visible(sc, el);
  }

  function visible(sc: HTMLElement, el: HTMLElement): boolean {
    const view = sc.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    return r.top >= view.top && r.bottom <= view.bottom;
  }

  function move(delta: number) {
    const list = items();
    if (!list.length) {
      if (delta) page(delta);
      return focus(null);
    }
    const i = current ? list.indexOf(current) : -1;
    // Lost track (its control was re-rendered, or the layer changed): stay at the same spot.
    if (i < 0) {
      lastIndex = Math.min(lastIndex, list.length - 1);
      return focus(list[lastIndex]);
    }
    const next = i + delta;
    // Past the last/first control: keep scrolling through any content left there.
    if (next < 0 || next >= list.length) {
      if (delta) page(delta);
      return;
    }
    // Long non-navigable content in between: read through it page by page first.
    if (delta && farAway(list[next], delta) && page(delta)) return;
    lastIndex = next;
    focus(list[next]);
  }

  /** X: fold/unfold the section under the ring (its title, or the section it's in). */
  function toggleSection() {
    if (!current) return;
    if (current.tagName === "SUMMARY") {
      const details = current.parentElement as HTMLDetailsElement;
      details.open = !details.open;
      return updateScrollHints();
    }
    const details = current.closest("details");
    if (!details || !scope().contains(details)) return;
    details.open = false;
    const summary = details.querySelector<HTMLElement>(":scope > summary");
    if (summary) {
      lastIndex = Math.max(0, items().indexOf(summary));
      focus(summary);
    }
    updateScrollHints();
  }

  function side(dir: -1 | 1) {
    if (!current) return;
    // ←→ only change values (sections fold with X). Sliders / number inputs: step the value.
    const input = current as HTMLInputElement;
    if (current.tagName === "INPUT" && (input.type === "range" || input.type === "number")) {
      dir > 0 ? input.stepUp() : input.stepDown();
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    // Anything else: ←→ mean nothing (moving up/down on them felt random).
  }

  function activate() {
    if (!current || !current.isConnected) return move(0);
    current.click();
  }

  /** L1/R1 (level 1) and L2/R2 (level 2): the top layer's tabs, never the panel's under a window. */
  function tabs(level: 1 | 2, dir: -1 | 1) {
    const win = topWindow();
    if (win) return void win.dispatchEvent(new CustomEvent(WINDOW_TAB_EVENT, { detail: { level, dir } }));
    return level === 1 ? opts.onTab?.(dir) : opts.onSubTab?.(dir);
  }

  const onEvent = (e: Event) => {
    if (panel.hidden) return;
    const isKey = e instanceof KeyboardEvent;
    if (isKey && !ARROWS.has(e.key)) return; // F10 and typing stay untouched
    // Typing into one of our fields (Steam or physical keyboard): let every key through.
    const origin = e.composedPath()[0] as HTMLElement | undefined;
    if (isKey && origin?.matches?.("input, textarea") && stage.contains(origin)) return;
    const d = isKey ? undefined : (e as CustomEvent<{ button?: number; is_repeat?: boolean }>).detail;
    // Only the buttons the panel uses are taken from Steam. Everything else (Steam/Guide,
    // ···, back paddles, touchpads, buttons Valve adds later) goes through untouched, so
    // the system buttons always work and an unknown button can never get stuck here.
    if (!isKey && !NAV_CODES.has(d?.button ?? -1)) return;
    // Panel open: Steam's UI underneath must not see the panel's buttons.
    e.preventDefault();
    e.stopImmediatePropagation();
    if (isKey || e.type !== "vgp_onbuttondown" || d?.button === undefined) return;
    // The selected control (i.e. its module) gets first say; preventDefault() overrides ours.
    if (offer(d.button, !!d.is_repeat)) return;
    switch (d.button) {
      case BTN.UP:
        return move(-1);
      case BTN.DOWN:
        return move(1);
      case BTN.LEFT:
        return side(-1);
      case BTN.RIGHT:
        return side(1);
      case BTN.A:
        return activate();
      case BTN.B: {
        // B closes the top layer: a big window first, the panel last.
        const win = topWindow();
        return win ? void win.dispatchEvent(new CustomEvent(WINDOW_CLOSE_EVENT)) : close();
      }
      case BTN.X:
        return toggleSection();
      case BTN.L1:
      case BTN.R1:
        return tabs(1, d.button === BTN.R1 ? 1 : -1);
      case BTN.L2:
      case BTN.R2:
        return tabs(2, d.button === BTN.R2 ? 1 : -1);
    }
  };
  EVENTS.forEach((t) => window.addEventListener(t, onEvent, true));
  // Controls announce hint changes (e.g. a select's list opening).
  stage.addEventListener("invasor:hints", showHints);
  // Controls/dialogs/windows can ask to put the ring on a specific element.
  const onRefocus = (e: Event) => {
    const el = (e as CustomEvent<HTMLElement>).detail;
    if (el?.isConnected) {
      const i = items().indexOf(el);
      if (i >= 0) lastIndex = i;
      focus(el);
    }
  };
  stage.addEventListener("invasor:refocus", onRefocus);
  // When the selected control is re-rendered away (module toggled, values reset…),
  // put the ring back at the same position right away instead of losing it.
  const observer = new MutationObserver(() => {
    if (!panel.hidden && current && !current.isConnected) move(0);
    updateScrollHints(); // content grew/shrank (tab switch, section folded, re-render)
  });
  // Scroll events don't bubble: listen in the capture phase to catch every layer's.
  stage.addEventListener("scroll", updateScrollHints, { capture: true, passive: true });
  observer.observe(stage, { childList: true, subtree: true });

  return {
    refreshHints: showHints,
    ensureFocus() {
      // Only moves the ring if it's lost: never yanks it from where the user put it.
      if (!current || !items().includes(current)) {
        focus(null);
        lastIndex = 0;
        move(0);
      }
      showHints();
    },
    reset() {
      focus(null);
      lastIndex = 0;
      const sc = scroller();
      if (sc) sc.scrollTop = 0;
      move(0);
      showHints();
    },
    destroy() {
      EVENTS.forEach((t) => window.removeEventListener(t, onEvent, true));
      stage.removeEventListener("invasor:hints", showHints);
      stage.removeEventListener("invasor:refocus", onRefocus);
      stage.removeEventListener("scroll", updateScrollHints, { capture: true });
      observer.disconnect();
    },
  };
}
