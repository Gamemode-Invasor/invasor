// What a module's `import { ui, defineModule } from "invasor"` resolves to in its own
// bundle (see build.mjs): the kit published by the already injected core. Types come
// from module-api.ts (tsconfig "paths"), so modules typecheck against the real thing.
import type * as Kit from "./module-api";

const kit = window.__invasorKit;
if (!kit) throw new Error("Invasor core isn't loaded");

export const ui: typeof Kit.ui = kit.ui;
export const defineModule: typeof Kit.defineModule = kit.defineModule;
export const KIT_API: typeof Kit.KIT_API = kit.api;
export const currentGame: typeof Kit.currentGame = kit.currentGame;
