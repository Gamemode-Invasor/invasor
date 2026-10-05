// Which modules a module's own showInQam() hides from the Quick Access panel.
// Only an explicit `false` hides: an error, a slow answer or any other value shows the
// module, so a broken hook never takes a tab away.

export interface QamEntry {
  id: string;
  /** The module's showInQam, already bound to its ctx. Missing: always shown. */
  fn?: () => unknown;
}

export async function hiddenInQam(entries: QamEntry[], timeoutMs = 1500): Promise<Set<string>> {
  const hidden = new Set<string>();
  await Promise.all(
    entries.map(async ({ id, fn }) => {
      if (!fn) return;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const slow = new Promise<true>((resolve) => (timer = setTimeout(() => resolve(true), timeoutMs)));
        if ((await Promise.race([Promise.resolve(fn()), slow])) === false) hidden.add(id);
      } catch (e) {
        console.error(`[invasor] module ${id}: showInQam failed, showing it`, e);
      } finally {
        clearTimeout(timer);
      }
    }),
  );
  return hidden;
}
