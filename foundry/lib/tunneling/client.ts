import { assertSafePathSegment, redactSecrets } from "@/lib/utils";

export type TunnelingClientOptions = {
  baseUrl: string;
  token?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
};

export class TunnelingClient {
  readonly baseUrl: string;
  private readonly token?: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: TunnelingClientOptions) {
    const url = new URL(options.baseUrl);
    if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("Tunneling base URL must use http or https");
    this.baseUrl = url.toString().replace(/\/$/, "");
    this.token = options.token?.trim() || undefined;
    this.timeoutMs = options.timeoutMs ?? 20_000;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async request(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    if (this.token) {
      headers.set("Authorization", `Bearer ${this.token}`);
      headers.set("X-Arena-Token", this.token);
    }
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await this.fetchImpl(`${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`, {
      ...init, headers, signal: AbortSignal.timeout(this.timeoutMs), cache: "no-store"
    });
    if (!response.ok) {
      const body = (await response.text().catch(() => "")).slice(0, 800);
      throw new Error(`Tunneling HTTP ${response.status} ${path}: ${redactSecrets(body)}`);
    }
    return response;
  }

  async json<T = Record<string, unknown>>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.request(path, init);
    return await response.json() as T;
  }

  async get<T = Record<string, unknown>>(path: string, query?: Record<string, string | number | boolean | undefined>): Promise<T> {
    const suffix = query ? `?${new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}` : "";
    return this.json<T>(`${path}${suffix}`);
  }

  async post<T = Record<string, unknown>>(path: string, body: unknown): Promise<T> {
    return this.json<T>(path, { method: "POST", body: JSON.stringify(body) });
  }

  health() { return this.get("/api/health"); }
  state() { return this.get("/api/state"); }
  peers() { return this.get("/api/peers"); }
  studios() { return this.get("/api/studios"); }
  ops() { return this.get("/api/ops"); }
  tunnel() { return this.get("/api/tunnel"); }
  confirmations() { return this.get("/api/confirmations"); }
  bus(action: string, extra: Record<string, string | number | boolean> = {}) { return this.get("/api/bus", { action, ...extra }); }
  orchestrator(action: string, extra: Record<string, string | number | boolean> = {}) { return this.get("/api/orchestrator", { action, ...extra }); }
  events(query: Record<string, string | number | boolean> = {}) { return this.get("/api/events", query); }
  audit(query: Record<string, string | number | boolean> = {}) { return this.get("/api/audit", query); }

  async evidence(filename: string): Promise<{ bytes: ArrayBuffer; contentType: string }> {
    const safe = assertSafePathSegment(filename);
    const response = await this.request(`/api/orchestrator/evidence/${safe}`);
    return { bytes: await response.arrayBuffer(), contentType: response.headers.get("content-type") ?? "application/octet-stream" };
  }
}

export function summarizeTunnelingHealth(health: Record<string, unknown>) {
  const studio = (health.studio as Record<string, unknown> | undefined) ?? {};
  const tunnel = (health.tunnel as Record<string, unknown> | undefined) ?? {};
  return {
    ok: health.ok !== false,
    version: typeof health.version === "string" ? health.version : null,
    studioConnected: health.studio_connected === true || studio.online === true,
    mcpTools: Number(health.mcp_tools ?? 0),
    peerCount: Array.isArray(health.peers) ? health.peers.length : Number(health.peers ?? 0),
    queued: Number(health.queued ?? 0),
    confirmGate: (health.confirmGate as Record<string, unknown> | undefined) ?? {},
    tunnelState: typeof tunnel.state === "string" ? tunnel.state : "unknown",
    publicUrl: typeof tunnel.publicUrl === "string" ? tunnel.publicUrl : null,
    authMode: typeof health.authMode === "string" ? health.authMode : null
  };
}
