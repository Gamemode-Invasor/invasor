// The kit the core publishes for module bundles (window.__invasorKit, see kit-shim.ts),
// and the registry their definitions land in. The backend injects each module's
// dist/ui.js after the core; its last statement is __invasorKit.register(id, def).

import { currentGame, defineModule, KIT_API, ui, type ModuleDef } from "./module-api";

export interface InvasorKit {
  api: number;
  ui: typeof ui;
  defineModule: typeof defineModule;
  currentGame: typeof currentGame;
  register(id: string, def: unknown): void;
}

declare global {
  interface Window {
    __invasorKit?: InvasorKit;
  }
}

/** A registered module UI, or why it couldn't be used. */
export type Registered = { def: ModuleDef } | { error: string };

export interface KitRegistry {
  get(id: string): Registered | undefined;
  /** Called after every registration (e.g. to rebuild the tabs). */
  onRegister(cb: () => void): void;
  destroy(): void;
}

function check(def: unknown): Registered {
  if (!def || typeof def !== "object") return { error: "ui.ts has no `export default defineModule({...})`" };
  const d = def as ModuleDef;
  const hasTabs = Array.isArray(d.tabs) && d.tabs.length > 0;
  if (typeof d.render !== "function" && !hasTabs) return { error: "the module defines neither render() nor tabs" };
  if (hasTabs) {
    const bad = d.tabs!.findIndex((t) => !t || typeof t.label !== "string" || typeof t.render !== "function");
    if (bad >= 0) return { error: `tabs[${bad}] needs a label and a render()` };
  }
  return { def: d };
}

export function publishKit(): KitRegistry {
  const defs = new Map<string, Registered>();
  let listener: (() => void) | null = null;
  const kit: InvasorKit = {
    api: KIT_API,
    ui,
    defineModule,
    currentGame,
    register(id, def) {
      const r = check(def);
      if ("error" in r) console.error(`[invasor] module ${id}: ${r.error}`);
      defs.set(id, r);
      try {
        listener?.();
      } catch (e) {
        console.error("[invasor] module registration listener failed", e);
      }
    },
  };
  window.__invasorKit = kit;
  return {
    get: (id) => defs.get(id),
    onRegister: (cb) => void (listener = cb),
    destroy() {
      listener = null;
      // Only our own: a newer injection may already have published its kit.
      if (window.__invasorKit === kit) delete window.__invasorKit;
    },
  };
}
