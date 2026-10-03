// Big windows: almost full screen (16px margin), opened by a module over the panel.
// Same building blocks as the panel: the tab host (L1/R1 tabs, L2/R2 sub-tabs, lazy,
// contained, with onShow/onHide), scrolling content with our own scrollbar, the hint
// bar and the ui kit. While open, a window is the top navigation layer (see
// gamepad-nav.ts): B closes it and the ring goes back to the control that opened it.

import type { ModuleCtx, WindowHandle, WindowSpec } from "../module-api";
import { WINDOW_CLOSE_EVENT, WINDOW_TAB_EVENT, type GamepadNav } from "./gamepad-nav";
import { tabRowHTML } from "./tabbar";
import { createTabHost, type TabSpec } from "./tabhost";

interface WindowDeps {
  stage: HTMLElement;
  nav: GamepadNav;
  ctx: ModuleCtx;
}

export function openBigWindow(spec: WindowSpec, { stage, nav, ctx }: WindowDeps): WindowHandle {
  const opener = stage.querySelector<HTMLElement>(".nav-focus");

  const win = document.createElement("div");
  win.className = "iwin";
  win.innerHTML = `
    <div class="iwin-box">
      <header><span class="iwin-title"></span><button class="close" aria-label="Close">✕</button></header>
      ${tabRowHTML("main", "L1", "R1", true)}
      ${tabRowHTML("sub", "L2", "R2", true)}
      <div class="content-wrap">
        <section class="content"></section>
        <div class="scrollbar"><div class="thumb"></div></div>
      </div>
      <div class="toast" hidden></div>
      <div class="hints"></div>
    </div>`;
  const $ = <T extends Element = HTMLElement>(sel: string) => win.querySelector<T>(sel)!;
  const box = $(".iwin-box");
  const title = $(".iwin-title");
  title.textContent = spec.title;

  // One top-level tab per spec.tabs entry; a plain `render` is a single, tab-less page.
  const hasTabs = !!spec.tabs?.length;
  $(".tabbar.main").hidden = !hasTabs;
  let closed = false;

  const tabs: TabSpec[] = (hasTabs ? spec.tabs! : [{ label: spec.title, render: spec.render }]).map((t, i) => ({
    id: String(i),
    label: t.label,
    name: `${spec.title} › ${t.label}`,
    tabsAlign: "tabsAlign" in t ? t.tabsAlign : undefined,
    render: t.render ? (el) => t.render!(el, ctx) : undefined,
    tabs: "tabs" in t ? t.tabs?.map((s) => ({ ...s, render: (el: HTMLElement) => s.render(el, ctx), onShow: () => s.onShow?.(ctx) })) : undefined,
    onShow: "onShow" in t ? () => t.onShow?.(ctx) : undefined,
    onHide: "onHide" in t ? () => t.onHide?.() : undefined,
  }));

  const host = createTabHost({
    content: $<HTMLElement>(".content"),
    mainTabs: hasTabs ? $<HTMLElement>(".tabbar.main .tabs") : null,
    subBar: $<HTMLElement>(".tabbar.sub"),
    subTabs: $<HTMLElement>(".tabbar.sub .tabs"),
    nav,
    visible: () => !closed,
    onBars: () => {
      win.dataset.extraHint = !hasTabs ? "" : host.hasSub() ? "L1/R1 tab · L2/R2 section" : "L1/R1 tab";
      nav.refreshHints();
    },
  });

  function close() {
    if (closed) return;
    box.dispatchEvent(new CustomEvent("invasor:dismiss")); // cancels a ui.confirm open in here
    host.clear(); // onHide of what was on screen
    closed = true;
    win.remove();
    try {
      spec.onClose?.();
    } catch (e) {
      console.error("[invasor] window onClose failed", e);
    }
    // Back to the control that opened the window, or the first one if it's gone.
    if (opener?.isConnected) stage.dispatchEvent(new CustomEvent("invasor:refocus", { detail: opener }));
    nav.ensureFocus();
  }

  win.addEventListener(WINDOW_TAB_EVENT, (e) => {
    const { level, dir } = (e as CustomEvent<{ level: 1 | 2; dir: -1 | 1 }>).detail;
    if (level === 1) host.step(dir);
    else host.stepSub(dir);
  });
  win.addEventListener(WINDOW_CLOSE_EVENT, close);
  $(".close").addEventListener("click", close);

  stage.appendChild(win);
  void host.set(tabs);

  return {
    close,
    setTitle: (t: string) => void (title.textContent = t),
  };
}
