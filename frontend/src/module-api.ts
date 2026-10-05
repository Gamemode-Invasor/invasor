// The contract between Invasor and a module's UI (full guide: docs/MODULES.md).
//
// A module's ui.ts does:
//
//   import { defineModule, ui } from "invasor";
//   export default defineModule({ async render(el, ctx) { el.append(await ui.settingsForm(ctx)); } });
//
// It is built on its own (modules/<id>/dist/ui.js) and gets `ui`/`defineModule` from
// the core at run time. Only what's declared here is the contract: anything else in
// the core can change without notice.

import { hasApi, safeCall } from "./steam";

// Form controls (toggle, slider, select, text, section, confirm, choose, settingsForm…) with
// gamepad support built in.
export { ui, type Option, type Control, type ButtonControl, type SettingsForm, type FormStore, type FormOptions } from "./ui/controls";

/** Version of this contract. module.json must declare the same `"api"`. */
export const KIT_API: number = 1;

/**
 * The game a "per game" module should act on, and why: the game whose page is open
 * first, then the tile highlighted in the library, then the running game. Null if none.
 */
export function currentGame(state: GameState): { game: Game; how: "selected" | "highlighted" | "running" } | null {
  if (state.selected) return { game: state.selected, how: "selected" };
  if (state.highlighted) return { game: state.highlighted, how: "highlighted" };
  if (state.running) return { game: state.running, how: "running" };
  return null;
}

export interface Game {
  appid: string;
  name: string | null;
  /** True for non-Steam shortcuts (appid from shortcuts.vdf). */
  shortcut: boolean;
}

/**
 * The three game states, from most to least reliable:
 * - running: the game being played (from its process; doesn't depend on Steam's UI);
 * - selected: the game whose page is open (Play, Manage…), from Steam's route;
 * - highlighted: the tile under the cursor in the library, read from Steam's page.
 *   Best effort: null whenever it can't be told (see README, Steam touchpoints).
 */
export interface GameState {
  running: Game | null;
  selected: Game | null;
  highlighted: Game | null;
}

// ---------- settings (declared in module.json, validated by the backend) ----------

export type SettingValue = boolean | number | string;

interface FieldBase<T extends SettingValue> {
  key: string;
  label: string;
  default: T;
  hint?: string;
  /** Shown only while every key of the same form has this value, or one of a list (view only). */
  when?: Record<string, SettingValue | SettingValue[]>;
  /** Disabled (still visible) while every key of the same form has this value, or one of a list. */
  disabled_when?: Record<string, SettingValue | SettingValue[]>;
}

export type SettingField =
  | (FieldBase<boolean> & { type: "toggle" | "checkbox" })
  | (FieldBase<number> & { type: "slider" | "number"; min: number; max: number; step: number; unit?: string })
  | (FieldBase<SettingValue> & { type: "radio" | "select"; options: { value: SettingValue; label: string }[] })
  | (FieldBase<string> & { type: "text" | "password"; max_length: number; placeholder?: string });

export interface SettingSection {
  section: string;
  open: boolean;
  items: SettingField[];
  when?: Record<string, SettingValue | SettingValue[]>;
}

/** module.json "settings", as normalized by the backend. */
export type SettingsSchema = (SettingField | SettingSection)[];

export interface ModuleSettings {
  /** The schema from module.json (what ui.settingsForm draws). */
  readonly schema: SettingsSchema;
  /** Every setting, always valid (defaults for anything missing or invalid). */
  get(): Promise<Record<string, SettingValue>>;
  /**
   * Change one setting. The backend validates it and returns what it stored (a number
   * may come back clamped/snapped to its step). Rejects for unknown keys or bad values.
   * The module's backend is told through ctx.settings.on_change.
   */
  set<T extends SettingValue>(key: string, value: T): Promise<T>;
}

// ---------- the context every module UI gets ----------

export interface ModuleCtx {
  id: string;
  /** Calls this module's own backend: METHODS[method](**args). */
  call<T = unknown>(method: string, args?: Record<string, unknown>): Promise<T>;
  /** Latest known game state (refreshed while the panel is open). */
  game(): GameState;
  /**
   * Steam's JS API, guarded. safeCall("Apps.SetShortcutName", appid, name) calls
   * SteamClient.<path> here, or in Steam's SharedJSContext through the backend when
   * this window doesn't have it; it rejects (never throws) if it isn't available
   * anywhere, so always have a plan B. hasApi() only looks at this window.
   */
  steam: { safeCall: typeof safeCall; hasApi: typeof hasApi };
  settings: ModuleSettings;
  /** module.json "forms": schemas the module stores itself (draw them with ui.form). */
  forms: Record<string, SettingsSchema>;
  /** Short message at the bottom of the panel (or of the window on top). */
  toast(message: string, kind?: "ok" | "error"): void;
  /**
   * Is there room for a big window here? False in the Quick Access column during a
   * game (only ~348px of it are on screen); true in Steam's full UI, also over a game.
   */
  canOpenWindow(): boolean;
  /**
   * Open a big window ("expanded view", almost full screen) over the panel. Returns
   * null, and tells the user how to get room ("Steam button → Library"), when
   * canOpenWindow() is false. To offer one, prefer ui.windowButton: it shows itself
   * locked, with that explanation, whenever there's no room.
   */
  openWindow(spec: WindowSpec): WindowHandle | null;
  /**
   * Be told when canOpenWindow() changes while the panel is open (e.g. Quick Access
   * settling into place). Returns an unsubscribe function. ui.windowButton uses it.
   */
  onSpaceChange(cb: (canOpen: boolean) => void): () => void;
}

// ---------- pages, tabs and windows ----------

/** A page: built the first time it's shown, then kept (hidden) with its state. */
export interface SubTab {
  label: string;
  render(el: HTMLElement, ctx: ModuleCtx): void | Promise<void>;
  /** Became visible (switched to, panel opened on it). */
  onShow?(ctx: ModuleCtx): void;
  /** Stopped being visible: pause timers, polling… */
  onHide?(): void;
}

/** A tab of a big window (L1/R1): one page (`render`) or sub-tabs (`tabs`, L2/R2). */
export interface WindowTab {
  label: string;
  render?(el: HTMLElement, ctx: ModuleCtx): void | Promise<void>;
  tabs?: SubTab[];
  tabsAlign?: TabsAlign;
  onShow?(ctx: ModuleCtx): void;
  onHide?(): void;
}

export type TabsAlign = "start" | "center" | "end" | "justify";

export interface WindowSpec {
  title: string;
  /** A single page… */
  render?(el: HTMLElement, ctx: ModuleCtx): void | Promise<void>;
  /** …or tabs (L1/R1), each optionally with sub-tabs (L2/R2). */
  tabs?: WindowTab[];
  /** The window closed (B, ✕, handle.close(), or the panel closing). */
  onClose?(): void;
}

export interface WindowHandle {
  close(): void;
  setTitle(title: string): void;
}

export interface ModuleDef {
  /**
   * Each module is a tab in the panel (L1/R1). Provide either `render` (one page) or
   * `tabs` (sub-tabs, L2/R2). Content is built lazily, the first time it's shown,
   * and errors are contained to the module's tab.
   * A module with settings in module.json and no ui.ts gets a tab with just its form.
   */
  render?(el: HTMLElement, ctx: ModuleCtx): void | Promise<void>;
  tabs?: SubTab[];
  /** How the sub-tab row (L2/R2) is laid out. Default "start"; unknown values fall back to it. */
  tabsAlign?: TabsAlign;
  /** The module's tab became visible (switched to, or panel opened on it). */
  onShow?(ctx: ModuleCtx): void;
  /** The module's tab stopped being visible: pause timers, polling… */
  onHide?(): void;
  onGameChange?(game: GameState, ctx: ModuleCtx): void;
  /** Overlay torn down or module disabled: release everything. */
  destroy?(): void;
}

/**
 * Gamepad buttons while the panel is open. The core dispatches this event on the
 * control that has the blue focus ring (it bubbles), before doing its own action.
 * Call preventDefault() to handle the button yourself and skip the core's default.
 *
 * Button roles (keep custom controls consistent with them):
 *   A accept/click · B close/cancel · D-pad move, ←→ change value
 *   L1/R1 module tab · L2/R2 sub-tab · X fold/unfold section
 *   Y the control's special value (e.g. back to default)
 *
 *   el.dataset.nav = "";                     // a navigation stop
 *   el.dataset.hint = "←→ adjust · Y default";  // shown in the hint bar
 *   el.addEventListener("invasor:button", (e) => {
 *     if (e.detail.button === "Y") { reset(); e.preventDefault(); }
 *   });
 */
export interface InvasorButtonDetail {
  /** "A" | "B" | "X" | "Y" | "UP" | "DOWN" | "LEFT" | "RIGHT" | "L1" | "R1" | "L2" | "R2",
   *  or "BTN_<code>" if not mapped yet. L1/R1 and L2/R2 switch tabs unless a control takes them. */
  button: string;
  /** Steam's raw button number. */
  code: number;
  /** True for auto-repeat while held. */
  repeat: boolean;
}

declare global {
  interface HTMLElementEventMap {
    "invasor:button": CustomEvent<InvasorButtonDetail>;
  }
}

/** Identity function that gives a module's definition its types. */
export function defineModule(def: ModuleDef): ModuleDef {
  return def;
}
