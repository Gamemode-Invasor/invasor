// The built-in "Settings" tab: always present, even with no modules installed.
// Built with the same control kit modules use.

import type { Api } from "../api";
import type { SettingsSchema, WindowHandle, WindowSpec } from "../module-api";
import { steamAvailable } from "../steam";
import { ui } from "./controls";
import { ACCENT_COLORS } from "./palette";
import { reorderList } from "./reorder";

/** One entry of core.modules (backend/invasor/modules.py listing()). */
export interface ModuleInfo {
  id: string;
  name: string;
  tab: string;
  version?: string;
  description: string;
  /** module.json "author", name only ("" if none). */
  author: string;
  order: number;
  enabled: boolean;
  loaded: boolean;
  /** Invalid module.json: the module isn't loaded at all. */
  error: string | null;
  /** "core": shipped with Invasor; "user": installed by the user (can be uninstalled). */
  source: "core" | "user";
  /** Not shown in Quick Access (···), only in the library's panel. */
  no_qam?: boolean;
  /** Has a UI (ui.ts or dist/ui.js), and whether it's built. */
  ui: boolean;
  ui_built: boolean;
  settings: SettingsSchema;
  forms: Record<string, SettingsSchema>;
}

interface Prefs {
  open_combo: string[];
  panel_side: "auto" | "left" | "right";
  accent_color: string;
  handle_icon: "icon" | "letter" | "none";
  module_order: string[];
  update_check: boolean;
  update_channel: "stable" | "beta";
}

/** core.update_check / core.update_status (backend/invasor/updater.py). `checked` is false
 * when GitHub couldn't be asked or has no usable release: not an error. */
interface UpdateInfo {
  current: string;
  latest: string | null;
  available: boolean;
  /** `available` is the stable version, older than this pre-release: the way back. */
  downgrade: boolean;
  checked: boolean;
  notes: string;
}

interface Pad {
  device: string;
  name: string;
  kind: string;
}

export interface SettingsDeps {
  api: Api;
  version: string;
  toast(message: string, kind?: "ok" | "error"): void;
  /** A module was enabled/disabled: rebuild the module tabs. */
  onModulesChanged(): Promise<unknown>;
  /** Panel side preference changed: apply it now. */
  onPanelSide(side: Prefs["panel_side"]): void;
  /** Accent colour preference changed: apply it now. */
  onAccentColor(name: string): void;
  /** Handle icon preference changed: apply it now. */
  onHandleIcon(mode: string): void;
  /** Open a big window over the panel (null, with a toast, where there's no room). */
  openWindow(spec: WindowSpec): WindowHandle | null;
}

// Combos offered in the UI. L4/R4/L5/R5 only exist on Steam Deck-protocol pads (Deck, Legion Go…).
const COMBOS: { value: string; label: string }[] = [
  { value: "L3+R3", label: "L3 + R3" },
  { value: "L4+R4", label: "L4 + R4 (back)" },
  { value: "L5+R5", label: "L5 + R5 (back)" },
];

const comboKey = (buttons: string[]) => [...buttons].sort().join("+");

// The latest draw of each Settings pane: an older one still waiting for the backend must not
// add its sections after a newer one did (two quick actions would show everything twice).
const drawings = new WeakMap<HTMLElement, number>();

export async function renderSettings(el: HTMLElement, deps: SettingsDeps) {
  const drawing = (drawings.get(el) ?? 0) + 1;
  drawings.set(el, drawing);
  // After installing/uninstalling a module the whole tab is drawn again (fresh list).
  const rerender = () => {
    el.replaceChildren();
    void renderSettings(el, deps).catch((e) => el.append(ui.info(`Settings failed: ${(e as Error).message}`)));
  };
  const { api, toast } = deps;
  const [modules, prefs] = await Promise.all([
    api.call<ModuleInfo[]>("core", "modules"),
    api.call<Prefs>("core", "prefs"),
  ]);

  // --- Modules
  const moduleControls = modules.length
    ? modules.flatMap((m, i) => {
        // A change the backend rejects is undone on screen too (same below).
        const box = ui.checkbox({
          label: `${m.name}${m.version ? ` (${m.version})` : ""}${m.author ? ` by ${m.author}` : ""}`,
          hint: m.description || undefined,
          value: m.enabled,
          onChange: async (on) => {
            try {
              await api.call("core", "set_enabled", { id: m.id, enabled: on });
              toast(`${m.name} ${on ? "enabled" : "disabled"}`);
            } catch (e) {
              box.set(!on);
              toast(`Couldn't change ${m.name}: ${(e as Error).message}`, "error");
            }
            await deps.onModulesChanged();
          },
        });
        const extra: HTMLElement[] = m.error ? [ui.info(`⚠ ${m.error}`)] : [];
        extra.push(
          ui.button({
            label: `Uninstall ${m.name}`,
            onClick: async () => {
              const shipped = m.source === "core" ? " It comes with Invasor: it stays uninstalled after updates, and you can restore it from this section." : "";
              const purge = await ui.choose(`Uninstall ${m.name}? Its settings and data can be kept in case you install it again.${shipped}`, [
                { label: "Uninstall, keep its settings", value: false },
                { label: "Uninstall and delete its data", value: true },
              ]);
              if (purge === null) return;
              try {
                await api.call("core", "module_uninstall", { id: m.id, purge });
                toast(purge ? `${m.name} and its data uninstalled` : `${m.name} uninstalled`);
              } catch (e) {
                toast(`Couldn't uninstall ${m.name}: ${(e as Error).message}`, "error");
              }
              await deps.onModulesChanged();
              rerender();
            },
          }),
        );
        return [...(i ? [ui.separator()] : []), box, ...extra]; // a line between modules
      })
    : [ui.info("No modules installed.")];

  // --- Rescan: pick up module folders added, removed or edited on disk, no service restart
  moduleControls.push(
    ui.separator(),
    ui.button({
      label: "Rescan modules",
      onClick: async () => {
        let after: ModuleInfo[];
        try {
          after = await api.call<ModuleInfo[]>("core", "module_rescan");
        } catch (e) {
          return toast(`Couldn't rescan: ${(e as Error).message}`, "error");
        }
        const was = new Set(modules.map((m) => m.id));
        const now = new Set(after.map((m) => m.id));
        const added = after.filter((m) => !was.has(m.id)).map((m) => m.name);
        const removed = modules.filter((m) => !now.has(m.id)).map((m) => m.name);
        const changes = [added.length ? `added ${added.join(", ")}` : "", removed.length ? `removed ${removed.join(", ")}` : ""];
        toast(`Modules rescanned${added.length || removed.length ? `: ${changes.filter(Boolean).join("; ")}` : ""}`);
        await deps.onModulesChanged();
        rerender();
      },
    }),
  );

  // --- Shipped modules the user uninstalled (Demo): they can come back
  const gone = await api.call<{ id: string; name: string }[]>("core", "module_removed").catch(() => []);
  for (const g of gone) {
    moduleControls.push(
      ui.button({
        label: `Restore ${g.name}`,
        onClick: async () => {
          try {
            await api.call("core", "module_restore", { id: g.id });
            toast(`${g.name} restored`);
          } catch (e) {
            toast(`Couldn't restore ${g.name}: ${(e as Error).message}`, "error");
          }
          await deps.onModulesChanged();
          rerender();
        },
      }),
    );
  }

  // --- Install a module from a zip (the core checks everything before installing)
  const picker = document.createElement("div");
  const formatSize = (n: number | null) => (n === null ? "" : n > 1 << 20 ? ` (${(n / (1 << 20)).toFixed(1)} MB)` : ` (${Math.max(1, Math.round(n / 1024))} KB)`);
  async function installFrom(path: string) {
    let info: { id: string; name: string; version: string; description: string; author: string; installed_version: string | null };
    try {
      info = await api.call("core", "module_inspect", { path });
    } catch (e) {
      return toast(`Not installed: ${(e as Error).message}`, "error");
    }
    const replacing = info.installed_version !== null;
    const by = info.author ? ` by ${info.author}` : "";
    const what = `${info.name} ${info.version}${by}${info.description ? ` — ${info.description}` : ""}`;
    const question = replacing
      ? `Replace the installed ${info.name} ${info.installed_version} with ${info.version}${by}?`
      : `Install ${what}?`;
    if (!(await ui.confirm(question, { ok: replacing ? "Replace" : "Install" }))) return;
    try {
      await api.call("core", "module_install", { path, replace: replacing });
      toast(`${info.name} ${info.version} installed`);
    } catch (e) {
      return toast(`Not installed: ${(e as Error).message}`, "error");
    }
    await deps.onModulesChanged();
    rerender();
  }
  async function browse(path?: string) {
    let listing: { path: string; parent: string | null; entries: { name: string; kind: "dir" | "zip"; size: number | null }[] };
    try {
      listing = await api.call("core", "module_browse", path === undefined ? {} : { path });
    } catch (e) {
      picker.replaceChildren(ui.info(`Can't browse: ${(e as Error).message}`));
      return;
    }
    const here = (name: string) => (listing.path ? `${listing.path}/${name}` : name);
    const rows: HTMLElement[] = [ui.info(`~/${listing.path}`)];
    if (listing.parent !== null) rows.push(ui.button({ label: "⬑ Up", onClick: () => void browse(listing.parent!) }));
    for (const e of listing.entries) {
      rows.push(
        e.kind === "dir"
          ? ui.button({ label: `📁 ${e.name}`, onClick: () => void browse(here(e.name)) })
          : ui.button({ label: `📦 ${e.name}${formatSize(e.size)}`, onClick: () => void installFrom(here(e.name)) }),
      );
    }
    if (!listing.entries.length) rows.push(ui.info("No folders or .zip files here."));
    picker.replaceChildren(...rows);
  }
  const startBrowsing = ui.button({ label: "Choose a module .zip…", onClick: () => void browse() });
  picker.append(startBrowsing);

  // --- Controller
  const current = comboKey(prefs.open_combo);
  const comboOptions = [...COMBOS];
  if (!comboOptions.some((c) => c.value === current)) {
    comboOptions.unshift({ value: current, label: `Custom (${prefs.open_combo.join(" + ")})` });
  }
  let savedCombo = current;
  const combo = ui.select({
    label: "Open/close panel",
    value: current,
    options: comboOptions,
    onChange: async (value) => {
      try {
        await api.call("core", "set_pref", { key: "open_combo", value: value.split("+") });
        savedCombo = value;
        toast(`Shortcut: ${comboOptions.find((c) => c.value === value)?.label ?? value}`);
      } catch (e) {
        combo.set(savedCombo);
        toast(`Invalid shortcut: ${(e as Error).message}`, "error");
      }
    },
  });

  // --- Panel
  let savedSide = prefs.panel_side;
  const side = ui.radio({
    label: "Panel side",
    hint: "Only for the Library. Quick Access always shows the panel on the left.",
    value: prefs.panel_side,
    options: [
      { value: "auto" as const, label: "Auto" },
      { value: "left" as const, label: "Left" },
      { value: "right" as const, label: "Right" },
    ],
    onChange: async (value) => {
      deps.onPanelSide(value);
      try {
        await api.call("core", "set_pref", { key: "panel_side", value });
        savedSide = value;
      } catch (e) {
        side.set(savedSide);
        deps.onPanelSide(savedSide);
        toast(`Couldn't save: ${(e as Error).message}`, "error");
      }
    },
  });

  let savedHandle: string = prefs.handle_icon ?? "icon";
  const handleIcon = ui.radio({
    label: "Handle icon",
    value: savedHandle,
    options: [
      { value: "icon", label: "Icon" },
      { value: "letter", label: "Letter I" },
      { value: "none", label: "None" },
    ],
    onChange: async (value) => {
      deps.onHandleIcon(value);
      try {
        await api.call("core", "set_pref", { key: "handle_icon", value });
        savedHandle = value;
      } catch (e) {
        handleIcon.set(savedHandle);
        deps.onHandleIcon(savedHandle);
        toast(`Couldn't save: ${(e as Error).message}`, "error");
      }
    },
  });

  let savedColor = prefs.accent_color;
  const color = ui.radio({
    label: "Accent color",
    value: prefs.accent_color,
    options: ACCENT_COLORS.map((c) => ({ value: c.value as string, label: c.label, swatch: c.hex })),
    onChange: async (value) => {
      deps.onAccentColor(value);
      try {
        await api.call("core", "set_pref", { key: "accent_color", value });
        savedColor = value;
      } catch (e) {
        color.set(savedColor);
        deps.onAccentColor(savedColor);
        toast(`Couldn't save: ${(e as Error).message}`, "error");
      }
    },
  });

  // --- Module order: the backend lists the modules already in the user's order
  const orderControls = [
    ...reorderList(
      modules.map((m) => ({ id: m.id, label: m.name, dim: !m.enabled || !!m.error })),
      async (ids) => {
        try {
          await api.call("core", "set_pref", { key: "module_order", value: ids });
        } catch (e) {
          toast(`Couldn't save the order: ${(e as Error).message}`, "error");
          throw e;
        }
        await deps.onModulesChanged();
      },
    ),
    ui.separator(),
    ui.button({
      label: "Reset order",
      onClick: async () => {
        try {
          await api.call("core", "set_pref", { key: "module_order", value: [] });
          toast("Module order reset");
        } catch (e) {
          toast(`Couldn't reset the order: ${(e as Error).message}`, "error");
        }
        await deps.onModulesChanged();
        rerender();
      },
    }),
  ];

  // --- Updates
  const updates = document.createElement("div");
  const showUpdate = (u: UpdateInfo | null) => {
    const rows: HTMLElement[] = [];
    if (!u) rows.push(ui.info(`Installed: ${deps.version}. Not checked yet.`));
    else if (!u.checked) rows.push(ui.info(`Installed: ${u.current}. Couldn't check for updates right now.`));
    else if (!u.available) rows.push(ui.info(`Installed: ${u.current}. You're up to date.`));
    else {
      rows.push(
        ui.info(
          u.downgrade
            ? `Installed: ${u.current} (pre-release). The stable version is ${u.latest}.`
            : `Installed: ${u.current}. New version available: ${u.latest}.`,
        ),
      );
      // One row per line: a single paragraph would run the lines of the notes together.
      for (const line of u.notes.split("\n")) if (line.trim()) rows.push(ui.info(line.trim()));
    }
    rows.push(
      ui.button({
        label: "Check for updates",
        onClick: async () => {
          try {
            showUpdate(await api.call<UpdateInfo>("core", "update_check"));
          } catch (e) {
            toast(`Couldn't check for updates: ${(e as Error).message}`, "error");
          }
        },
      }),
      ui.button({
        label: u?.available ? (u.downgrade ? `Go back to ${u.latest}` : `Install version ${u.latest}`) : "Install update",
        disabled: !u?.available,
        onClick: async () => {
          const ok = await ui.confirm(
            `${u?.downgrade ? "Go back to" : "Install"} Invasor ${u?.latest}? The panel will reload in a few seconds; Steam and any running game are not restarted.`,
            { ok: u?.downgrade ? "Go back and reload panel" : "Install and reload panel" },
          );
          if (!ok) return;
          try {
            await api.call("core", "update_apply");
            toast("Updating… the panel will reload");
          } catch (e) {
            toast(`Update failed: ${(e as Error).message}`, "error");
          }
        },
      }),
      autoCheck,
      channel,
    );
    updates.replaceChildren(...rows);
  };
  let savedAuto = prefs.update_check;
  const autoCheck = ui.checkbox({
    label: "Check for updates automatically",
    hint: "At startup and then daily, asking GitHub. Steam shows a notification when there is a new version.",
    value: prefs.update_check,
    onChange: async (on) => {
      try {
        await api.call("core", "set_pref", { key: "update_check", value: on });
        savedAuto = on;
      } catch (e) {
        autoCheck.set(savedAuto);
        toast(`Couldn't save: ${(e as Error).message}`, "error");
      }
    },
  });
  let savedChannel = prefs.update_channel;
  const channel = ui.radio({
    label: "Update channel",
    value: prefs.update_channel,
    hint: "Beta also offers pre-releases (X.Y.Z-rcN) to try before they are final. They may be unstable.",
    options: [
      { value: "stable" as const, label: "Stable" },
      { value: "beta" as const, label: "Beta (pre-releases)" },
    ],
    onChange: async (value) => {
      try {
        await api.call("core", "set_pref", { key: "update_channel", value });
        savedChannel = value;
      } catch (e) {
        channel.set(savedChannel);
        toast(`Couldn't save: ${(e as Error).message}`, "error");
        return;
      }
      // Like the "Check for updates" button: look for a version on the chosen channel right away.
      try {
        showUpdate(await api.call<UpdateInfo>("core", "update_check"));
      } catch (e) {
        toast(`Couldn't check for updates: ${(e as Error).message}`, "error");
      }
    },
  });
  showUpdate(await api.call<UpdateInfo | null>("core", "update_status").catch(() => null));

  // --- About
  const about = document.createElement("div");
  const refreshAbout = async () => {
    about.innerHTML = "";
    try {
      const [info, pads] = await Promise.all([
        api.call<{ version: string; python: string }>("core", "info"),
        api.call<Pad[]>("core", "pads"),
      ]);
      about.append(
        ui.info(`Invasor ${deps.version} · backend ${info.version} · Python ${info.python}`),
        ui.info(steamAvailable() ? "SteamClient: available" : "SteamClient: unavailable"),
        ui.info(`Controllers detected: ${pads.length ? "" : "none"}`),
        ...pads.map((p) => ui.info(`  · ${p.name} (${p.kind === "deck" ? "Steam Deck protocol" : "evdev"})`)),
      );
    } catch (e) {
      about.append(ui.info(`Backend unavailable: ${(e as Error).message}`));
    }
  };
  await refreshAbout();

  // --- Manage Invasor: updates, restart, log, reset, uninstall
  const LOG_LINES = 300;
  // The log in a big window (a long text doesn't fit the panel): monospace, scrolling, refreshable.
  const showLog = () => {
    deps.openWindow({
      title: "Invasor log",
      render: async (win) => {
        const box = document.createElement("pre");
        box.className = "ctl-log";
        const load = async () => {
          try {
            const text = await api.call<string>("core", "log_tail", { lines: LOG_LINES });
            box.textContent = text.trim() || "(the log is empty)";
            box.scrollTop = box.scrollHeight;
          } catch (e) {
            box.textContent = `Couldn't read the log: ${(e as Error).message}`;
          }
        };
        win.append(ui.button({ label: "Refresh", onClick: () => void load() }), box);
        await load();
      },
    });
  };
  /** Ask first, then call the backend; the same error toast for all of them. */
  const manage = (label: string, question: string, ok: string, method: string, done: string, args: Record<string, unknown> = {}) =>
    ui.button({
      label,
      onClick: async () => {
        if (!(await ui.confirm(question, { ok }))) return;
        try {
          await api.call("core", method, args);
          toast(done);
        } catch (e) {
          toast(`${label} failed: ${(e as Error).message}`, "error");
        }
      },
    });
  const manageControls = [
    updates,
    ui.separator(),
    manage(
      "Restart Invasor",
      "Restart the Invasor service? The panel reloads in a few seconds; Steam and any running game are not restarted.",
      "Restart",
      "restart_service",
      "Restarting… the panel will reload",
    ),
    manage(
      "Restart Steam",
      "Restart Steam? This closes the game that is running.",
      "Restart Steam",
      "restart_steam",
      "Steam is restarting…",
    ),
    ui.button({ label: "View log", onClick: showLog }),
    manage(
      "Reset configuration",
      "Go back to the default settings: panel side, colour, shortcut, module order and which modules are on. Installed modules and their own settings are kept. Invasor restarts.",
      "Reset and restart",
      "reset_config",
      "Configuration reset… the panel will reload",
    ),
    ui.button({
      label: "Uninstall Invasor",
      onClick: async () => {
        const purge = await ui.choose(
          "Uninstall Invasor? The service, the core and every module installed from a zip are removed. Your settings can be kept in case you install it again.",
          [
            { label: "Uninstall, keep my settings", value: false },
            { label: "Uninstall and delete my settings", value: true },
          ],
        );
        if (purge === null) return;
        try {
          await api.call("core", "uninstall_invasor", { purge });
          toast("Uninstalling Invasor…");
        } catch (e) {
          toast(`Couldn't uninstall: ${(e as Error).message}`, "error");
        }
      },
    }),
  ];

  if (drawings.get(el) !== drawing) return; // a newer draw took over while we waited
  el.append(
    ui.section("Manage Invasor", manageControls, { open: false }),
    ui.section("Manage modules", moduleControls, { open: false }),
    ui.section("Install module", [ui.info("Module zips are checked before anything is installed."), picker], { open: false }),
    ...(modules.length > 1 ? [ui.section("Module order", orderControls, { open: false })] : []),
    ui.section("Controller", [combo], { open: false }),
    ui.section("Panel", [side, handleIcon, color], { open: false }),
    ui.section("About", [about, ui.button({ label: "Refresh", onClick: () => void refreshAbout() })], { open: false }),
  );
}
