import { makeApi, type InvasorCfg } from "./api";
import { publishKit, type KitRegistry } from "./kit";
import { trackHighlight, type TileInfo } from "./library";
import { createOverlay } from "./ui/overlay";

declare global {
  interface Window {
    __INVASOR_CFG?: InvasorCfg;
    __invasor?: {
      version: string;
      toggle(): void;
      setOpen(open: boolean): void;
      state(): { role: string; open: boolean; focused: boolean; available: boolean };
      /** Hide this window's "I" while another window shows its own (set by the backend). */
      setHandleHidden(hidden: boolean): void;
      /** The game tile highlighted in this window's library, if any (asked by the backend). */
      highlighted(): TileInfo | null;
      destroy(): void;
    };
    /** Identifies the latest injection (see boot()). */
    __invasorBoot?: object;
  }
}

// The backend evaluates this bundle and then, right away, each module's UI (which calls
// __invasorKit.register). So the kit is published synchronously, here, before anything
// else; the overlay itself waits for a <body> if the page is still loading.
function boot() {
  // Injection is idempotent: the backend re-injects on reloads, so tear down any previous copy.
  try {
    window.__invasor?.destroy();
  } catch (e) {
    console.warn("[invasor] previous instance teardown failed", e);
  }

  const cfg = window.__INVASOR_CFG;
  if (!cfg) {
    console.error("[invasor] missing __INVASOR_CFG, not starting");
    return;
  }
  const kit = publishKit();
  // A newer injection may happen before this one's page finished loading: only the
  // latest boot may start an overlay.
  const token = {};
  window.__invasorBoot = token;
  const ready = () => {
    if (window.__invasorBoot !== token) return;
    try {
      start(cfg, kit);
    } catch (e) {
      console.error("[invasor] failed to start", e);
    }
  };
  if (document.body) ready();
  else window.addEventListener("DOMContentLoaded", ready, { once: true });
}

function start(cfg: InvasorCfg, kit: KitRegistry) {
  // Single source of the version: the backend (backend/invasor/__init__.py).
  const VERSION = cfg.version ?? "dev";
  const api = makeApi(cfg);
  const overlay = createOverlay(api, VERSION, cfg.role, kit);
  const highlight = trackHighlight(document.getElementById("invasor-root") ?? document.body);
  // Diagnostics: tell the backend where we landed and how big the window is.
  api
    .call("core", "report", {
      role: cfg.role,
      title: document.title,
      size: `${window.innerWidth}x${window.innerHeight}`,
      screen: `${screen.width}x${screen.height}`,
      dpr: window.devicePixelRatio,
    })
    .catch(() => {});

  // Keyboard toggle; the gamepad combo (L3+R3) is detected by the backend.
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "F10") {
      e.preventDefault();
      overlay.toggle();
    }
  };
  window.addEventListener("keydown", onKey, true);

  window.__invasor = {
    version: VERSION,
    toggle: () => overlay.toggle(),
    // Used by the backend's gamepad combo to keep every window's panel in sync.
    setOpen: (open: boolean) => overlay.setOpen(open),
    state: () => ({ role: cfg.role, open: overlay.isOpen(), focused: document.hasFocus(), available: overlay.isAvailable() }),
    setHandleHidden: (hidden: boolean) => overlay.setHandleHidden(hidden),
    highlighted: () => highlight.get(),
    destroy() {
      window.removeEventListener("keydown", onKey, true);
      highlight.destroy();
      overlay.destroy();
      kit.destroy();
      delete window.__invasor;
    },
  };
  console.log(`[invasor] ${VERSION} loaded`);
}

boot();
