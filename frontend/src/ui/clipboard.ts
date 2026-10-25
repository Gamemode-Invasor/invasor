// ctx.copy: text to the clipboard, with the old execCommand way when the Clipboard API fails.

const MAX = 1 << 20;

/** True if `text` got to the clipboard. Never throws or rejects. */
export async function copyText(text: string, doc: Document = document): Promise<boolean> {
  if (typeof text !== "string" || text.length > MAX) return false;
  try {
    if (!navigator.clipboard) throw new Error("no Clipboard API");
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return execCopy(text, doc);
  }
}

function execCopy(text: string, doc: Document): boolean {
  // Selecting the textarea takes browser focus from Steam's selected element; give it
  // back or the next D-pad press leaks (gamepad-nav.ts).
  // STEAM TOUCHPOINT: what has browser focus is Steam's ".gpfocus" element. If that
  // changes, nothing breaks: focus is just handed back to whatever had it.
  const area = doc.createElement("textarea");
  area.value = text;
  area.readOnly = true;
  area.style.cssText = "position: fixed; left: 0; top: 0; opacity: 0; pointer-events: none";
  const had = doc.activeElement as HTMLElement | null;
  try {
    doc.body.append(area);
    area.select();
    return doc.execCommand("copy") === true;
  } catch {
    return false;
  } finally {
    area.remove();
    had?.focus?.({ preventScroll: true });
  }
}
