// A panel that covers the Invasor panel (Tasks, Settings): the same place and width, and always the full
// height of the window (the Invasor panel is as tall as its tab, which can be little), opened from
// the panel by a button of the pad (Select, Start) or of its header, and while it is open it is the
// layer the pad drives (see gamepad-nav.ts: B or its own button close it and the pad goes back
// to the Invasor panel, with the ring where it was). Big windows and dialogs still open above it.

import { WINDOW_CLOSE_EVENT, type GamepadNav } from "./gamepad-nav";

export interface CoverPanel {
  /** Cover the Invasor panel and take the pad. */
  open(): void;
  /** Give the pad back to the Invasor panel (`restoreFocus: false` when that panel is closing too). */
  close(restoreFocus?: boolean): void;
  toggle(): void;
  isOpen(): boolean;
  /** Where its content goes. */
  readonly content: HTMLElement;
  destroy(): void;
}

export interface CoverDeps {
  /** What the pad navigates: the Invasor panel, big windows and this panel live in it. */
  stage: HTMLElement;
  /** The Invasor panel this one covers. */
  panel: HTMLElement;
  nav: GamepadNav;
  /** Which one it is (`data-kind`); also what the pad's hint says is the way back. */
  kind: string;
  title: string;
  /** What the pad hint says closes it besides B, e.g. "Select back". */
  backHint: string;
  /**
   * Just opened and on screen. It may build or draw the content (and return a promise): the
   * pad's ring is put on the first control once that is done.
   */
  onOpen?(content: HTMLElement): void | Promise<void>;
}

export function createCoverPanel({ stage, panel, nav, kind, title, backHint, onOpen }: CoverDeps): CoverPanel {
  const el = document.createElement("div");
  el.className = "cover-panel";
  el.dataset.kind = kind;
  el.hidden = true;
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-label", title);
  // The pad's hint bar adds this while the panel is the top layer (see showHints in gamepad-nav.ts).
  el.dataset.extraHint = backHint;
  el.innerHTML = `
    <header><span class="cover-title"></span><button class="close" aria-label="Close"></button></header>
    <div class="content-wrap">
      <section class="content"></section>
      <div class="scrollbar"><div class="thumb"></div></div>
    </div>
    <div class="toast" hidden></div>
    <div class="hints"></div>`;
  el.querySelector(".cover-title")!.textContent = title;
  const closeBtn = el.querySelector<HTMLElement>(".close")!;
  closeBtn.textContent = "✕";
  closeBtn.setAttribute("aria-label", `Close ${title}`);
  const content = el.querySelector<HTMLElement>(".content")!;
  stage.appendChild(el);

  const isOpen = () => !el.hidden;
  let opener: HTMLElement | null = null; // where the pad's ring was in the panel, to put it back
  let opening = 0; // bumped on every open/close: an onOpen that ends after the panel closed does nothing

  function open() {
    if (isOpen()) return;
    opener = panel.querySelector<HTMLElement>(".nav-focus");
    panel.classList.add("covered");
    el.hidden = false;
    const mine = ++opening;
    nav.reset();
    const built = onOpen?.(content);
    if (built && typeof (built as Promise<void>).then === "function") {
      (built as Promise<void>)
        .catch((e) => console.error(`[invasor] the ${title} panel failed`, e))
        .then(() => {
          if (opening === mine && isOpen()) nav.ensureFocus(); // its controls exist now
        });
    }
  }

  function close(restoreFocus = true) {
    if (!isOpen()) return;
    opening++;
    el.hidden = true;
    panel.classList.remove("covered");
    if (!restoreFocus) return;
    if (opener?.isConnected) stage.dispatchEvent(new CustomEvent("invasor:refocus", { detail: opener }));
    nav.ensureFocus();
  }

  el.addEventListener(WINDOW_CLOSE_EVENT, () => close()); // B
  closeBtn.addEventListener("click", () => close());

  return {
    open,
    close,
    toggle: () => (isOpen() ? close() : open()),
    isOpen,
    content,
    destroy() {
      panel.classList.remove("covered");
      el.remove();
    },
  };
}

/** The layer a dialog (ui.confirm) draws in: the last big window, else the covering panel, else the panel. */
export function topLayerOf(stage: HTMLElement, panel: HTMLElement): HTMLElement {
  const boxes = stage.querySelectorAll<HTMLElement>(".iwin .iwin-box");
  if (boxes.length) return boxes[boxes.length - 1];
  return stage.querySelector<HTMLElement>(".cover-panel:not([hidden])") ?? panel;
}

/** Where a toast shows: the last big window's, else the covering panel's, else the panel's (`panelToast`). */
export function toastHostOf(stage: HTMLElement, panelToast: HTMLElement): HTMLElement {
  const wins = stage.querySelectorAll<HTMLElement>(".iwin .toast");
  if (wins.length) return wins[wins.length - 1];
  return stage.querySelector<HTMLElement>(".cover-panel:not([hidden]) .toast") ?? panelToast;
}
