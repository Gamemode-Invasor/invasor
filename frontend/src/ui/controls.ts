// Form controls for modules, built for gamepad + touch inside Steam.
//
// Every control is one navigation stop (a `.ctl[data-nav]` row). It reacts to the
// core's "invasor:button" events (see gamepad-nav.ts) and to clicks/touch, and
// declares its own hint. Native <select> is avoided on purpose: its popup is a
// separate native widget gamescope may not show and the pad can't drive.
//
// Value controls return a Control<T>: the element plus get()/set()/setDisabled().
// set() never calls onChange (only the user's own changes do).

import type { ModuleCtx, SettingField, SettingValue, SettingsSchema, WindowSpec } from "../module-api";
import { createKeyboard, type MiniKeyboard } from "./keyboard";
import { keepVisible } from "./scroll";
import { isVideo, optionIndex, snap, whenMet } from "./values";

export interface Option<T> {
  value: T;
  label: string;
  /** A CSS colour: ui.radio shows a dot of it instead of the label (the label stays as its name). */
  swatch?: string;
}

interface Base<T> {
  label: string;
  value: T;
  onChange?(value: T): void;
  /** Extra hint for the hint bar, appended to the control's own. */
  hint?: string;
  /**
   * false: the hint bar shows only `hint` (a description of the option), without the
   * button help ("←→ adjust · Y default · B close"). Editing modes (an open list, the
   * keyboard) still show theirs. Default true.
   */
  navHints?: boolean;
  /** Start disabled (still selectable, so the hint can say why). */
  disabled?: boolean;
}

/** The idle hint of a control: its button help plus `hint`, or just `hint` (navHints false). */
function hintFor(o: { hint?: string; navHints?: boolean }, own: string) {
  return o.navHints === false ? (o.hint ?? "") : withHint(own, o.hint);
}

/** Mark a control whose hint bar shows only its own text (see gamepad-nav showHints). */
function plain(el: HTMLElement, o: { navHints?: boolean }) {
  if (o.navHints === false) el.dataset.hintPlain = "";
}

/** A value control: the element itself, plus a small API to drive it from code. */
export type Control<T> = HTMLElement & {
  get(): T;
  /** Show a new value (clamped/snapped like user input). Doesn't call onChange. */
  set(value: T): void;
  /** Disabled controls stay selectable but ignore input; `reason` goes in the hint bar. */
  setDisabled(disabled: boolean, reason?: string): void;
};

export type ButtonControl = HTMLElement & {
  setLabel(label: string): void;
  setDisabled(disabled: boolean, reason?: string): void;
};

/** What ui.settingsForm returns: the form, its controls by key, and helpers. */
export type SettingsForm = HTMLElement & {
  controls: Record<string, Control<SettingValue>>;
  /** Read the stored values again and show them. */
  reload(): Promise<void>;
  /** Store every field's default (from module.json) and show it. */
  reset(): Promise<void>;
};

// Tile height/width ratios of Steam's artwork kinds, and a sensible column count.
const GRID_ASPECTS = { grid: 1.5, hero: 31 / 96, logo: 0.5, icon: 1 };
const GRID_COLUMNS = { grid: 5, hero: 2, logo: 3, icon: 6 };
const NO_ROOM = "The expanded view is only available from the Library (Steam button → Library)";
// Buttons a disabled control swallows: the ones that change values or act.
const VALUE_BUTTONS = new Set(["A", "Y", "LEFT", "RIGHT"]);

// Where ui.confirm()/choose() draw their dialog: the overlay tells us its top layer (the last
// open big window, else the panel). Asked at the moment of use, so stacked windows
// closing in any order can never leave it pointing at the wrong place.
let topLayer: (() => HTMLElement | null) | null = null;
export function setTopLayer(fn: (() => HTMLElement | null) | null) {
  topLayer = fn;
}

/** Tell the nav bar this control's hint changed (e.g. a list opened). */
export function hintsChanged(el: HTMLElement) {
  el.dispatchEvent(new CustomEvent("invasor:hints", { bubbles: true }));
}

const isDisabled = (el: HTMLElement) => el.dataset.disabled !== undefined;

/**
 * Make `el` disable-able: while disabled, clicks and value buttons are swallowed
 * before any of the control's own listeners (registered first, capture for clicks).
 */
function disableable(el: HTMLElement, baseHint: () => string, extra?: (disabled: boolean) => void) {
  el.addEventListener(
    "click",
    (e) => {
      if (isDisabled(el)) {
        e.stopImmediatePropagation();
        e.preventDefault();
      }
    },
    true,
  );
  el.addEventListener("invasor:button", (e) => {
    if (isDisabled(el) && VALUE_BUTTONS.has(e.detail.button)) {
      e.stopImmediatePropagation();
      e.preventDefault();
    }
  });
  return (disabled: boolean, reason?: string) => {
    if (disabled) el.dataset.disabled = "";
    else delete el.dataset.disabled;
    el.classList.toggle("disabled", disabled);
    el.dataset.hint = disabled ? reason || "Disabled" : baseHint();
    extra?.(disabled);
    hintsChanged(el);
  };
}

function withHint(own: string, extra?: string) {
  return extra ? `${own} · ${extra}` : own;
}

export function row(label: string, hint: string, extra?: string): { el: HTMLElement; body: HTMLElement } {
  const el = document.createElement("div");
  el.className = "ctl";
  el.dataset.nav = "";
  el.dataset.hint = withHint(hint, extra);
  const name = document.createElement("span");
  name.className = "ctl-label";
  name.textContent = label;
  const body = document.createElement("span");
  body.className = "ctl-body";
  el.append(name, body);
  return { el, body };
}

/** Handle gamepad buttons on a control: return true from fn to consume the button. */
export function onButton(el: HTMLElement, fn: (button: string) => boolean | void) {
  el.addEventListener("invasor:button", (e) => {
    if (fn(e.detail.button)) e.preventDefault();
  });
}

function asControl<T>(el: HTMLElement, get: () => T, set: (v: T) => void, setDisabled: Control<T>["setDisabled"]): Control<T> {
  return Object.assign(el, { get, set, setDisabled });
}

function checkOptions<T>(kind: string, o: Base<T> & { options: Option<T>[] }): number {
  if (!Array.isArray(o.options) || o.options.length === 0) throw new Error(`ui.${kind} "${o.label}": options is empty`);
  const i = optionIndex(o.options, o.value);
  if (i < 0) console.warn(`[invasor] ui.${kind} "${o.label}": value ${JSON.stringify(o.value)} isn't an option, showing the first`);
  return Math.max(0, i);
}

function boolControl(kind: "toggle" | "checkbox", o: Base<boolean>): Control<boolean> {
  const { el, body } = row(o.label, "A toggle", o.hint);
  el.dataset.hint = hintFor(o, "A toggle");
  plain(el, o);
  const setDisabled = disableable(el, () => hintFor(o, "A toggle"));
  el.classList.add(kind);
  let value = !!o.value;
  const mark = document.createElement("span");
  mark.className = kind === "toggle" ? "switch" : "box";
  body.appendChild(mark);
  const paint = () => el.classList.toggle("on", value);
  paint();
  el.addEventListener("click", () => {
    value = !value;
    paint();
    o.onChange?.(value);
  });
  const c = asControl(el, () => value, (v) => ((value = !!v), paint()), setDisabled);
  if (o.disabled) c.setDisabled(true);
  return c;
}

type NumberOpts = Base<number> & { min: number; max: number; step?: number; default?: number; unit?: string };

function numberControl(kind: "slider" | "number", o: NumberOpts): Control<number> {
  if (!(o.min < o.max)) throw new Error(`ui.${kind} "${o.label}": min must be lower than max`);
  const step = o.step && o.step > 0 ? o.step : 1;
  const hasDefault = o.default !== undefined;
  const verb = kind === "slider" ? "adjust" : "change";
  const hint = () => hintFor(o, hasDefault ? `←→ ${verb} · Y default` : `←→ ${verb}`);
  const { el, body } = row(o.label, "", undefined);
  plain(el, o);
  el.dataset.hint = hint();
  el.classList.add(kind);
  const shown = document.createElement("span");
  shown.className = "ctl-value";
  let value = snap(o.value, o.min, o.max, step);
  let range: HTMLInputElement | null = null;
  const setDisabled = disableable(el, hint, (d) => range && (range.disabled = d));

  if (kind === "slider") {
    range = document.createElement("input");
    range.type = "range";
    Object.assign(range, { min: String(o.min), max: String(o.max), step: String(step) });
    body.append(range, shown);
    range.addEventListener("input", () => change(Number(range!.value)));
  } else {
    const dec = document.createElement("span");
    const inc = document.createElement("span");
    dec.className = inc.className = "ctl-arrow";
    dec.textContent = "‹";
    inc.textContent = "›";
    body.append(dec, shown, inc);
    dec.addEventListener("click", (e) => (e.stopPropagation(), change(value - step)));
    inc.addEventListener("click", (e) => (e.stopPropagation(), change(value + step)));
  }

  const paint = () => {
    if (range) range.value = String(value);
    shown.textContent = `${value}${o.unit ?? ""}`;
  };
  const change = (v: number) => {
    const next = snap(v, o.min, o.max, step);
    const changed = next !== value;
    value = next;
    paint();
    if (changed) o.onChange?.(value);
  };
  paint();
  onButton(el, (b) => {
    if (b === "LEFT" || b === "RIGHT") return change(value + (b === "RIGHT" ? step : -step)), true;
    if (b === "Y" && hasDefault) return change(o.default!), true;
  });
  const c = asControl(el, () => value, (v) => ((value = snap(v, o.min, o.max, step)), paint()), setDisabled);
  if (o.disabled) c.setDisabled(true);
  return c;
}

/** Where a form's values live: ctx.settings, or whatever the module provides. */
export interface FormStore {
  get(): Promise<Record<string, SettingValue>>;
  set<T extends SettingValue>(key: string, value: T): Promise<T>;
}

/** Options of settingsForm / form: only some keys; navHints false = hint bar shows each field's hint alone. */
export interface FormOptions {
  keys?: string[];
  navHints?: boolean;
}

/** Draws a schema (module.json "settings" or a "forms" entry) bound to a store. */
async function buildForm(schema: SettingsSchema, store: FormStore, toast: ModuleCtx["toast"], o: FormOptions): Promise<SettingsForm> {
  const form = document.createElement("div");
  form.className = "settings-form";
  const controls: Record<string, Control<SettingValue>> = {};
  const fields: SettingField[] = [];
  const values = await store.get();
  // Fields/sections with `when` (shown or hidden) and fields with `disabled_when`, updated
  // as the values they depend on change.
  type Condition = Record<string, SettingValue | SettingValue[]>;
  const conditional: { el: HTMLElement; when: Condition }[] = [];
  const lockable: { c: Control<SettingValue>; when: Condition }[] = [];
  const labels: Record<string, string> = {};
  for (const item of schema) for (const f of "section" in item ? item.items : [item]) labels[f.key] = f.label;
  const valueOf = (key: string) => (controls[key] ? controls[key].get() : values[key]);
  const applyWhen = () => {
    for (const c of conditional) c.el.style.display = whenMet(c.when, valueOf) ? "" : "none";
    for (const l of lockable) {
      const locked = whenMet(l.when, valueOf);
      l.c.setDisabled(locked, locked ? `Set by ${Object.keys(l.when).map((k) => labels[k] ?? k).join(" + ")}` : undefined);
    }
  };

  const make = (f: SettingField): HTMLElement => {
    fields.push(f);
    const value = values[f.key] ?? f.default;
    // One save in flight per key, and only the latest value is sent next: a held
    // slider fires many changes, and saves racing each other could store an old one.
    let saving = false;
    let pending: { v: SettingValue } | null = null;
    const onChange = async (v: SettingValue) => {
      values[f.key] = v;
      applyWhen();
      pending = { v };
      if (saving) return;
      saving = true;
      const c = controls[f.key];
      let stored: SettingValue | undefined;
      let failed = false;
      while (pending) {
        const next: SettingValue = pending.v;
        pending = null;
        try {
          stored = await store.set(f.key, next);
        } catch (e) {
          failed = true;
          toast(`Couldn't save ${f.label}: ${(e as Error).message}`, "error");
        }
      }
      saving = false;
      try {
        // Show what's really stored (clamped, snapped, or the old value after an error).
        if (failed) stored = (await store.get())[f.key] ?? f.default;
        if (stored !== undefined && stored !== c.get()) c.set(stored);
        if (stored !== undefined) values[f.key] = stored;
        applyWhen();
      } catch {
        /* backend gone: the status line says so */
      }
    };
    let c: Control<any>;
    switch (f.type) {
      case "toggle":
      case "checkbox":
        c = ui[f.type]({ label: f.label, value: value as boolean, hint: f.hint, navHints: o.navHints, onChange });
        break;
      case "slider":
      case "number":
        c = ui[f.type]({
          label: f.label,
          value: value as number,
          min: f.min,
          max: f.max,
          step: f.step,
          unit: f.unit,
          default: f.default,
          hint: f.hint,
          navHints: o.navHints,
          onChange,
        });
        break;
      case "radio":
      case "select":
        c = ui[f.type]<SettingValue>({ label: f.label, value, options: f.options, hint: f.hint, navHints: o.navHints, onChange });
        break;
      case "text":
      case "password":
        c = ui[f.type]({ label: f.label, value: value as string, placeholder: f.placeholder, maxLength: f.max_length, hint: f.hint, navHints: o.navHints, onChange });
        break;
      default:
        throw new Error(`unknown setting type ${(f as { type: string }).type}`);
    }
    controls[f.key] = c;
    if (f.when) conditional.push({ el: c, when: f.when });
    if (f.disabled_when) lockable.push({ c, when: f.disabled_when });
    return c;
  };

  const wanted = (f: SettingField) => !o.keys || o.keys.includes(f.key);
  for (const item of schema) {
    if (!("section" in item)) {
      if (wanted(item)) form.appendChild(make(item));
      continue;
    }
    const items = item.items.filter(wanted);
    if (!items.length) continue;
    const section = ui.section(item.section, items.map(make), { open: item.open });
    if (item.when) conditional.push({ el: section, when: item.when });
    form.appendChild(section);
  }
  applyWhen();
  if (!fields.length) form.appendChild(ui.info("Nothing to set here."));

  const reload = async () => {
    const now = await store.get();
    Object.assign(values, now);
    for (const f of fields) controls[f.key].set(now[f.key] ?? f.default);
    applyWhen();
  };
  const reset = async () => {
    for (const f of fields) controls[f.key].set(await store.set(f.key, f.default));
    applyWhen();
  };
  return Object.assign(form, { controls, reload, reset });
}

/** Shared by ui.text and ui.password. */
function textField(o: Base<string> & { placeholder?: string; maxLength?: number }, secret: boolean): Control<string> {
  const idleHint = () => hintFor(o, "A type");
  const { el, body } = row(o.label, idleHint());
  plain(el, o);
  el.classList.add("text");
  const input = document.createElement("input");
  input.type = secret ? "password" : "text";
  if (secret) {
    el.classList.add("password");
    input.autocomplete = "new-password";
  }
  if (o.maxLength && o.maxLength > 0) input.maxLength = o.maxLength;
  input.value = (o.value ?? "").slice(0, o.maxLength || undefined);
  input.placeholder = o.placeholder ?? "";
  body.appendChild(input);
  const showHide = {
    label: () => (input.type === "password" ? "show" : "hide"),
    run: () => void (input.type = input.type === "password" ? "text" : "password"),
  };
  let kbd: MiniKeyboard | null = null;
  let steamFocus: HTMLElement | null = null;
  let committed = input.value;
  const setDisabled = disableable(el, idleHint, (d) => {
    input.disabled = d;
    if (d) finish();
  });

  const setHint = (h: string) => {
    el.dataset.hint = withHint(h, o.hint);
    hintsChanged(el);
  };
  const commit = () => {
    if (input.value === committed) return;
    committed = input.value;
    o.onChange?.(input.value);
  };

  // Closing the panel or the window with the keyboard open keeps what was typed.
  let dismissHost: Element | null = null;
  function finish() {
    if (!kbd) return;
    dismissHost?.removeEventListener("invasor:dismiss", finish);
    dismissHost = null;
    kbd.el.remove();
    kbd = null;
    if (secret) input.type = "password";
    el.classList.remove("editing");
    el.dataset.hint = idleHint();
    hintsChanged(el);
    commit();
  }

  const open = () => {
    if (kbd) return;
    el.classList.add("editing");
    kbd = createKeyboard(input, () => {}, finish, secret ? showHide : undefined);
    el.appendChild(kbd.el);
    dismissHost = el.closest(".iwin-box, .panel");
    dismissHost?.addEventListener("invasor:dismiss", finish);
    setHint("A type · X delete · Y shift · B done");
  };

  // Physical keyboard path: typing needs browser focus, which Steam's own selection
  // loses meanwhile; hand it back on blur or the next D-pad press leaks (gamepad-nav.ts).
  // STEAM TOUCHPOINT: ".gpfocus" is Steam's class for its selected element. If it
  // changes, nothing breaks: focus just isn't handed back after physical typing.
  input.addEventListener("pointerdown", () => {
    steamFocus = el.ownerDocument.querySelector<HTMLElement>(".gpfocus");
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") input.blur();
  });
  input.addEventListener("change", commit);
  input.addEventListener("blur", () => {
    steamFocus?.focus({ preventScroll: true });
    steamFocus = null;
  });
  el.addEventListener("click", (e) => {
    if (e.target !== input) open();
  });
  onButton(el, (b) => {
    if (kbd) return kbd.handle(b);
    return false; // A -> core click -> open()
  });
  const set = (v: string) => {
    input.value = (v ?? "").slice(0, o.maxLength || undefined);
    committed = input.value;
  };
  const c = asControl(el, () => input.value, set, setDisabled);
  if (o.disabled) c.setDisabled(true);
  return c;
}

export const ui = {
  /** On/off switch. A (or tap) flips it. */
  toggle: (o: Base<boolean>) => boolControl("toggle", o),

  /** Checkbox. A (or tap) flips it. */
  checkbox: (o: Base<boolean>) => boolControl("checkbox", o),

  /** Numeric slider. ←→ step, Y resets to `default` (if given). Touch can drag. */
  slider: (o: NumberOpts) => numberControl("slider", o),

  /** Stepper ‹ n ›. ←→ change it, Y resets to `default` (if given). */
  number: (o: NumberOpts) => numberControl("number", o),

  /** Segmented choice for a few options. ←→ (or tap) pick. */
  radio<T>(o: Base<T> & { options: Option<T>[] }): Control<T> {
    let index = checkOptions("radio", o);
    const hint = () => hintFor(o, "←→ choose");
    const { el, body } = row(o.label, hint());
    plain(el, o);
    const setDisabled = disableable(el, hint);
    el.classList.add("radio");
    const segs = o.options.map((opt, i) => {
      const s = document.createElement("span");
      s.className = "seg";
      if (opt.swatch) {
        s.classList.add("swatch");
        s.title = opt.label;
        s.setAttribute("aria-label", opt.label);
        const dot = document.createElement("span");
        dot.className = "dot";
        dot.style.background = opt.swatch;
        s.appendChild(dot);
      } else {
        s.textContent = opt.label;
      }
      s.addEventListener("click", (e) => (e.stopPropagation(), pick(i)));
      body.appendChild(s);
      return s;
    });
    const paint = () => segs.forEach((s, i) => s.classList.toggle("on", i === index));
    const pick = (i: number) => {
      const next = Math.min(o.options.length - 1, Math.max(0, i));
      if (next === index) return;
      index = next;
      paint();
      o.onChange?.(o.options[index].value);
    };
    paint();
    onButton(el, (b) => {
      if (b === "LEFT" || b === "RIGHT") return pick(index + (b === "RIGHT" ? 1 : -1)), true;
    });
    const set = (v: T) => {
      const i = optionIndex(o.options, v);
      if (i >= 0) (index = i), paint();
    };
    const c = asControl(el, () => o.options[index].value, set, setDisabled);
    if (o.disabled) c.setDisabled(true);
    return c;
  },

  /**
   * Dropdown. ←→ cycle without opening; A opens the list, ↑↓ move in it, A picks,
   * B cancels (closes the list only, not the panel).
   */
  select<T>(o: Base<T> & { options: Option<T>[] }): Control<T> {
    let index = checkOptions("select", o);
    const closedHint = () => hintFor(o, "←→ change · A list");
    const { el, body } = row(o.label, closedHint());
    plain(el, o);
    const setDisabled = disableable(el, closedHint, (d) => d && close());
    el.classList.add("select");
    let hi = -1; // highlighted item while the list is open, -1 = closed
    const shown = document.createElement("span");
    shown.className = "ctl-value";
    body.appendChild(shown);
    const list = document.createElement("div");
    list.className = "select-list";
    list.hidden = true;
    const items = o.options.map((opt, i) => {
      const it = document.createElement("div");
      it.className = "select-item";
      it.textContent = opt.label;
      it.addEventListener("click", (e) => (e.stopPropagation(), pick(i), close()));
      list.appendChild(it);
      return it;
    });
    el.appendChild(list);

    const paint = () => {
      shown.textContent = `‹ ${o.options[index].label} ›`;
      items.forEach((it, i) => {
        it.classList.toggle("on", i === hi);
        it.classList.toggle("chosen", i === index);
      });
      if (items[hi]) keepVisible(list, items[hi]);
    };
    const pick = (i: number) => {
      const next = (i + o.options.length) % o.options.length;
      if (next === index) return;
      index = next;
      paint();
      o.onChange?.(o.options[index].value);
    };
    // Closing the panel or the window closes the list too, so it isn't left open.
    const onDismiss = () => close();
    let dismissHost: Element | null = null;
    const open = () => {
      hi = index;
      list.hidden = false;
      dismissHost = el.closest(".iwin-box, .panel");
      dismissHost?.addEventListener("invasor:dismiss", onDismiss);
      el.dataset.hint = "↑↓ choose · A select · B cancel";
      paint();
      hintsChanged(el);
    };
    function close() {
      if (hi < 0) return;
      hi = -1;
      list.hidden = true;
      dismissHost?.removeEventListener("invasor:dismiss", onDismiss);
      dismissHost = null;
      el.dataset.hint = closedHint();
      paint();
      hintsChanged(el);
    }
    paint();
    el.addEventListener("click", () => (hi < 0 ? open() : close()));
    onButton(el, (b) => {
      if (hi < 0) {
        if (b === "LEFT" || b === "RIGHT") return pick(index + (b === "RIGHT" ? 1 : -1)), true;
        return false; // A falls through to the core's click -> open()
      }
      // List open: this control owns the pad until it closes.
      if (b === "UP" || b === "DOWN") {
        hi = Math.min(o.options.length - 1, Math.max(0, hi + (b === "DOWN" ? 1 : -1)));
        paint();
      } else if (b === "A") {
        pick(hi);
        close();
      } else if (b === "B") {
        close();
      }
      return true;
    });
    const set = (v: T) => {
      const i = optionIndex(o.options, v);
      if (i >= 0) (index = i), paint();
    };
    const c = asControl(el, () => o.options[index].value, set, setDisabled);
    if (o.disabled) c.setDisabled(true);
    return c;
  },

  /**
   * Text field. A opens the built-in on-screen keyboard (see keyboard.ts): it lives
   * in the panel, so it works the same whatever Steam changes. Tapping/clicking the
   * field itself allows typing with a physical keyboard.
   */
  text(o: Base<string> & { placeholder?: string; maxLength?: number }): Control<string> {
    return textField(o, false);
  },

  /**
   * Password field: a text field whose characters show as dots. The keyboard has an
   * extra "show"/"hide" key to check what was typed. Masking is visual only: the value
   * is stored and handed to the module's backend as is.
   */
  password(o: Base<string> & { placeholder?: string; maxLength?: number }): Control<string> {
    return textField(o, true);
  },

  /** Action button. A (or tap) runs it. */
  button(o: { label: string; onClick(): void; hint?: string; disabled?: boolean }): ButtonControl {
    const el = document.createElement("button");
    el.className = "ctl-button";
    el.dataset.nav = "";
    const hint = () => o.hint ?? "A press";
    el.dataset.hint = hint();
    el.textContent = o.label;
    const setDisabled = disableable(el, hint);
    el.addEventListener("click", () => o.onClick());
    const c = Object.assign(el, { setLabel: (l: string) => void (el.textContent = l), setDisabled });
    if (o.disabled) c.setDisabled(true);
    return c;
  },

  /**
   * Foldable group of controls. X folds/unfolds it (from its title or from any control
   * inside), A or a tap on the title too; the gamepad skips controls inside a folded section.
   */
  section(title: string, children: HTMLElement[], o: { open?: boolean } = {}) {
    const details = document.createElement("details");
    details.className = "section";
    details.open = o.open ?? true;
    const summary = document.createElement("summary");
    summary.textContent = title;
    summary.dataset.hint = "X fold/unfold";
    const body = document.createElement("div");
    body.className = "section-body";
    body.append(...children);
    details.append(summary, body);
    return details;
  },

  /**
   * Modal yes/no dialog over the panel (or the window on top). While open, the gamepad
   * only moves between its buttons, B means cancel, and tab switching is blocked.
   * Resolves true on OK. Closing the panel/window counts as cancel.
   */
  async confirm(message: string, o: { ok?: string; cancel?: string } = {}): Promise<boolean> {
    return (await ui.choose(message, [{ label: o.ok ?? "OK", value: true }], { cancel: o.cancel })) === true;
  },

  /**
   * Like confirm, with several answers: resolves the chosen one's `value`, or null on
   * Cancel / B / closing. Two buttons sit side by side; more are stacked. Starts on Cancel.
   *
   *   const how = await ui.choose("Uninstall?", [{ label: "Uninstall", value: "keep" },
   *                                              { label: "Uninstall and delete its data", value: "purge" }]);
   */
  choose<T>(message: string, choices: { label: string; value: T }[], o: { cancel?: string } = {}): Promise<T | null> {
    if (!Array.isArray(choices) || choices.length === 0) throw new Error(`ui.choose "${message}": choices is empty`);
    const host = topLayer?.() ?? null;
    if (!host) return Promise.resolve(null);
    // One dialog at a time: a second one would leave the pad driving the first.
    if (host.querySelector(":scope > .modal")) {
      console.warn("[invasor] ui.confirm/choose: a dialog is already open, answering cancel");
      return Promise.resolve(null);
    }
    const previous = host.querySelector<HTMLElement>(".nav-focus");
    return new Promise((resolve) => {
      const modal = document.createElement("div");
      modal.className = "modal";
      const box = document.createElement("div");
      box.className = "modal-box";
      const text = document.createElement("p");
      text.textContent = message;
      const done = (answer: T | null) => {
        if (!modal.isConnected) return;
        host.removeEventListener("invasor:dismiss", dismiss);
        modal.remove();
        // Give the ring back to whatever opened the dialog.
        if (previous?.isConnected) host.dispatchEvent(new CustomEvent("invasor:refocus", { detail: previous, bubbles: true }));
        resolve(answer);
      };
      const hint = "A choose · B cancel";
      const order = [
        ui.button({ label: o.cancel ?? "Cancel", onClick: () => done(null), hint }),
        ...choices.map((c) => ui.button({ label: c.label, onClick: () => done(c.value), hint })),
      ];
      const buttons = document.createElement("div");
      buttons.className = order.length > 2 ? "modal-buttons stacked" : "modal-buttons";
      buttons.append(...order);
      box.append(text, buttons);
      modal.appendChild(box);
      const dismiss = () => done(null);
      host.addEventListener("invasor:dismiss", dismiss);
      // bubbles: the navigation listens higher up (on the stage holding panel and windows).
      const refocus = (el: HTMLElement) => host.dispatchEvent(new CustomEvent("invasor:refocus", { detail: el, bubbles: true }));
      const step = (by: number) => {
        const at = order.findIndex((b) => b.classList.contains("nav-focus"));
        refocus(order[Math.min(order.length - 1, Math.max(0, at + by))]);
      };
      onButton(modal, (b) => {
        if (b === "B") return done(null), true;
        if (b === "LEFT") return step(-1), true;
        if (b === "RIGHT") return step(1), true;
        if (["L1", "R1", "L2", "R2"].includes(b)) return true; // no tab switching under a dialog
      });
      // Cover exactly what's visible of the host, even if it's scrolled.
      modal.style.top = `${host.scrollTop}px`;
      modal.style.height = `${host.clientHeight}px`;
      host.appendChild(modal);
      // Start on Cancel: the safe choice for destructive actions.
      refocus(order[0]);
    });
  },

  /**
   * The module's settings form, drawn from the schema in its module.json and bound to
   * ctx.settings: every change is validated and stored by the backend (a value it
   * normalizes, e.g. snapped to the step, shows up corrected; a rejected one is undone
   * with a toast). Sections become foldable ui.section groups.
   *
   *   el.append(await ui.settingsForm(ctx));
   *   el.append(await ui.settingsForm(ctx, { keys: ["name"] })); // only some fields
   */
  async settingsForm(ctx: ModuleCtx, o: FormOptions = {}): Promise<SettingsForm> {
    return buildForm(ctx.settings.schema, ctx.settings, ctx.toast, o);
  },

  /**
   * A form declared in module.json "forms", whose values the module stores itself
   * (e.g. in another program's config file) through `store`: get() returns every
   * value, set(key, value) saves one and returns what was stored. Same controls and
   * behaviour as settingsForm (one save in flight per key, corrected values shown).
   *
   *   el.append(await ui.form(ctx, "profile", {
   *     get: () => ctx.call("profile_get", { name }),
   *     set: (key, value) => ctx.call("profile_set", { name, key, value }),
   *   }));
   */
  async form(ctx: ModuleCtx, name: string, store: FormStore, o: FormOptions = {}): Promise<SettingsForm> {
    const schema = ctx.forms[name];
    if (!schema) throw new Error(`module.json has no form "${name}"`);
    return buildForm(schema, store, ctx.toast, o);
  },

  /**
   * Grid of image previews (e.g. game artwork). One navigation stop with its own
   * highlighted tile: ←→ along a row, ↑↓ between rows (leaving the grid past the first
   * or last row), A activates (onActivate), Y is the special value (onSelect: mark it).
   * Images load lazily; a grey box shows while loading and ⚠ if one fails.
   */
  imageGrid(o: {
    /** `src` may be an image or a short video (.webm/.mp4, e.g. an animated thumbnail; or set `video`). */
    items: { id: string; src: string; label?: string; video?: boolean }[];
    /** Tile shape: Steam artwork kinds (grid, hero, logo, icon), or a custom height/width ratio. */
    aspect?: "grid" | "hero" | "logo" | "icon" | number;
    columns?: number;
    selected?: string;
    onSelect?(id: string): void;
    onActivate?(id: string): void;
    /** What A does, for the hint bar ("A open" by default). */
    activateLabel?: string;
    hint?: string;
  }) {
    const ratio = typeof o.aspect === "number" ? o.aspect : GRID_ASPECTS[o.aspect ?? "grid"];
    const columns = Math.max(1, Math.floor(o.columns ?? GRID_COLUMNS[typeof o.aspect === "string" ? o.aspect : "grid"]));
    const el = document.createElement("div");
    el.className = "ctl image-grid";
    el.dataset.nav = "";
    el.dataset.hint = ["↔↕ choose", o.onActivate ? `A ${o.activateLabel ?? "open"}` : "", o.onSelect ? "Y mark" : "", o.hint ?? ""].filter(Boolean).join(" · ");
    const grid = document.createElement("div");
    grid.className = "grid";
    grid.style.gridTemplateColumns = `repeat(${columns}, 1fr)`;
    el.appendChild(grid);
    let hi = Math.max(0, o.items.findIndex((x) => x.id === o.selected));
    let selected = o.selected;

    const tiles = o.items.map((item, i) => {
      const tile = document.createElement("div");
      tile.className = "tile";
      const frame = document.createElement("div");
      frame.className = "frame";
      frame.style.paddingTop = `${ratio * 100}%`; // fixed shape before the image loads
      // Animated art comes with a video thumbnail: shown paused on its first frame, and
      // played only while its tile is highlighted (25 videos at once would load a handheld).
      let media: HTMLImageElement | HTMLVideoElement;
      if (item.video ?? isVideo(item.src)) {
        const video = document.createElement("video");
        video.muted = true;
        video.loop = true;
        video.playsInline = true;
        video.preload = "auto";
        // Some Steam windows don't decode video at all (seen in the desktop client): after
        // a while without a frame, show a "▶" placeholder instead of an endless grey box.
        window.setTimeout(() => {
          if (video.readyState < 2 && !frame.classList.contains("broken")) frame.classList.add("novideo");
        }, 8000);
        video.addEventListener("loadeddata", () => frame.classList.remove("novideo"));
        media = video;
      } else {
        const img = document.createElement("img");
        img.loading = "lazy";
        img.decoding = "async";
        img.alt = item.label ?? "";
        media = img;
      }
      media.addEventListener("error", () => frame.classList.add("broken"));
      media.src = item.src;
      frame.appendChild(media);
      tile.appendChild(frame);
      if (item.label) {
        const cap = document.createElement("span");
        cap.className = "cap";
        cap.textContent = item.label;
        tile.appendChild(cap);
      }
      tile.addEventListener("click", (e) => {
        e.stopPropagation();
        hi = i;
        paint();
        o.onActivate?.(item.id);
      });
      grid.appendChild(tile);
      return tile;
    });

    /** Play the highlighted tile's video only while the grid has the ring. */
    const playback = () => {
      const active = el.classList.contains("nav-focus") && el.isConnected;
      tiles.forEach((t, i) => {
        const v = t.querySelector("video");
        if (!v) return;
        if (active && i === hi) void v.play().catch(() => {});
        else if (!v.paused) v.pause();
      });
    };
    // The ring is a class (gamepad-nav.ts): follow it to start/stop the video.
    new MutationObserver(playback).observe(el, { attributes: true, attributeFilter: ["class"] });

    const paint = () => {
      tiles.forEach((t, i) => {
        t.classList.toggle("hi", i === hi);
        t.classList.toggle("selected", o.items[i].id === selected);
      });
      playback();
      const scroller = el.closest<HTMLElement>(".content");
      if (scroller && tiles[hi]) keepVisible(scroller, tiles[hi], "y", 24);
    };
    paint();

    onButton(el, (b) => {
      if (!tiles.length) return false;
      const row = Math.floor(hi / columns);
      const lastRow = Math.floor((tiles.length - 1) / columns);
      if (b === "LEFT" || b === "RIGHT") {
        const next = hi + (b === "RIGHT" ? 1 : -1);
        if (next >= 0 && next < tiles.length && Math.floor(next / columns) === row) hi = next;
        return paint(), true;
      }
      if (b === "UP") {
        if (row === 0) return false; // leave the grid upwards
        hi -= columns;
        return paint(), true;
      }
      if (b === "DOWN") {
        if (row === lastRow) return false; // leave the grid downwards
        hi = Math.min(tiles.length - 1, hi + columns);
        return paint(), true;
      }
      if (b === "A" && o.onActivate) return o.onActivate(o.items[hi].id), true;
      if (b === "Y" && o.onSelect) {
        selected = o.items[hi].id;
        paint();
        return o.onSelect(selected), true;
      }
    });
    return el;
  },

  /**
   * Button that opens an "expanded view" (ctx.openWindow). Where there's no room for
   * one (the Quick Access column during a game) it shows itself locked, explains that
   * it's available from the Library, and stays selectable so the hint bar can say why.
   * It locks/unlocks by itself if the room changes while the panel is open.
   */
  windowButton(ctx: ModuleCtx, o: { label?: string; open: () => WindowSpec; hint?: string }) {
    const label = o.label ?? "Open expanded view";
    const wrap = document.createElement("div");
    wrap.className = "window-button";
    const btn = document.createElement("button");
    btn.className = "ctl-button";
    btn.dataset.nav = "";
    // Not `disabled`: navigation skips disabled elements, and this one must stay reachable.
    const note = document.createElement("p");
    note.className = "locked-note";
    note.textContent = "Available from the Library (Steam button → Library)";
    wrap.append(btn, note);
    let locked = false;

    const paint = (canOpen: boolean) => {
      locked = !canOpen;
      btn.classList.toggle("locked", locked);
      btn.textContent = locked ? `🔒 ${label}` : label;
      note.hidden = !locked;
      btn.dataset.hint = locked ? "Library only" : (o.hint ?? "A open");
      hintsChanged(btn);
    };
    btn.addEventListener("click", () => {
      if (locked) return ctx.toast(NO_ROOM, "error");
      ctx.openWindow(o.open());
    });
    // A window's render makes a new button on every open: one that left the page stops
    // listening (the module's own unsubscribe only runs when the whole module is destroyed).
    let attached = false;
    const stop = ctx.onSpaceChange((canOpen) => {
      if (wrap.isConnected) attached = true;
      else if (attached) return stop();
      paint(canOpen);
    });
    paint(ctx.canOpenWindow());
    return wrap;
  },

  /**
   * An image preview (not selectable): a grey box while it loads, ⚠ if it can't.
   * Scaled down to fit the panel width and `maxHeight` (px, default 180).
   */
  image(src: string, o: { alt?: string; maxHeight?: number } = {}) {
    const box = document.createElement("div");
    box.className = "ctl-image";
    const img = document.createElement("img");
    img.alt = o.alt ?? "";
    img.decoding = "async";
    img.style.maxHeight = `${o.maxHeight ?? 180}px`;
    img.addEventListener("error", () => box.classList.add("broken"));
    img.addEventListener("load", () => box.classList.add("loaded"));
    img.src = src;
    box.appendChild(img);
    return box;
  },

  /** Plain text line (not selectable). */
  info(text: string) {
    const p = document.createElement("p");
    p.className = "ctl-info";
    p.textContent = text;
    return p;
  },

  separator() {
    const hr = document.createElement("hr");
    hr.className = "ctl-sep";
    return hr;
  },
};
