// Tab rows shared by the panel and big windows.
import { keepVisible } from "./scroll";

const SUB_ALIGNS = ["start", "center", "end", "justify"];

/** Fill a tab row with `labels`, mark `on` as active, keep it scrolled into view. */
export function renderBar(el: HTMLElement, labels: string[], on: number, pick: (i: number) => void) {
  el.innerHTML = "";
  labels.forEach((label, i) => {
    const b = document.createElement("span");
    b.className = "tab" + (i === on ? " on" : "");
    b.textContent = label;
    b.addEventListener("click", () => pick(i));
    el.appendChild(b);
  });
  const activeTab = el.querySelector<HTMLElement>(".tab.on");
  if (activeTab) keepVisible(el, activeTab, "x");
}

/** data-align for a sub-tab row: one of SUB_ALIGNS, anything else falls back to "start". */
export function alignOf(value: string | undefined): string {
  return value && SUB_ALIGNS.includes(value) ? value : "start";
}

/** Markup of a tab row with its shoulder-button keys on both sides. */
export function tabRowHTML(kind: "main" | "sub", left: string, right: string, hidden = false): string {
  return `<nav class="tabbar ${kind}"${hidden ? " hidden" : ""}><span class="tab-key">${left}</span><div class="tabs"></div><span class="tab-key">${right}</span></nav>`;
}
