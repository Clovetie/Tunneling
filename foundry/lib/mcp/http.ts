import { jsonRpcRequest, mcpResultToJson, parseMcpResponse, type JsonRpcResponse, type McpCallResult, type McpTool } from "@/lib/mcp/jsonrpc";
import { redactSecrets } from "@/lib/utils";

export type McpHttpOptions = {
  endpoint: string;
  token?: string;
  protocolVersion?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
};

function assertEndpoint(endpoint: string): URL {
  const url = new URL(endpoint);
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("MCP endpoint must use http or https");
  return url;
}

export class McpHttpClient {
  readonly endpoint: string;
  readonly protocolVersion: string;
  private readonly token?: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private sessionId?: string;
  private initialized = false;
  private nextId = 1;
  serverInfo: Record<string, unknown> = {};
  initResult: Record<string, unknown> = {};
  negotiatedVersion?: string;

  constructor(options: McpHttpOptions) {
    assertEndpoint(options.endpoint);
    this.endpoint = options.endpoint.replace(/\/$/, "");
    this.token = options.token?.trim() || undefined;
    this.protocolVersion = options.protocolVersion || "2026-07-28";
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async request<T = unknown>(method: string, params?: unknown): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "MCP-Protocol-Version": this.protocolVersion
    };
    if (this.token) {
      headers.Authorization = `Bearer ${this.token}`;
      headers["X-Arena-Token"] = this.token;
    }
    if (this.sessionId) headers["Mcp-Session-Id"] = this.sessionId;
    const response = await this.fetchImpl(this.endpoint, {
      method: "POST",
      headers,
      body: JSON.stringify(jsonRpcRequest(method, params, this.nextId++)),
      signal: AbortSignal.timeout(this.timeoutMs),
      cache: "no-store"
    });
    const session = response.headers.get("mcp-session-id") ?? response.headers.get("Mcp-Session-Id");
    if (session) this.sessionId = session;
    const body = await response.text();
    if (!response.ok) {
      const detail = redactSecrets(body.slice(0, 600));
      throw new Error(`MCP HTTP ${response.status}: ${detail}`);
    }
    const parsed = parseMcpResponse<T>(body, response.headers.get("content-type") ?? "application/json") as JsonRpcResponse<T>;
    return parsed.result as T;
  }

  async initialize(): Promise<Record<string, unknown>> {
    if (this.initialized) return this.initResult;
    const result = await this.request<Record<string, unknown>>("initialize", {
      protocolVersion: this.protocolVersion,
      capabilities: { roots: { listChanged: false }, sampling: {}, elicitation: {} },
      clientInfo: { name: "foundry", version: "0.1.0" }
    });
    this.serverInfo = (result.serverInfo as Record<string, unknown>) ?? {};
    this.initResult = result;
    this.negotiatedVersion = result.protocolVersion as string | undefined;
    this.initialized = true;
    return result;
  }

  async serverDiscover(): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>("server/discover");
  }

  async listTools(): Promise<McpTool[]> {
    await this.initialize();
    const tools: McpTool[] = [];
    let cursor: string | undefined;
    for (let page = 0; page < 20; page++) {
      const result = await this.request<{ tools: McpTool[]; nextCursor?: string }>("tools/list", cursor ? { cursor } : {});
      tools.push(...(result.tools ?? []));
      cursor = result.nextCursor;
      if (!cursor) break;
    }
    return tools;
  }

  async callTool(name: string, args: Record<string, unknown>, extras: Record<string, unknown> = {}): Promise<unknown> {
    await this.initialize();
    const result = await this.request<McpCallResult>("tools/call", { name, arguments: args, ...extras });
    const requiresInput = result.resultType === "input_required" || Boolean(result.inputRequests);
    if (result.isError && !requiresInput) throw new Error(`MCP tool ${name} reported an error: ${String(mcpResultToJson(result)).slice(0, 500)}`);
    return mcpResultToJson(result);
  }
}
