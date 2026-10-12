export interface InvasorCfg {
  apiPort: number;
  token: string;
  /** Which Steam window we were injected into (see backend config "targets"). */
  role: "main" | "quickaccess" | string;
  /** Invasor's version (the backend's). */
  version?: string;
}

export function makeApi(cfg: InvasorCfg) {
  const base = `http://127.0.0.1:${cfg.apiPort}`;
  return {
    /** Calls a backend feature method: POST /api/<feature>/<method>. */
    async call<T = unknown>(feature: string, method: string, args: Record<string, unknown> = {}): Promise<T> {
      const res = await fetch(`${base}/api/${feature}/${method}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Invasor-Token": cfg.token },
        body: JSON.stringify(args),
      });
      let body: { error?: string; result?: unknown };
      try {
        body = await res.json();
      } catch {
        throw new Error(`HTTP ${res.status}`); // not JSON: a proxy or an error page, not our backend
      }
      if (!res.ok) throw new Error(body?.error ?? `HTTP ${res.status}`);
      if (!body || !("result" in body)) throw new Error("the backend's answer has no result");
      return body.result as T;
    },
  };
}

export type Api = ReturnType<typeof makeApi>;
