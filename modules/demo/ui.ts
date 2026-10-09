import {
  currentGame,
  defineModule,
  ui,
  type GameState,
  type ModuleCtx,
  type SettingsForm,
  type SettingValue,
  type WindowSpec,
} from "invasor";
import { CLIP } from "./clip";

// Showcase of everything the core offers a module UI (docs/MODULES.md, sections 3, 5
// and 6): the settings form from module.json with its rules (when, disabled_when,
// hints, folded and conditional sections), a "forms" entry stored by the module,
// sub-tabs (L2/R2) with onShow/onHide, the control API (set, setDisabled, setLabel),
// the confirm and choose dialogs, toasts, calls to the backend (notifications, Steam, clean
// errors), Steam's JS API from here, a custom control, images, videos and big windows.
// Demo keeps no_qam off: it must show in Quick Access too (the "no room" case). Its showInQam
// hook lets you hide it there from a setting, to try the hook.

let gameEl: HTMLElement | null = null;
let summaryEl: HTMLElement | null = null;
let spaceEl: HTMLElement | null = null;
let statsEl: HTMLElement | null = null;
let statsTimer: number | undefined;
let unsubscribeSpace: (() => void) | null = null;
const forms: SettingsForm[] = []; // every settings form on screen, to reset them all
let controlsForm: SettingsForm | null = null;

function showGame(game: GameState) {
  const name = (g: GameState["running"]) => (g ? (g.name ?? g.appid) : "—");
  if (gameEl) {
    const acting = currentGame(game);
    gameEl.textContent =
      `Running: ${name(game.running)} · Selected: ${name(game.selected)} · Highlighted: ${name(game.highlighted)}` +
      ` · Acting on: ${acting ? `${name(acting.game)} (${acting.how})` : "—"}`;
  }
}

async function showSummary(ctx: ModuleCtx) {
  if (summaryEl) summaryEl.textContent = `Values: ${JSON.stringify({ ...(await ctx.settings.get()), secret: undefined })}`;
}

function showSpace(canOpen: boolean) {
  if (spaceEl) spaceEl.textContent = `Room for a big window here: ${canOpen ? "yes" : "no (Quick Access during a game)"}`;
}

async function showStats(ctx: ModuleCtx) {
  try {
    const s = await ctx.call<{
      uptime: number; visits: number; upgraded_from: string | null; steam_starts: number; last_steam_start: string | null;
    }>("stats");
    const upgraded = s.upgraded_from ? ` · Upgraded from ${s.upgraded_from}` : "";
    const starts = ` · Steam starts seen: ${s.steam_starts}${s.last_steam_start ? ` (last at ${s.last_steam_start})` : ""}`;
    if (statsEl) statsEl.textContent = `Backend thread: up ${s.uptime} s · Backend tab visits: ${s.visits}${starts}${upgraded}`;
  } catch (e) {
    if (statsEl) statsEl.textContent = `Backend unavailable: ${(e as Error).message}`;
  }
}

/** A local placeholder "cover" (SVG data URL): no network in demos or tests. */
function cover(i: number): string {
  const hue = (i * 47) % 360;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="hsl(${hue},45%,35%)"/><text x="100" y="170" font-size="64" text-anchor="middle" fill="white" font-family="sans-serif">${i + 1}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** A button that calls the backend, disabled (with a "working" label) until it answers. */
function callButton(ctx: ModuleCtx, label: string, run: () => Promise<unknown>) {
  const b = ui.button({
    label,
    onClick: async () => {
      b.setDisabled(true, "Waiting for the backend…");
      b.setLabel(`${label}…`);
      try {
        ctx.toast(String(await run()));
      } catch (e) {
        ctx.toast((e as Error).message, "error"); // InvalidArgument / Unavailable arrive as their message
      } finally {
        b.setLabel(label);
        b.setDisabled(false);
      }
    },
  });
  return b;
}

/**
 * A control of the module's own (section 5, "Button roles"): one navigation stop
 * (data-nav) with its hint (data-hint), driven by invasor:button. ←→ change, Y resets.
 * Drawn with the user's accent colour (--accent, --accent-rgb, --accent-fg).
 */
function counter(label: string, onChange: (n: number) => void) {
  let n = 0;
  const el = document.createElement("div");
  el.className = "ctl";
  el.dataset.nav = "";
  el.dataset.hint = "←→ count · Y reset";
  const name = document.createElement("span");
  name.className = "ctl-label";
  name.textContent = label;
  const pill = document.createElement("span");
  pill.className = "ctl-value";
  pill.style.cssText =
    "min-width:3em;text-align:center;padding:2px 10px;border-radius:999px;font-weight:600;" +
    "background:var(--accent);color:var(--accent-fg);box-shadow:0 0 0 4px rgba(var(--accent-rgb),0.15)";
  el.append(name, pill);
  const paint = () => (pill.textContent = String(n));
  paint();
  el.addEventListener("invasor:button", (e) => {
    const b = e.detail.button;
    if (b !== "LEFT" && b !== "RIGHT" && b !== "Y") return;
    n = b === "Y" ? 0 : n + (b === "RIGHT" ? 1 : -1);
    paint();
    onChange(n);
    e.preventDefault(); // handled here: the core does nothing else with it
  });
  el.addEventListener("click", () => (n++, paint(), onChange(n)));
  return el;
}

function demoWindowSpec(ctx: ModuleCtx): WindowSpec {
  return {
    title: "Example expanded view",
    tabs: [
      {
        label: "Gallery",
        render(el) {
          const last = ui.info("Press A on an image to open it, Y to mark it. The last tile is a video: it plays while highlighted.");
          el.append(
            last,
            ui.imageGrid({
              items: [
                ...Array.from({ length: 24 }, (_, i) => ({ id: `img${i + 1}`, src: cover(i), label: `Cover ${i + 1}` })),
                { id: "clip", src: CLIP, label: "Video", video: true },
              ],
              aspect: "grid",
              columns: 6,
              activateLabel: "open",
              onActivate: (id) => (last.textContent = `Opened: ${id}`),
              onSelect: (id) => ctx.toast(`Marked: ${id}`),
            }),
            ui.button({
              label: "Button after the gallery",
              onClick: async () => ctx.toast((await ui.confirm("Confirm inside the window?")) ? "Confirmed" : "Cancelled"),
            }),
          );
        },
      },
      {
        label: "Form",
        tabsAlign: "center",
        tabs: [
          {
            label: "Basic",
            render: (el) =>
              void el.append(
                ui.toggle({ label: "Option", value: true }),
                ui.slider({ label: "Level", min: 0, max: 10, default: 5, value: 5 }),
              ),
          },
          {
            label: "More",
            render: (el) =>
              void el.append(
                ui.select({ label: "Choice", value: "a", options: [{ value: "a", label: "A" }, { value: "b", label: "B" }] }),
                ui.text({ label: "Text", value: "" }),
                ui.password({ label: "Password", value: "" }),
              ),
          },
        ],
      },
      {
        label: "List",
        render: (el) => void el.append(...Array.from({ length: 40 }, (_, i) => ui.checkbox({ label: `Item ${i + 1}`, value: false }))),
      },
    ],
  };
}

/** A window opened with ctx.openWindow directly, to use its handle (setTitle, close). */
function handleWindowSpec(ctx: ModuleCtx, handle: () => ReturnType<ModuleCtx["openWindow"]>): WindowSpec {
  let shown = 0;
  return {
    title: "Window handle",
    render(el) {
      el.append(
        ui.info("This window was opened with ctx.openWindow: its handle can retitle and close it."),
        ui.button({ label: "Change the title", onClick: () => handle()?.setTitle(`Window handle · retitled ${++shown}×`) }),
        ui.button({ label: "Close from code", onClick: () => handle()?.close() }),
      );
    },
    onClose: () => ctx.toast("Window closed (onClose)"),
  };
}

export default defineModule({
  // Sub-tab row layout: "start" (default), "center", "end" or "justify".
  tabsAlign: "center",
  // Quick Access only: false hides this tab there. Runs when the panel opens and when the game changes.
  showInQam: async (ctx) => !(await ctx.settings.get()).hide_in_qam,
  tabs: [
    {
      // The whole form comes from "settings" in module.json: no wiring needed.
      label: "Controls",
      async render(el, ctx) {
        gameEl = ui.info("");
        showGame(ctx.game());
        const form = await ui.settingsForm(ctx, { keys: ["enabled", "hdr", "volume", "fps", "mode", "quality"] });
        forms.push(form);
        controlsForm = form;
        el.append(gameEl, form);
      },
    },
    {
      label: "Text",
      async render(el, ctx) {
        summaryEl = ui.info("");
        const form = await ui.settingsForm(ctx, { keys: ["name", "secret", "hide_in_qam"], navHints: false });
        forms.push(form);
        // navHints: false: the hint bar shows each field's own hint alone, without the button help.
        // Controls can be driven from code: set() a value, setDisabled() with a reason.
        const volume = () => controlsForm?.controls.volume;
        el.append(
          form,
          ui.separator(),
          ui.toggle({
            label: "Lock volume",
            value: false,
            hint: "disables Volume in Controls",
            onChange: (on) => volume()?.setDisabled(on, "Locked from Demo › Text"),
          }),
          ui.button({
            label: "Preview Volume at 100%",
            // set() only moves what is drawn: nothing is saved and onChange doesn't fire.
            onClick: () => volume()?.set(100),
          }),
          ui.button({
            label: "Reset values",
            onClick: async () => {
              if (!(await ui.confirm("Reset all of Demo's values?", { ok: "Reset" }))) return;
              for (const f of forms) await f.reset();
              await showSummary(ctx);
              ctx.toast("Values reset");
            },
          }),
          summaryEl,
        );
        await showSummary(ctx);
      },
      // Each sub-tab can react to becoming visible, e.g. to refresh what it shows.
      onShow: (ctx) => void showSummary(ctx),
    },
    {
      // Big windows: a gallery (ui.imageGrid), a form with sub-tabs, a long list.
      label: "Windows",
      render(el, ctx) {
        spaceEl = ui.info("");
        showSpace(ctx.canOpenWindow());
        unsubscribeSpace = ctx.onSpaceChange(showSpace);
        let handle: ReturnType<ModuleCtx["openWindow"]> = null;
        el.append(
          ui.info("The expanded view takes almost the whole screen and has its own tabs (L1/R1, L2/R2). B closes it."),
          // Locks itself (and says why) where there's no room, e.g. ··· during a game.
          ui.windowButton(ctx, { open: () => demoWindowSpec(ctx) }),
          ui.button({
            label: "Open with a handle",
            hint: "A open · ctx.openWindow returns null where there's no room",
            onClick: () => (handle = ctx.openWindow(handleWindowSpec(ctx, () => handle))),
          }),
          spaceEl,
          ui.image(cover(4), { alt: "Cover 5", maxHeight: 120 }),
          ui.info("ui.image above: a preview (grey while loading, ⚠ if it fails)."),
        );
      },
    },
    {
      // Scroll test: long text between controls, many items, and text after the last one.
      label: "Long list",
      render(el) {
        const long = Array.from({ length: 20 }, (_, i) =>
          ui.info(`Paragraph ${i + 1}: long non-navigable text, to check that ↓ pages through it without losing the selection and that the shadows show there is more content.`),
        );
        const items = Array.from({ length: 30 }, (_, i) => ui.checkbox({ label: `Item ${i + 1}`, value: i % 3 === 0 }));
        el.append(
          ui.toggle({ label: "First control", value: true }),
          ...long,
          ui.toggle({ label: "After the text", value: false }),
          ui.section("Items", items),
          ui.info("End of the list: this text comes after the last control."),
          ...Array.from({ length: 4 }, () => ui.info("…")),
        );
      },
    },
    {
      // module.json rules: `when` (one value, a list, several keys), `disabled_when`,
      // a folded section that only shows while Adaptive is on, and hints.
      label: "Rules",
      async render(el, ctx) {
        let form: SettingsForm | null = null;
        // The backend sets Fan when Preset changes (settings.on_change): show what it stored.
        const store = {
          schema: ctx.settings.schema,
          get: () => ctx.settings.get(),
          async set<T extends SettingValue>(key: string, value: T): Promise<T> {
            const stored = await ctx.settings.set(key, value);
            if (key === "preset") await form?.reload();
            return stored;
          },
        };
        form = await ui.settingsForm(
          { ...ctx, settings: store },
          { keys: ["preset", "fan", "adaptive", "multiplier", "target", "notify_preset", "notify_text"] },
        );
        forms.push(form);
        el.append(
          ui.info("Fan is disabled while a preset sets it. Multiplier hides with Adaptive on; the Adaptive section shows instead."),
          form,
          ui.separator(),
          // Not a setting: a plain control. Options with `swatch` show as colour dots.
          ui.radio({
            label: "Colour",
            value: "#1a9fff",
            options: [
              { value: "#1a9fff", label: "Blue", swatch: "#1a9fff" },
              { value: "#59bf40", label: "Green", swatch: "#59bf40" },
              { value: "#e5a50a", label: "Amber", swatch: "#e5a50a" },
              { value: "#d0413e", label: "Red", swatch: "#d0413e" },
            ],
            onChange: (v) => ctx.toast(`Colour: ${v}`),
          }),
        );
      },
    },
    {
      // The backend contract from the UI: ctx.call, notifications, Steam's API (backend
      // and here), clean errors, storage, a form stored by the module, a custom control.
      label: "Backend",
      async render(el, ctx) {
        statsEl = ui.info("");
        const path = "System.GetSystemInfo";
        el.append(
          statsEl,
          ui.section("Calls", [
            callButton(ctx, "Who am I", async () => JSON.stringify(await ctx.call("whoami"))),
            callButton(ctx, "Notify", async () => {
              await ctx.call("notify", { title: "Demo", body: "A notification from Demo's backend" });
              return "Notification sent";
            }),
            callButton(ctx, "Notify (async method)", async () => {
              await ctx.call("notify_async", { title: "Demo", body: "From an async def method" });
              return "Notification sent";
            }),
            callButton(ctx, "Steam info (backend)", async () => JSON.stringify(await ctx.call("steam_info"))),
            callButton(ctx, "Steam info (async method)", async () => JSON.stringify(await ctx.call("steam_info_async"))),
            callButton(ctx, "Steam info (from here)", async () => {
              const where = ctx.steam.hasApi(path) ? "this window" : "SharedJSContext";
              try {
                return `${where}: ${JSON.stringify(await ctx.steam.safeCall(path))}`;
              } catch (e) {
                return `Not available (plan B): ${(e as Error).message}`;
              }
            }),
            callButton(ctx, "Fail: invalid argument", () => ctx.call("fail", { kind: "invalid" })),
            callButton(ctx, "Fail: unavailable", () => ctx.call("fail", { kind: "unavailable" })),
            callButton(ctx, "Fail: choose how", async () => {
              const kind = await ui.choose("Which error should the backend raise?", [
                { label: "Invalid argument (400)", value: "invalid" },
                { label: "Unavailable (503)", value: "unavailable" },
              ]);
              return kind === null ? "Cancelled" : ctx.call("fail", { kind });
            }),
          ]),
          // module.json "forms": the backend validates with ctx.forms and stores it in a TOML file.
          ui.section(
            "Profile (stored by the module)",
            [
              await ui.form(ctx, "profile", {
                get: () => ctx.call("profile_get"),
                set: (key, value) => ctx.call("profile_set", { key, value }),
              }),
            ],
            { open: false },
          ),
          ui.section("Custom control", [counter("Counter", (n) => (n === 10 ? ctx.toast("Ten!") : undefined))]),
        );
      },
      onShow(ctx) {
        const game = currentGame(ctx.game())?.game;
        void ctx.call("visit", { appid: game?.appid ?? null }).catch(() => {});
        void showStats(ctx);
        statsTimer = window.setInterval(() => void showStats(ctx), 1000);
      },
      onHide() {
        window.clearInterval(statsTimer);
        statsTimer = undefined;
      },
    },
  ],
  onShow: (ctx) => showGame(ctx.game()),
  onHide: () => {
    window.clearInterval(statsTimer);
    statsTimer = undefined;
  },
  onGameChange: (game) => showGame(game),
  destroy: () => {
    window.clearInterval(statsTimer);
    unsubscribeSpace?.();
    gameEl = summaryEl = spaceEl = statsEl = null;
    controlsForm = null;
    unsubscribeSpace = null;
    forms.length = 0;
  },
});
