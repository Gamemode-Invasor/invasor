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
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
      return body.result as T;
    },
  };
}

export type Api = ReturnType<typeof makeApi>;
