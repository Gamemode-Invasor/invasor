// Builds the core (frontend/dist/invasor.js) and, separately, each module's UI
// (modules/<id>/ui.ts -> modules/<id>/dist/ui.js). The backend injects the core first
// and then every module on its own, so a broken module can't take anything else down.
//
// A module bundle imports "invasor" from src/kit-shim.ts: the kit (ui, defineModule)
// comes from the already injected core at run time, never copied into the module.
import { build, context } from "esbuild";
import { existsSync, readdirSync, rmSync } from "node:fs";
import { basename, join, resolve } from "node:path";

const here = import.meta.dirname;
const modulesDir = resolve(here, "../modules");
const ID = /^[a-z0-9][a-z0-9_-]*$/; // same rule as backend/invasor/schema.py

// Left over from the single-bundle days: nothing imports it any more.
rmSync(join(here, "src/modules.gen.ts"), { force: true });

const common = {
  bundle: true,
  format: "iife",
  target: "chrome109",
  minify: true,
  logLevel: "info",
};

const core = {
  ...common,
  entryPoints: [join(here, "src/main.ts")],
  loader: { ".css": "text" },
  outfile: join(here, "dist/invasor.js"),
};

/** Build options for one module folder (its name is the module id). */
function moduleBuild(dir) {
  const id = basename(dir);
  if (!ID.test(id)) {
    console.error(`${dir}: invalid module id "${id}" (use lowercase letters, digits, - and _)`);
    process.exit(1);
  }
  return {
    ...common,
    entryPoints: [join(dir, "ui.ts")],
    globalName: "__invasorModule",
    alias: { invasor: join(here, "src/kit-shim.ts") },
    outfile: join(dir, "dist/ui.js"),
    logLevel: "warning",
  };
}

function moduleBuilds() {
  // Modules developed outside the core (their own repository):
  //   node build.mjs --module ../../invasor-ducky/ducky [--module …]
  const external = process.argv.flatMap((a, i, all) => (a === "--module" && all[i + 1] ? [resolve(all[i + 1])] : []));
  for (const dir of external) {
    if (!existsSync(join(dir, "module.json")) || !existsSync(join(dir, "ui.ts"))) {
      console.error(`${dir}: not a module with a ui.ts (needs module.json and ui.ts)`);
      process.exit(1);
    }
  }
  if (external.length) return external.map(moduleBuild);
  if (!existsSync(modulesDir)) return [];
  const out = [];
  for (const d of readdirSync(modulesDir, { withFileTypes: true })) {
    // "_name"/".name" are templates/hidden: never loaded, so never built.
    if (!d.isDirectory() || /^[_.]/.test(d.name) || !existsSync(join(modulesDir, d.name, "ui.ts"))) continue;
    out.push(moduleBuild(join(modulesDir, d.name)));
  }
  return out;
}

const mods = moduleBuilds();
console.log(`modules with UI: ${mods.map((m) => m.outfile.split("/").at(-3)).join(", ") || "(none)"}`);

if (process.argv.includes("--watch")) {
  for (const options of [core, ...mods]) await (await context(options)).watch();
} else {
  await Promise.all([core, ...mods].map((options) => build(options)));
}
