// STEAM TOUCHPOINT: every access to Steam internals goes through here. If Valve moves or removes an
// API, callers get `undefined` / a rejected promise instead of an exception that
// would take the whole overlay down.

declare global {
  interface Window {
    SteamClient?: any;
  }
}

function steamClient(): any {
  return window.SteamClient ?? (window.opener as Window | null)?.SteamClient;
}

function resolve(path: string): { fn: any; self: any } | undefined {
  let self: any = undefined;
  let cur: any = steamClient();
  for (const key of path.split(".")) {
    if (cur == null) return undefined;
    self = cur;
    cur = cur[key];
  }
  return cur === undefined ? undefined : { fn: cur, self };
}

/** True if Steam's JS API is reachable from this window at all. */
export function steamAvailable(): boolean {
  return typeof steamClient() === "object";
}

/** True if `SteamClient.<path>` exists and is callable. */
export function hasApi(path: string): boolean {
  return typeof resolve(path)?.fn === "function";
}

/** Calls `SteamClient.<path>(...args)`; rejects (never throws) if missing or failing. */
export async function safeCall<T = unknown>(path: string, ...args: unknown[]): Promise<T> {
  const r = resolve(path);
  if (typeof r?.fn !== "function") throw new Error(`SteamClient.${path} not available`);
  return await r.fn.apply(r.self, args);
}
