// The Settings panel: opened with Start from the Invasor panel (or its ⚙ button), it covers that
// panel (see cover-panel.ts). Its content is built the first time it is opened and then kept,
// like the tab it used to be.

import { createCoverPanel, type CoverPanel } from "./cover-panel";
import type { GamepadNav } from "./gamepad-nav";

interface SettingsDeps {
  stage: HTMLElement;
  panel: HTMLElement;
  nav: GamepadNav;
  /** Draws the settings into `el` (ui/settings.ts renderSettings, with its dependencies). */
  render(el: HTMLElement): Promise<void>;
  /** The panel was just opened. */
  onOpen?(): void;
  /** The panel was just closed. */
  onClose?(): void;
}

export function createSettingsPanel({ stage, panel, nav, render, onOpen, onClose }: SettingsDeps): CoverPanel {
  let built = false;
  return createCoverPanel({
    stage,
    panel,
    nav,
    kind: "settings",
    title: "Settings",
    backHint: "Start back",
    onClose,
    async onOpen(content) {
      onOpen?.();
      if (built) return;
      built = true;
      try {
        await render(content);
      } catch (e) {
        console.error("[invasor] Settings failed", e);
        built = false; // the next opening tries again
        const msg = document.createElement("p");
        msg.className = "mod-error";
        msg.textContent = `Error in Settings: ${String((e as Error)?.message ?? e)}`;
        content.replaceChildren(msg);
      }
    },
  });
}
