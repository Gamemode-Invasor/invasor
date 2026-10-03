// Which game is highlighted in Steam's library (the tile under the cursor, before
// opening it). Steam publishes this nowhere stable, so it's read from the page:
//
// STEAM TOUCHPOINT (see README "What Invasor relies on in Steam"):
// - the highlighted tile is the page's focused element (document.activeElement, plain
//   DOM; Invasor never takes focus), a role="link" element;
// - its artwork URL carries the appid: /assets/<appid>/… (Steam games) or
//   /customimages/<appid>p… (custom art, also non-Steam shortcuts);
// - its accessible name (aria-labelledby) is the game's name: the backend matches it
//   against shortcuts.vdf for shortcuts without custom art.
// If Valve changes any of it, the result is just null: "running" and "selected" don't
// depend on this.

/** What a tile shows; the backend turns it into a game. */
export interface TileInfo {
  appid?: string;
  name?: string;
}

const ART_APPID = /\/(?:assets|customimages)\/(\d{1,20})(?=[_p./?]|$)/;
const MAX_NAME = 256;

/** The appid in an artwork URL, if any. Pure: tested in test/library.test.ts. */
export function appidFromArt(src: string | null | undefined): string | undefined {
  return src ? ART_APPID.exec(src)?.[1] : undefined;
}

/** A library tile's appid/name, or null if `el` isn't a game tile. Never throws. */
function tileInfo(el: Element | null): TileInfo | null {
  try {
    if (!el || el.getAttribute("role") !== "link") return null;
    const appid = appidFromArt(el.querySelector("img")?.getAttribute("src"));
    const ids = (el.getAttribute("aria-labelledby") ?? "").split(/\s+/).filter(Boolean);
    const name = ids
      .map((id) => el.ownerDocument.getElementById(id)?.textContent?.trim())
      .find((t) => !!t)
      ?.slice(0, MAX_NAME);
    if (!appid && !name) return null;
    const info: TileInfo = {};
    if (appid) info.appid = appid;
    if (name) info.name = name;
    return info;
  } catch {
    return null;
  }
}

/**
 * Follows Steam's focus in this window and remembers the last game tile it was on.
 * Remembered (not just read when asked) because opening the panel by touch/F10 moves
 * the browser's focus onto the panel itself.
 */
export function trackHighlight(ownHost: Element): { get(): TileInfo | null; destroy(): void } {
  let last: TileInfo | null = null;
  const update = (target: EventTarget | null) => {
    if (!(target instanceof Element) || target === ownHost || ownHost.contains(target)) return;
    last = tileInfo(target); // a non-game element (menu, tab…) clears it
  };
  const onFocus = (e: FocusEvent) => update(e.target);
  document.addEventListener("focusin", onFocus, true);
  update(document.activeElement);
  return {
    get() {
      update(document.activeElement);
      return last;
    },
    destroy: () => document.removeEventListener("focusin", onFocus, true),
  };
}
