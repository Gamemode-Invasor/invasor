// ctx.pickPath: a file/folder browser in a big window (core.browse_path), from Companion's and Opti's.
// ".." goes up, a folder opens; a file is picked by pressing it, a folder with the choose button.

import type { ModuleCtx, PickPathOptions, WindowHandle } from "../module-api";
import { ui } from "./controls";

/** One folder, as core.browse_path lists it. */
export interface Dir {
  path: string;
  parent: string | null;
  dirs: { name: string; label: string }[];
  files: { name: string }[];
  error: string | null;
}

export type ListDir = (args: { path?: string; files: boolean; ext?: string[]; hidden: boolean }) => Promise<Dir>;

const join = (dir: string, name: string) => (dir === "/" ? "/" : dir + "/") + name;
const errorText = (e: unknown) => (e as Error)?.message ?? String(e);
// The picker each module has open: opening another closes it (it resolves null).
const open = new Map<string, WindowHandle>();

export function pickPath(ctx: ModuleCtx, list: ListDir, o: PickPathOptions): Promise<string | null> {
  return new Promise((resolve) => {
    let done = false;
    const finish = (path: string | null) => {
      if (done) return;
      done = true;
      resolve(path);
    };
    const files = o.files === true;
    const hidden = o.hidden === true;
    let handle: WindowHandle | null = null;
    const pick = (path: string) => {
      finish(path);
      handle?.close();
    };

    let shown = 0; // a slow answer for a folder the user already left is dropped
    async function show(box: HTMLElement, path?: string) {
      const n = ++shown;
      let items: HTMLElement[];
      try {
        items = folder(await list({ path, files, ext: files ? o.ext : undefined, hidden }));
      } catch (e) {
        items = [ui.info(`Can't read this folder: ${errorText(e)}`), ui.button({ label: "Go to home", onClick: () => void show(box) })];
      }
      if (n !== shown) return;
      box.replaceChildren(...items);
      // Put the ring on the first button, not at whatever index it had in the last folder.
      const first = box.querySelector<HTMLElement>("[data-nav]");
      if (first) box.dispatchEvent(new CustomEvent("invasor:refocus", { detail: first, bubbles: true }));

      function folder(dir: Dir): HTMLElement[] {
        const go = (p: string) => () => void show(box, p);
        const items: HTMLElement[] = [ui.info(dir.path)];
        if (dir.error) items.push(ui.info(`Can't read this folder: ${dir.error}`));
        if (!files) items.push(ui.button({ label: o.chooseLabel ?? "Choose this folder", onClick: () => pick(dir.path) }));
        if (dir.parent) items.push(ui.button({ label: "..", onClick: go(dir.parent) }));
        for (const d of dir.dirs) items.push(ui.button({ label: d.label, onClick: go(join(dir.path, d.name)) }));
        for (const f of dir.files) items.push(ui.button({ label: f.name, onClick: () => pick(join(dir.path, f.name)) }));
        if (!dir.dirs.length && !dir.files.length) items.push(ui.info("Nothing here."));
        return items;
      }
    }

    open.get(ctx.id)?.close();
    handle = ctx.openWindow({
      title: o.title,
      render: (box) => show(box, o.start || undefined),
      onClose: () => {
        if (open.get(ctx.id) === handle) open.delete(ctx.id);
        finish(null);
      },
    });
    if (handle) open.set(ctx.id, handle);
    else finish(null);
  });
}
