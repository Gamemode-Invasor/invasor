// Tabs (L1/R1) with optional sub-tabs (L2/R2): the one implementation behind both the
// panel's module tabs and a big window's tabs, so they behave exactly alike.
//
// - Lazy: a tab/sub-tab is built the first time it's shown, then kept (hidden) with
//   its state.
// - Contained: a render that throws shows "Error in …" in its own pane only.
// - Race-safe: a slow build that finishes after the user moved on (or after the tab
//   list was replaced) doesn't touch the tab they are on now.
// - Hooks: onShow/onHide of the tab and of its active sub-tab fire when they become
//   visible / stop being visible, including the host itself being shown or hidden.

import type { GamepadNav } from "./gamepad-nav";
import { alignOf, renderBar } from "./tabbar";

export interface PageSpec {
  label: string;
  render?(el: HTMLElement): void | Promise<void>;
  onShow?(): void;
  onHide?(): void;
}

export interface TabSpec extends PageSpec {
  id: string;
  /** Sub-tabs (L2/R2); when present, `render` isn't used. */
  tabs?: PageSpec[];
  tabsAlign?: string;
  /** Shown instead of any content (e.g. the module's backend didn't load). */
  error?: string;
  /** Name in error messages (defaults to the label). */
  name?: string;
}

export interface TabHostOpts {
  /** Where the tabs' panes go. */
  content: HTMLElement;
  /** The main tab row's container, or null for a single page with no row. */
  mainTabs: HTMLElement | null;
  /** The sub-tab row (shown only for tabs with sub-tabs) and its container. */
  subBar: HTMLElement;
  subTabs: HTMLElement;
  nav: GamepadNav;
  /** Hooks only fire while this is true (panel open / window up). */
  visible(): boolean;
  /** The tab rows changed (e.g. to update hints). */
  onBars?(): void;
}

export interface TabHost {
  /**
   * Replace every tab; keeps the one with id `keepId` active if it's still there.
   * A tab passed again as the very same spec object keeps its built content and state.
   */
  set(tabs: TabSpec[], keepId?: string | null): Promise<void>;
  step(dir: -1 | 1): void;
  stepSub(dir: -1 | 1): void;
  activeId(): string | null;
  hasSub(): boolean;
  count(): number;
  /** The host became visible (e.g. panel opened): onShow of what's on screen. */
  shown(): void;
  /** The host is being hidden: onHide of what was on screen. */
  hidden(): void;
  /** hidden(), then remove every pane. */
  clear(): void;
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function showError(el: HTMLElement, name: string, e: unknown) {
  console.error(`[invasor] ${name} failed`, e);
  const msg = typeof e === "string" ? e : String((e as Error)?.message ?? e);
  el.innerHTML = `<p class="mod-error">Error in ${esc(name)}: ${esc(msg)}</p>`;
}

function safe(what: string, fn: () => void) {
  try {
    fn();
  } catch (e) {
    console.error(`[invasor] ${what} failed`, e);
  }
}

interface SubState {
  panes: HTMLElement[];
  built: boolean[];
  active: number;
}

interface State {
  spec: TabSpec;
  pane: HTMLElement;
  built: boolean;
  sub: SubState | null;
}

export function createTabHost(o: TabHostOpts): TabHost {
  let states: State[] = [];
  let active = -1;
  let gen = 0; // bumped when the tab list is replaced: stale builds then do nothing
  let live = false; // onShow of the active tab has fired and onHide hasn't yet

  const nameOf = (s: State) => s.spec.name ?? s.spec.label;

  async function build(el: HTMLElement, name: string, fn?: (el: HTMLElement) => void | Promise<void>) {
    try {
      await fn?.(el);
    } catch (e) {
      showError(el, name, e);
    }
  }

  function hideActive() {
    const s = states[active];
    if (!live || !s) return;
    live = false;
    const sub = s.sub;
    if (sub?.built[sub.active]) safe("sub-tab onHide", () => s.spec.tabs![sub.active].onHide?.());
    safe("tab onHide", () => s.spec.onHide?.());
  }

  function showActive() {
    const s = states[active];
    if (live || !s?.built || !o.visible()) return;
    live = true;
    safe("tab onShow", () => s.spec.onShow?.());
    const sub = s.sub;
    if (sub?.built[sub.active]) safe("sub-tab onShow", () => s.spec.tabs![sub.active].onShow?.());
  }

  function renderBars() {
    if (o.mainTabs) renderBar(o.mainTabs, states.map((s) => s.spec.label), active, (i) => void show(i));
    const s = states[active];
    const sub = s?.sub;
    o.subBar.hidden = !sub;
    // Each tab picks how its own sub-tab row is laid out (CSS in overlay.css).
    o.subBar.dataset.align = alignOf(s?.spec.tabsAlign);
    if (s && sub) renderBar(o.subTabs, s.spec.tabs!.map((t) => t.label), sub.active, (j) => void showSub(j));
    o.onBars?.();
  }

  /** Show sub-tab j of s (building it the first time). No hooks. */
  async function openSub(s: State, j: number) {
    const sub = s.sub!;
    sub.panes.forEach((p) => (p.hidden = true));
    sub.active = j;
    sub.panes[j].hidden = false;
    if (!sub.built[j]) {
      sub.built[j] = true;
      const page = s.spec.tabs![j];
      await build(sub.panes[j], `${nameOf(s)} › ${page.label}`, page.render);
    }
  }

  async function show(i: number) {
    if (i < 0 || i >= states.length || i === active) return;
    hideActive();
    if (states[active]) states[active].pane.hidden = true;
    active = i;
    const g = gen;
    const s = states[i];
    s.pane.hidden = false;
    // Right away, before any (possibly slow) build: drop the previous tab's sub-tab row
    // and take the ring off its now hidden controls.
    renderBars();
    o.nav.reset();
    if (!s.built) {
      s.built = true;
      if (s.spec.error) {
        showError(s.pane, nameOf(s), s.spec.error);
      } else if (s.spec.tabs?.length) {
        s.sub = { panes: [], built: [], active: 0 };
        for (let k = 0; k < s.spec.tabs.length; k++) {
          const p = document.createElement("div");
          p.className = "subpane";
          p.hidden = true;
          s.pane.appendChild(p);
          s.sub.panes.push(p);
          s.sub.built.push(false);
        }
      } else {
        await build(s.pane, nameOf(s), s.spec.render);
      }
    }
    if (g !== gen || active !== i) return; // moved on while it was building
    if (s.sub) {
      const opening = openSub(s, s.sub.active);
      renderBars(); // the sub-tab row shows up now, even if its content is still building
      await opening;
      if (g !== gen || active !== i) return;
    }
    renderBars();
    showActive();
    o.nav.ensureFocus();
  }

  async function showSub(j: number) {
    const s = states[active];
    const sub = s?.sub;
    if (!s || !sub || j < 0 || j >= sub.panes.length || j === sub.active) return;
    if (live && sub.built[sub.active]) safe("sub-tab onHide", () => s.spec.tabs![sub.active].onHide?.());
    const g = gen;
    const opening = openSub(s, j); // marks j active and shows its pane synchronously
    renderBars();
    o.nav.reset();
    await opening;
    if (g !== gen || states[active] !== s || sub.active !== j) return;
    if (live) safe("sub-tab onShow", () => s.spec.tabs![j].onShow?.());
    o.nav.ensureFocus();
  }

  function clear() {
    hideActive();
    gen++;
    for (const s of states) s.pane.remove();
    states = [];
    active = -1;
  }

  return {
    async set(tabs, keepId) {
      // The same spec object again = the same tab: it survives with its content.
      const kept = new Map(states.filter((s) => tabs.includes(s.spec)).map((s) => [s.spec, s]));
      // The tab on screen stays on screen, untouched (e.g. ⚙ Settings after a module is
      // switched off there): no hide/show, so the ring and the scroll stay where they were.
      const current = states[active];
      const stays = !!current && current.built && kept.has(current.spec) && current.spec.id === keepId;
      if (!stays) hideActive();
      gen++;
      for (const s of states) if (!kept.has(s.spec)) s.pane.remove();
      active = -1;
      states = tabs.map((spec) => {
        const old = kept.get(spec);
        if (old) {
          if (old !== current || !stays) old.pane.hidden = true;
          o.content.appendChild(old.pane); // moves it into tab order (a kept node keeps its scroll)
          return old;
        }
        const pane = document.createElement("div");
        pane.className = "pane";
        pane.hidden = true;
        o.content.appendChild(pane); // DOM order = tab order
        return { spec, pane, built: false, sub: null };
      });
      if (!states.length) return renderBars();
      if (stays) {
        active = states.indexOf(current);
        renderBars();
        o.nav.ensureFocus(); // only if the focused control is gone
        showActive(); // hidden() ran before this (a rebuild): the tab is back on screen, so is its onShow
        return;
      }
      const keep = states.findIndex((s) => s.spec.id === keepId);
      await show(keep >= 0 ? keep : 0);
    },
    step: (dir) => void show(active + dir),
    stepSub(dir) {
      const sub = states[active]?.sub;
      if (sub) void showSub(sub.active + dir);
    },
    activeId: () => states[active]?.spec.id ?? null,
    hasSub: () => !!states[active]?.sub,
    count: () => states.length,
    shown: showActive,
    hidden: hideActive,
    clear,
  };
}
