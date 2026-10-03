// Pure value helpers shared by the controls (tested in frontend/test/values.test.ts).
// Same rules as the backend's schema.snap, so what a control shows is what gets stored.

/** Decimal places of a number as written (0.1 -> 1, 0.25 -> 2, 5 -> 0, 1e-7 -> 7). */
export function decimals(x: number): number {
  if (!Number.isFinite(x) || Number.isInteger(x)) return 0;
  const s = String(x);
  const exp = s.match(/e-(\d+)$/);
  if (exp) return Number(exp[1]) + (s.split("e")[0].split(".")[1]?.length ?? 0);
  return s.split(".")[1]?.length ?? 0;
}

/**
 * Clamp to [min, max] and round to the nearest step counted from min, without float
 * noise: snap(0.1 + 0.2, 0, 1, 0.1) === 0.3. If max isn't on a step, the last step
 * below it is the top.
 */
export function snap(value: number, min: number, max: number, step: number): number {
  if (!Number.isFinite(value)) value = min;
  if (!(step > 0)) step = 1;
  const last = Math.floor((max - min) / step + 1e-9);
  const n = Math.min(last, Math.max(0, Math.round((value - min) / step)));
  const places = Math.max(decimals(step), decimals(min));
  return Number((min + n * step).toFixed(places));
}

/** Is this URL a video (by the extension of its path)? Used for animated thumbnails. */
export function isVideo(src: string | null | undefined): boolean {
  if (!src) return false;
  const path = src.split(/[?#]/, 1)[0].toLowerCase();
  return /\.(webm|mp4|m4v)$/.test(path);
}

/** Index of `value` among `options` (strict equality), or -1. */
export function optionIndex<T>(options: { value: T }[], value: T): number {
  return options.findIndex((o) => o.value === value);
}

/**
 * A `when` / `disabled_when` from module.json: true while every key has its value, or
 * one of its values when it's a list. No condition = always true.
 */
export function whenMet(when: Record<string, unknown> | undefined, value: (key: string) => unknown): boolean {
  if (!when) return true;
  return Object.entries(when).every(([k, v]) => (Array.isArray(v) ? v.includes(value(k)) : value(k) === v));
}
