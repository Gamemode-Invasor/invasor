/** Escape text for innerHTML. */
export function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** Run a module's hook: a throw, or a rejected promise from an async hook, is logged, never unhandled. */
export function safe(what: string, fn: () => unknown) {
  const fail = (e: unknown) => console.error(`[invasor] ${what} failed`, e);
  try {
    const r = fn();
    if (r instanceof Promise) r.catch(fail);
  } catch (e) {
    fail(e);
  }
}

/** Why a big window can't open where there isn't room (Quick Access during a game). */
export const NO_ROOM = "The expanded view is only available from the Library (Steam button → Library)";
