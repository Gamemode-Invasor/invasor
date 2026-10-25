import "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import { settle } from "./dom";
import { pickPath } from "../src/ui/pick-path";
import type { ModuleCtx, WindowSpec } from "../src/module-api";

interface Dir {
  path: string;
  parent: string | null;
  dirs: { name: string; label: string }[];
  files: { name: string }[];
  error: string | null;
}

const HOME = "/home/deck";

function dir(path: string, o: Partial<Dir> = {}): Dir {
  const parent = path === "/" ? null : path.slice(0, path.lastIndexOf("/")) || "/";
  return { path, parent, dirs: [], files: [], error: null, ...o };
}

/** A ctx whose openWindow renders the spec into a box in the page; close() fires onClose as the real one does. */
let ctxCount = 0;
function fakeCtx(o: { noRoom?: boolean; id?: string } = {}) {
  const id = o.id ?? `mod${++ctxCount}`;
  const windows: { spec: WindowSpec; box: HTMLElement; closed: boolean; close(): void }[] = [];
  const ctx = {
    id,
    toast() {},
    canOpenWindow: () => !o.noRoom,
    openWindow(spec: WindowSpec) {
      if (o.noRoom) return null;
      const box = document.body.appendChild(document.createElement("div"));
      const w = {
        spec,
        box,
        closed: false,
        close() {
          if (w.closed) return;
          w.closed = true;
          box.remove();
          spec.onClose?.();
        },
      };
      windows.push(w);
      void spec.render?.(box, ctx);
      return { close: () => w.close(), setTitle() {} };
    },
  } as unknown as ModuleCtx;
  return { ctx, windows, last: () => windows[windows.length - 1] };
}

/** A list fake: answers from a map by path (missing/undefined path → home). */
function fakeList(tree: Record<string, Dir>) {
  const calls: Record<string, unknown>[] = [];
  const list = (args: Record<string, unknown>) => {
    calls.push(args);
    const path = (args.path as string | null | undefined) || HOME;
    const d = tree[path] ?? tree[HOME];
    return Promise.resolve(d);
  };
  return { list, calls };
}

const buttons = (box: HTMLElement) => [...box.querySelectorAll("button")];
const byText = (box: HTMLElement, text: string) => buttons(box).find((b) => b.textContent === text);

async function flush() {
  for (let i = 0; i < 4; i++) await settle();
}

test("no room for a window resolves null", async () => {
  const { ctx } = fakeCtx({ noRoom: true });
  const { list } = fakeList({ [HOME]: dir(HOME) });
  assert.equal(await pickPath(ctx, list, { title: "Pick" }), null);
});

test("folder mode: the choose button picks the current folder and closes", async () => {
  const { ctx, last } = fakeCtx();
  const { list, calls } = fakeList({ [HOME]: dir(HOME, { dirs: [{ name: "a", label: "a" }] }) });
  const p = pickPath(ctx, list, { title: "Pick a folder" });
  await flush();
  const w = last();
  assert.equal(w.spec.title, "Pick a folder");
  assert.equal(calls[0].files ?? false, false);
  assert.match(w.box.textContent ?? "", new RegExp(HOME));
  const choose = byText(w.box, "Choose this folder");
  assert.ok(choose);
  choose.click();
  assert.equal(await p, HOME);
  assert.equal(w.closed, true);
});

test("folder mode with a custom chooseLabel and start", async () => {
  const { ctx, last } = fakeCtx();
  const { list, calls } = fakeList({ [HOME]: dir(HOME), "/games": dir("/games") });
  const p = pickPath(ctx, list, { title: "T", chooseLabel: "Use it", start: "/games" });
  await flush();
  assert.equal(calls[0].path, "/games");
  assert.equal(byText(last().box, "Choose this folder"), undefined);
  byText(last().box, "Use it")!.click();
  assert.equal(await p, "/games");
});

test("file mode lists files and pressing one picks its path", async () => {
  const { ctx, last } = fakeCtx();
  const { list, calls } = fakeList({
    [HOME]: dir(HOME, { dirs: [{ name: "sub", label: "sub" }], files: [{ name: "a.dll" }, { name: "b.dll" }] }),
  });
  const p = pickPath(ctx, list, { title: "Pick a file", files: true, ext: [".dll"] });
  await flush();
  assert.equal(calls[0].files, true);
  assert.deepEqual(calls[0].ext, [".dll"]);
  const box = last().box;
  assert.equal(byText(box, "Choose this folder"), undefined);
  assert.ok(byText(box, "sub"));
  byText(box, "b.dll")!.click();
  assert.equal(await p, `${HOME}/b.dll`);
  assert.equal(last().closed, true);
});

test("file mode at / joins without a double slash", async () => {
  const { ctx, last } = fakeCtx();
  const { list } = fakeList({ [HOME]: dir("/", { files: [{ name: "f" }] }) });
  const p = pickPath(ctx, list, { title: "T", files: true });
  await flush();
  byText(last().box, "f")!.click();
  assert.equal(await p, "/f");
});

test("entering a folder lists its joined path (by name, not label); .. only with a parent", async () => {
  const { ctx, last } = fakeCtx();
  const { list, calls } = fakeList({
    [HOME]: dir("/", { dirs: [{ name: "123", label: "123 - Some Game" }] }),
    "/123": dir("/123", { dirs: [{ name: "pfx", label: "pfx" }] }),
    "/123/pfx": dir("/123/pfx"),
  });
  const p = pickPath(ctx, list, { title: "T" });
  await flush();
  const box = last().box;
  assert.equal(byText(box, ".."), undefined); // "/" has no parent
  byText(box, "123 - Some Game")!.click();
  await flush();
  assert.equal(calls.at(-1)!.path, "/123");
  assert.ok(byText(box, ".."));
  byText(box, "pfx")!.click();
  await flush();
  assert.equal(calls.at(-1)!.path, "/123/pfx");
  byText(box, "..")!.click();
  await flush();
  assert.equal(calls.at(-1)!.path, "/123");
  byText(box, "Choose this folder")!.click();
  assert.equal(await p, "/123");
});

test("the folder's error is shown as text, never markup", async () => {
  const { ctx, last } = fakeCtx();
  const { list } = fakeList({ [HOME]: dir(HOME, { error: "<b>x</b>" }) });
  void pickPath(ctx, list, { title: "T" });
  await flush();
  const box = last().box;
  assert.match(box.textContent ?? "", /<b>x<\/b>/);
  assert.equal(box.querySelector("b"), null);
  last().close();
});

test("an empty folder says Nothing here.", async () => {
  for (const files of [false, true]) {
    const { ctx, last } = fakeCtx();
    const { list } = fakeList({ [HOME]: dir(HOME) });
    void pickPath(ctx, list, { title: "T", files });
    await flush();
    assert.match(last().box.textContent ?? "", /Nothing here\./);
    last().close();
  }
});

test("a rejected list shows the failure and a way home, nothing unhandled", async () => {
  const unhandled: unknown[] = [];
  const onUnhandled = (e: unknown) => void unhandled.push(e);
  process.on("unhandledRejection", onUnhandled);
  try {
    const { ctx, last } = fakeCtx();
    const calls: Record<string, unknown>[] = [];
    const list = (args: Record<string, unknown>) => {
      calls.push(args);
      return calls.length === 1 ? Promise.reject(new Error("boom")) : Promise.resolve(dir(HOME));
    };
    const p = pickPath(ctx, list, { title: "T", start: "/gone" });
    await flush();
    const box = last().box;
    assert.match(box.textContent ?? "", /Can't read this folder: .*boom/);
    const all = buttons(box);
    assert.ok(all.length >= 1);
    const home = all.find((b) => /home/i.test(b.textContent ?? "")) ?? all[0];
    home.click();
    await flush();
    assert.equal(calls.length, 2);
    assert.ok(!calls[1].path, "goes home: no path");
    byText(box, "Choose this folder")!.click();
    assert.equal(await p, HOME);
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
  assert.deepEqual(unhandled, []);
});

test("closing the window resolves null", async () => {
  const { ctx, last } = fakeCtx();
  const { list } = fakeList({ [HOME]: dir(HOME) });
  const p = pickPath(ctx, list, { title: "T" });
  await flush();
  last().close();
  assert.equal(await p, null);
});

test("it resolves once: a pick, then the close it causes, keeps the pick", async () => {
  const { ctx, last } = fakeCtx();
  const { list } = fakeList({ [HOME]: dir(HOME) });
  const p = pickPath(ctx, list, { title: "T" });
  await flush();
  let results = 0;
  void p.then(() => results++);
  const choose = byText(last().box, "Choose this folder")!;
  choose.click();
  choose.click(); // a second press, already gone
  last().close();
  await flush();
  assert.equal(await p, HOME);
  assert.equal(results, 1);
});

test("opening a second picker closes the first, which resolves null", async () => {
  const { ctx, windows } = fakeCtx();
  const { list } = fakeList({ [HOME]: dir(HOME) });
  const p1 = pickPath(ctx, list, { title: "One" });
  await flush();
  const p2 = pickPath(ctx, list, { title: "Two" });
  await flush();
  assert.equal(await p1, null);
  assert.equal(windows[0].closed, true);
  assert.equal(windows.length, 2);
  assert.equal(windows[1].closed, false);
  byText(windows[1].box, "Choose this folder")!.click();
  assert.equal(await p2, HOME);
});

test("after changing folder the ring goes to the first button", async () => {
  const { ctx, last } = fakeCtx();
  const { list } = fakeList({
    [HOME]: dir(HOME, { dirs: [{ name: "a", label: "a" }, { name: "b", label: "b" }] }),
    [`${HOME}/b`]: dir(`${HOME}/b`, { dirs: [{ name: "c", label: "c" }] }),
  });
  void pickPath(ctx, list, { title: "T" });
  await flush();
  const box = last().box;
  const seen: { detail: unknown; bubbles: boolean }[] = [];
  document.body.addEventListener("invasor:refocus", (e) => void seen.push({ detail: (e as CustomEvent).detail, bubbles: e.bubbles }));
  byText(box, "b")!.click();
  await flush();
  assert.ok(seen.length >= 1, "an invasor:refocus reached the page");
  const ev = seen[seen.length - 1];
  assert.equal(ev.bubbles, true);
  assert.equal(ev.detail, box.querySelector("button"));
  assert.equal((ev.detail as HTMLElement).isConnected, true);
  last().close();
});

test("hidden: true is passed on every list call; omitted it is false", async () => {
  const tree = {
    [HOME]: dir(HOME, { dirs: [{ name: ".config", label: ".config" }] }),
    [`${HOME}/.config`]: dir(`${HOME}/.config`),
  };
  {
    const { ctx, last } = fakeCtx();
    const { list, calls } = fakeList(tree);
    void pickPath(ctx, list, { title: "T", hidden: true });
    await flush();
    byText(last().box, ".config")!.click();
    await flush();
    byText(last().box, "..")!.click();
    await flush();
    assert.equal(calls.length, 3);
    for (const c of calls) assert.equal(c.hidden, true);
    last().close();
  }
  {
    const { ctx, last } = fakeCtx();
    const { list, calls } = fakeList(tree);
    void pickPath(ctx, list, { title: "T", files: true });
    await flush();
    byText(last().box, ".config")!.click();
    await flush();
    assert.equal(calls.length, 2);
    for (const c of calls) assert.equal(c.hidden ?? false, false);
    last().close();
  }
});

test("hidden: true is kept after Go to home", async () => {
  const { ctx, last } = fakeCtx();
  const calls: Record<string, unknown>[] = [];
  const list = (args: Record<string, unknown>) => {
    calls.push(args);
    return calls.length === 1 ? Promise.reject(new Error("boom")) : Promise.resolve(dir(HOME));
  };
  void pickPath(ctx, list, { title: "T", hidden: true, start: "/gone" });
  await flush();
  const all = buttons(last().box);
  (all.find((b) => /home/i.test(b.textContent ?? "")) ?? all[0]).click();
  await flush();
  assert.equal(calls.length, 2);
  assert.equal(calls[0].hidden, true);
  assert.equal(calls[1].hidden, true);
  last().close();
});

test("pickers of different modules don't close each other; of the same module they do", async () => {
  const a = fakeCtx({ id: "a" });
  const b = fakeCtx({ id: "b" });
  const a2 = fakeCtx({ id: "a" });
  const { list } = fakeList({ [HOME]: dir(HOME) });
  const pa = pickPath(a.ctx, list, { title: "A" });
  void pickPath(b.ctx, list, { title: "B" });
  await flush();
  assert.equal(a.last().closed, false);
  assert.equal(b.last().closed, false);
  void pickPath(a2.ctx, list, { title: "A again" });
  await flush();
  assert.equal(await pa, null);
  assert.equal(b.last().closed, false);
  b.last().close();
  a2.last().close();
});
