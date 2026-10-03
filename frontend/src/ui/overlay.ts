import type { Api } from "../api";
import type { KitRegistry } from "../kit";
import type { GameState, ModuleCtx, ModuleDef, WindowHandle } from "../module-api";
import { hasApi, safeCall, steamAvailable } from "../steam";
import { setTopLayer, ui } from "./controls";
import { attachGamepadNav } from "./gamepad-nav";
import css from "./overlay.css";
import { accentColor } from "./palette";
import { renderSettings, type ModuleInfo } from "./settings";
import { tabRowHTML } from "./tabbar";
import { createTabHost, type TabSpec } from "./tabhost";
import { openBigWindow } from "./window";

// The overlay lives in its own shadow root: Steam's CSS can't restyle it and ours
// can't leak into Steam, so class-name churn on Valve's side doesn't affect us.
//
// Layout: one tab per enabled module (L1/R1) plus the built-in Settings tab,
// optional sub-tabs inside a module (L2/R2), all handled by the tab host (tabhost.ts).

export interface Overlay {
  toggle(): void;
  setOpen(open: boolean): void;
  isOpen(): boolean;
  /** False in Quick Access while no game runs: no "I" there, and nothing opens. */
  isAvailable(): boolean;
  destroy(): void;
}

/**
 * A module shown as a tab. Kept across tab rebuilds (e.g. another module toggled in
 * ⚙ Settings) as long as what it shows doesn't change, so it keeps its state; destroy()
 * only runs when it goes away or changes.
 */
interface LiveModule {
  id: string;
  /** What decides its tab: when this changes, the module is rebuilt. */
  sig: string;
  def: ModuleDef | null;
  ctx: ModuleCtx | null;
  built: boolean;
  spec: TabSpec;
}

type Side = "auto" | "left" | "right";

const GAME_POLL_MS = 3000;
const TOAST_MS = 2500;
const FIT_MS = 500; // how often an open panel re-checks where its window sits
// Visible width of Steam's Quick Access column, measured on a Legion Go (1500px wide
// logical screen). Only used until the real one has been measured once.
const QAM_FALLBACK_W = 348;
// Share of the real screen the panel takes: 40% in the Library's main window, a third
// elsewhere (in Quick Access the visible column is narrower anyway).
const PANEL_SHARE: Record<string, number> = { main: 0.4 };
const PANEL_SHARE_DEFAULT = 1 / 3;
const SETTINGS_ID = "__settings";
// A big window needs at least this share of the screen on screen (not the 348px
// Quick Access column during a game).
const WINDOW_MIN_SHARE = 0.6;
const NO_ROOM_MESSAGE = "The expanded view is only available from the Library (Steam button → Library)";

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

export function createOverlay(api: Api, version: string, role: string, kit: KitRegistry): Overlay {
  const host = document.createElement("div");
  host.id = "invasor-root";
  // Own top-level stacking context above anything Steam renders; the host itself
  // is click-through, only our handle/panel take input.
  host.style.cssText = "position:fixed;inset:0;z-index:2147483647;pointer-events:none;";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>${css}</style>
    <button class="handle" aria-label="Open Invasor">I</button>
    <div class="stage">
    <div class="panel" hidden>
      <header>
        <span>Invasor <small>${esc(version)}</small></span>
        <button class="close" aria-label="Close">✕</button>
      </header>
      <div class="game"></div>
      ${tabRowHTML("main", "L1", "R1")}
      ${tabRowHTML("sub", "L2", "R2", true)}
      <div class="content-wrap">
        <section class="content"></section>
        <div class="scrollbar"><div class="thumb"></div></div>
      </div>
      <div class="toast" hidden></div>
      <div class="hints"></div>
      <footer class="status">Connecting…</footer>
    </div>
    </div>`;
  document.body.appendChild(host);

  const $ = <T extends Element = HTMLElement>(sel: string) => root.querySelector<T>(sel)!;
  const stage = $<HTMLDivElement>(".stage"); // panel + big windows: what the pad navigates
  const panel = $<HTMLDivElement>(".panel");
  const status = $(".status");
  const gameLine = $(".game");
  const toastEl = $(".toast");
  $(".close").addEventListener("click", () => toggle(false));
  // Touch/mouse entry point, so the overlay is reachable without a keyboard.
  const handle = $<HTMLButtonElement>(".handle");
  handle.addEventListener("click", () => toggle());
  // ui.confirm draws over the top layer: the last open big window, else the panel.
  setTopLayer(() => {
    const boxes = stage.querySelectorAll<HTMLElement>(".iwin .iwin-box");
    return boxes.length ? boxes[boxes.length - 1] : panel;
  });

  // Quick Access offers the panel only while a game runs (the library always does).
  const qam = role === "quickaccess";
  let available = !qam;
  function setAvailable(yes: boolean) {
    available = yes;
    handle.style.display = yes ? "" : "none";
  }
  setAvailable(available);

  let game: GameState = { selected: null, running: null, highlighted: null };
  let gameKey = "";
  let pollTimer: number | undefined;
  let toastTimer: number | undefined;
  let modules = new Map<string, LiveModule>();
  let tabsLoaded = false;

  const windows = new Set<WindowHandle>(); // open big windows, closed with the panel
  const spaceListeners = new Map<string, Set<(canOpen: boolean) => void>>(); // per module id
  let lastSpace: boolean | null = null;

  const nav = attachGamepadNav(stage, panel, () => toggle(false), {
    onTab: (dir) => tabs.step(dir),
    onSubTab: (dir) => tabs.stepSub(dir),
    extraHint: () => (tabs.hasSub() ? "L1/R1 module · L2/R2 section" : tabs.count() > 1 ? "L1/R1 module" : ""),
  });

  const tabs = createTabHost({
    content: $<HTMLElement>(".content"),
    mainTabs: $<HTMLElement>(".tabbar.main .tabs"),
    subBar: $<HTMLElement>(".tabbar.sub"),
    subTabs: $<HTMLElement>(".tabbar.sub .tabs"),
    nav,
    visible: () => !panel.hidden,
  });

  // A module UI registering after the tabs were built (it was injected late): pick it
  // up on the next open, or now if the panel is open.
  kit.onRegister(() => {
    if (!tabsLoaded) return;
    tabsLoaded = false;
    if (!panel.hidden) {
      tabsLoaded = true;
      void rebuildTabs();
    }
  });

  // ---------- side ----------
  function applySide(side: Side) {
    // Auto: in Quick Access, Steam's menu sits on the right, so we live on the left.
    const left = side === "left" || (side === "auto" && role === "quickaccess");
    host.className = left ? "side-left" : "side-right";
  }
  applySide("auto");

  /** Accent colour of everything (panel, windows, kit controls) and the "I" handle. */
  function applyAccent(name: string) {
    const c = accentColor(name);
    host.style.setProperty("--accent", c.hex);
    host.style.setProperty("--accent-rgb", c.rgb);
    host.style.setProperty("--accent-fg", c.fg);
  }
  // The handle shows before the panel is ever opened: give it its colour right away.
  api
    .call<{ accent_color?: string }>("core", "prefs")
    .then((p) => applyAccent(p.accent_color ?? "blue"))
    .catch(() => {});

  // ---------- toast ----------
  function toast(message: string, kind: "ok" | "error" = "ok") {
    // Shown on the top layer: a big window has its own toast area (the panel's is under it).
    const wins = stage.querySelectorAll<HTMLElement>(".iwin .toast");
    const el = wins.length ? wins[wins.length - 1] : toastEl;
    el.textContent = message;
    el.className = `toast ${kind}`;
    el.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => (el.hidden = true), TOAST_MS);
  }

  // ---------- module plumbing ----------
  function makeCtx(m: ModuleInfo): ModuleCtx {
    const id = m.id;
    const ctx: ModuleCtx = {
      id,
      call: (method, args = {}) => api.call(id, method, args),
      game: () => game,
      steam: {
        // SteamClient isn't in every window: when this one lacks the function, the
        // backend calls it in Steam's SharedJSContext (core.steam_call).
        safeCall: <T,>(path: string, ...args: unknown[]): Promise<T> =>
          hasApi(path) ? safeCall<T>(path, ...args) : api.call<T>("core", "steam_call", { path, args }),
        hasApi,
      },
      settings: {
        schema: m.settings,
        get: () => api.call("core", "settings_get", { id }),
        set: (key, value) => api.call("core", "settings_set", { id, key, value }),
      },
      forms: m.forms ?? {},
      toast,
      canOpenWindow,
      openWindow(spec) {
        if (!canOpenWindow()) {
          toast(NO_ROOM_MESSAGE, "error");
          return null;
        }
        const handle = openBigWindow({ ...spec, onClose: () => (windows.delete(handle), spec.onClose?.()) }, { stage, nav, ctx });
        windows.add(handle);
        return handle;
      },
      onSpaceChange(cb) {
        const set = spaceListeners.get(id) ?? new Set();
        spaceListeners.set(id, set);
        set.add(cb);
        return () => void set.delete(cb);
      },
    };
    return ctx;
  }

  /** Tell subscribers (ui.windowButton…) when there stops/starts being room for a window. */
  function checkSpace() {
    const can = canOpenWindow();
    if (can === lastSpace) return;
    lastSpace = can;
    for (const set of spaceListeners.values()) for (const cb of set) safe("onSpaceChange", () => cb(can));
  }

  /** Room for a big window: enough of this window must be on screen (see fitToScreen). */
  function canOpenWindow(): boolean {
    const visibleW = parseFloat(host.style.getPropertyValue("--visible-w")) || window.innerWidth;
    return visibleW >= screen.width * WINDOW_MIN_SHARE && window.innerHeight >= screen.height * WINDOW_MIN_SHARE;
  }

  function safe(what: string, fn: () => void) {
    try {
      fn();
    } catch (e) {
      console.error(`[invasor] ${what} failed`, e);
    }
  }

  /** What a module's tab shows: its definition, or why it can't be shown. Null: no tab. */
  function resolve(m: ModuleInfo): { def: ModuleDef } | { error: string } | null {
    if (!m.enabled || m.error) return null; // a broken module.json is reported in ⚙ Settings
    if (!m.loaded) return { error: "the module's backend didn't load (see log)" };
    if (m.ui) {
      const r = kit.get(m.id);
      if (!r) return { error: m.ui_built ? "its UI didn't load (see log)" : "its UI isn't built (run npm run build)" };
      return r;
    }
    // Settings in module.json and no ui.ts: the form is the whole tab.
    if (m.settings.length) return { def: { render: async (el, ctx) => void el.append(await ui.settingsForm(ctx)) } };
    return null; // backend-only module
  }

  /** The module's tab: the previous one if nothing about it changed, else a new one. */
  function moduleTab(m: ModuleInfo, old: LiveModule | undefined): LiveModule | null {
    const r = resolve(m);
    if (!r) return null;
    const def = "def" in r ? r.def : null;
    // A settings-only module gets a fresh def object each time: compare its schema instead.
    const sig = JSON.stringify([m.tab, m.name, m.settings, "error" in r ? r.error : null, m.ui]);
    if (old && old.sig === sig && (old.def === def || !m.ui)) return old;
    const base = { id: m.id, label: m.tab || m.name, name: m.name };
    if (!def) return { id: m.id, sig, def: null, ctx: null, built: false, spec: { ...base, error: (r as { error: string }).error } };
    const ctx = makeCtx(m);
    const live: LiveModule = { id: m.id, sig, def, ctx, built: false, spec: base as TabSpec };
    const built = <T>(fn: () => T) => ((live.built = true), fn());
    live.spec = {
      ...base,
      tabsAlign: def.tabsAlign,
      render: def.render ? (el) => built(() => def.render!(el, ctx)) : undefined,
      tabs: def.tabs?.map((s) => ({
        label: s.label,
        render: (el: HTMLElement) => built(() => s.render(el, ctx)),
        onShow: () => s.onShow?.(ctx),
        onHide: () => s.onHide?.(),
      })),
      onShow: () => def.onShow?.(ctx),
      onHide: () => def.onHide?.(),
    };
    return live;
  }

  // Created once: passing the same spec again keeps the tab's content (see tabhost.ts).
  const settingsSpec: TabSpec = {
    id: SETTINGS_ID,
    label: "⚙ Settings",
    name: "Settings",
    render: (el) => renderSettings(el, { api, version, toast, onModulesChanged: rebuildTabs, onPanelSide: applySide, onAccentColor: applyAccent }),
  };

  function destroyModule(m: LiveModule) {
    spaceListeners.delete(m.id);
    if (m.def) safe("module destroy", () => m.def!.destroy?.());
  }

  /**
   * (Re)create the tabs from the backend's module list. Unchanged tabs keep their state.
   * False if the list couldn't be fetched: what's there stays (⚙ Settings at least).
   */
  async function rebuildTabs(): Promise<boolean> {
    let list: ModuleInfo[] = [];
    try {
      list = await api.call<ModuleInfo[]>("core", "modules");
    } catch {
      toast("Couldn't load the module list", "error");
      if (!tabs.count()) await tabs.set([settingsSpec]);
      return false;
    }
    const keep = tabs.activeId();
    list.sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
    const before = modules;
    modules = new Map();
    for (const m of list) {
      if (qam && m.no_qam) continue; // the module asked not to be shown in Quick Access
      const live = moduleTab(m, before.get(m.id));
      if (live) modules.set(m.id, live);
    }
    tabs.hidden(); // onHide before any module is destroyed
    for (const [id, m] of before) if (modules.get(id) !== m) destroyModule(m);
    await tabs.set([...[...modules.values()].map((m) => m.spec), settingsSpec], keep);
    return true;
  }

  // ---------- game / status ----------
  function describe(g: GameState): string {
    const label = (x: { appid: string; name: string | null }) => esc(x.name ?? `App ${x.appid}`);
    const parts: string[] = [];
    const shown = new Set<string>(); // the same game isn't listed twice
    for (const [kind, x] of [["Playing", g.running], ["Selected", g.selected], ["Highlighted", g.highlighted]] as const) {
      if (!x || shown.has(x.appid)) continue;
      shown.add(x.appid);
      parts.push(`<span class="k">${kind}</span> ${label(x)}`);
    }
    return parts.join("<br>") || `<span class="k">No game</span>`;
  }

  async function refreshGame() {
    try {
      const next = await api.call<GameState>("core", "game");
      if (qam) setAvailable(next.running !== null);
      const key = JSON.stringify(next);
      if (key === gameKey) return;
      game = next;
      gameKey = key;
      gameLine.innerHTML = describe(game);
      for (const m of modules.values()) {
        if (m.built && m.def) safe("onGameChange", () => m.def!.onGameChange?.(game, m.ctx!));
      }
    } catch {
      // Backend hiccup: keep the last known state, status line shows the problem.
    }
  }

  async function refreshStatus() {
    try {
      const [info, prefs] = await Promise.all([
        api.call<{ version: string }>("core", "info"),
        api.call<{ panel_side: Side; accent_color: string; qam_visible_w: number | null }>("core", "prefs"),
      ]);
      applySide(prefs.panel_side);
      applyAccent(prefs.accent_color);
      if (prefs.qam_visible_w && qamWidth === null) {
        qamWidth = prefs.qam_visible_w;
        fitToScreen();
      }
      const steam = steamAvailable() ? "SteamClient OK" : "SteamClient unavailable";
      status.textContent = `Backend ${info.version} · ${steam}`;
      status.className = "status ok";
    } catch (e) {
      status.textContent = `Backend unavailable: ${(e as Error).message}`;
      status.className = "status err";
    }
  }

  // ---------- fitting the window ----------
  let fitTimer: number | undefined;
  let fittedFor = "";
  let qamWidth: number | null = null; // learned visible width of the Quick Access column

  /**
   * Size the panel to the on-screen part of this window. Quick Access is a window
   * wider than what's visible (logged: 855px wide at x=1152 on a 1500px screen, so
   * only 348px show), and while Steam slides it in it briefly reports x=0, so this
   * is re-checked while the panel is open. If the position is unknown (0/negative)
   * the whole window counts. Plain reads of screenX/innerWidth: nothing to break.
   */
  // STEAM TOUCHPOINT: window.screenX/innerWidth of Steam's windows (fallback: the whole window).
  function fitToScreen() {
    let onScreen = screen.width - Math.max(0, window.screenX);
    if (role === "quickaccess") {
      if (window.screenX > 0) {
        // Steam told us where the window is: measure, and remember it for later.
        if (onScreen !== qamWidth) {
          qamWidth = onScreen;
          api.call("core", "set_qam_width", { width: onScreen }).catch(() => {});
        }
      } else {
        // Without a game, Quick Access reports x=0 although it sits at the right edge
        // (logged: 855px window, only its first 348px visible). Never trust the whole
        // window then: use the last measured column width, or the Legion Go measurement.
        onScreen = qamWidth ?? QAM_FALLBACK_W;
      }
    }
    const visible = Math.max(200, Math.min(window.innerWidth, onScreen));
    const key = `${screen.width}/${visible}`;
    // Every round, not only when the position changed: anything that changes the room
    // available must reach the subscribers (ui.windowButton locks/unlocks itself).
    if (key === fittedFor) return checkSpace();
    const refit = fittedFor !== "" && !panel.hidden;
    fittedFor = key;
    if (refit) {
      // Diagnostics: the window moved under an open panel (e.g. Quick Access sliding in).
      api.call("core", "report", { role, event: "refit", pos: `${window.screenX},${window.screenY}`, visible }).catch(() => {});
    }
    host.style.setProperty("--screen-w", `${screen.width}px`); // a share of the real screen…
    host.style.setProperty("--panel-share", String(PANEL_SHARE[role] ?? PANEL_SHARE_DEFAULT));
    host.style.setProperty("--visible-w", `${visible}px`); // …but never past its visible part
    checkSpace();
  }

  // A panel whose window stops being visible (··· closed, back to the game, another
  // window takes over) closes itself: never an invisible open panel holding the pad.
  const closeIfGone = () => {
    if (!panel.hidden && (document.hidden || !document.hasFocus())) toggle(false);
  };
  window.addEventListener("blur", closeIfGone);
  document.addEventListener("visibilitychange", closeIfGone);

  // Quick Access: show the "I" only while a game runs. Checked when the window shows up
  // and every few seconds while it's visible (an open panel polls the game anyway).
  const checkGame = () => {
    if (qam && panel.hidden && !document.hidden) void refreshGame();
  };
  const availTimer = qam ? window.setInterval(checkGame, GAME_POLL_MS) : undefined;
  if (qam) {
    window.addEventListener("focus", checkGame);
    document.addEventListener("visibilitychange", checkGame);
    checkGame();
  }

  // ---------- open / close ----------
  function toggle(force?: boolean) {
    const open = force === undefined ? panel.hidden : force;
    if (open === !panel.hidden) return;
    if (open && !available) return;
    panel.hidden = !open;
    window.clearInterval(pollTimer);
    window.clearInterval(fitTimer);
    if (!open) {
      for (const w of [...windows]) w.close(); // big windows go with the panel
      panel.dispatchEvent(new CustomEvent("invasor:dismiss")); // e.g. cancel an open ui.confirm
      tabs.hidden();
      return;
    }
    fitToScreen();
    fitTimer = window.setInterval(fitToScreen, FIT_MS);
    // setTimeout, not requestAnimationFrame: rAF is paused in hidden/background windows.
    window.setTimeout(() =>
      api
        .call("core", "report", {
          role,
          event: "open",
          size: `${window.innerWidth}x${window.innerHeight}`,
          outer: `${window.outerWidth}x${window.outerHeight}`,
          pos: `${window.screenX},${window.screenY}`,
          screen: `${screen.width}x${screen.height}`,
          visible: fittedFor.split("/")[1],
          panel: Math.round(panel.getBoundingClientRect().width),
        })
        .catch(() => {}),
    100);
    void refreshStatus();
    nav.reset();
    // Game first, so modules render with the current game already known.
    void refreshGame().then(async () => {
      if (!tabsLoaded) {
        tabsLoaded = true;
        // Backend not reachable yet: try again on the next open.
        if (!(await rebuildTabs())) tabsLoaded = false;
      } else {
        tabs.shown();
        nav.reset();
      }
    });
    pollTimer = window.setInterval(refreshGame, GAME_POLL_MS);
  }

  return {
    toggle: () => toggle(),
    setOpen: (open) => toggle(open),
    isOpen: () => !panel.hidden,
    isAvailable: () => available,
    destroy() {
      for (const w of [...windows]) w.close();
      window.clearInterval(availTimer);
      window.removeEventListener("focus", checkGame);
      document.removeEventListener("visibilitychange", checkGame);
      window.removeEventListener("blur", closeIfGone);
      document.removeEventListener("visibilitychange", closeIfGone);
      window.clearInterval(pollTimer);
      window.clearInterval(fitTimer);
      window.clearTimeout(toastTimer);
      tabs.clear();
      nav.destroy();
      for (const m of modules.values()) destroyModule(m);
      modules.clear();
      setTopLayer(null);
      host.remove();
    },
  };
}
