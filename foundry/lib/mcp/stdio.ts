import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { extractJsonRpcFrames, jsonRpcRequest, mcpResultToJson, type McpCallResult, type McpTool } from "@/lib/mcp/jsonrpc";
import { redactSecrets } from "@/lib/utils";

export type StdioConfig = {
  command: string;
  args: string[];
  cwd?: string;
  bridgeUrl?: string;
  bridgeToken?: string;
  readOnly?: boolean;
  timeoutMs?: number;
  allowlist?: string[];
};

const DEFAULT_ALLOWLIST = ["python3", "python", "node", "npx", "bun", "deno"];

export function validateStdioConfig(config: StdioConfig): StdioConfig {
  const rawCommand = String(config.command ?? "").trim();
  const commandName = path.basename(rawCommand).toLowerCase();
  const allowlist = (config.allowlist ?? (process.env.FOUNDRY_STDIO_COMMAND_ALLOWLIST ?? DEFAULT_ALLOWLIST.join(",")).split(","))
    .map((v) => v.trim().toLowerCase()).filter(Boolean);
  if (!rawCommand || !allowlist.includes(commandName)) throw new Error(`Stdio command is not allowlisted: ${rawCommand || "(empty)"}`);
  if (!config.bridgeUrl) throw new Error("BRIDGE_URL is required for the Tunneling stdio connector");
  const args = (config.args ?? []).map((arg) => String(arg));
  for (const arg of args) {
    if (arg.includes("\0") || /[;&|`]|\$\(/.test(arg)) throw new Error("Unsafe stdio argument rejected");
  }
  if (config.cwd) {
    const normalized = path.normalize(config.cwd);
    const parts = normalized.split(path.sep).filter(Boolean);
    const parentCount = parts.filter((part) => part === "..").length;
    if (
      config.cwd.includes("\0") || normalized === ".." ||
      parentCount > 1 || (parentCount === 1 && parts[0] !== "..")
    ) throw new Error("Unsafe stdio working directory rejected");
  }
  return { ...config, command: rawCommand, args };
}

export class McpStdioClient {
  private readonly config: StdioConfig;
  private process?: ChildProcess;
  private buffer: Buffer<ArrayBufferLike> = Buffer.alloc(0);
  private pending = new Map<string | number, { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: NodeJS.Timeout }>();
  private nextId = 1;
  private stderr = "";
  private started = false;
  initResult: Record<string, unknown> = {};
  serverInfo: Record<string, unknown> = {};

  constructor(config: StdioConfig) {
    this.config = validateStdioConfig(config);
  }

  async start(): Promise<Record<string, unknown>> {
    if (this.started) return this.serverInfo;
    const env: NodeJS.ProcessEnv = {
      NODE_ENV: process.env.NODE_ENV ?? "production",
      PATH: process.env.PATH ?? "/usr/local/bin:/usr/bin:/bin",
      HOME: process.env.HOME ?? "/tmp",
      PYTHONUNBUFFERED: "1",
      BRIDGE_URL: this.config.bridgeUrl!,
      BRIDGE_TOKEN: this.config.bridgeToken ?? "",
      BRIDGE_READ_ONLY: this.config.readOnly ? "1" : "0"
    };
    const child = spawn(this.config.command, this.config.args, {
      cwd: this.config.cwd ? path.resolve(this.config.cwd) : undefined,
      env,
      shell: false,
      stdio: ["pipe", "pipe", "pipe"]
    }) as ChildProcess;
    this.process = child;
    child.stdout?.on("data", (chunk: Buffer) => this.onStdout(chunk));
    child.stderr?.on("data", (chunk: Buffer) => { this.stderr = (this.stderr + chunk.toString("utf8")).slice(-4000); });
    child.on("exit", (code: number | null, signal: NodeJS.Signals | null) => {
      const error = new Error(`MCP stdio process exited (code ${code ?? "null"}, signal ${signal ?? "none"}): ${redactSecrets(this.stderr).slice(0, 500)}`);
      for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(error); }
      this.pending.clear();
    });
    this.started = true;
    const result = await this.request<Record<string, unknown>>("initialize", {
      protocolVersion: "2026-07-28",
      capabilities: { roots: { listChanged: false }, sampling: {}, elicitation: {} },
      clientInfo: { name: "foundry", version: "0.1.0" }
    });
    this.serverInfo = (result.serverInfo as Record<string, unknown>) ?? {};
    this.initResult = result;
    return result;
  }

  async initialize(): Promise<Record<string, unknown>> {
    if (this.started) return this.initResult;
    return this.start();
  }

  private write(message: Record<string, unknown>) {
    if (!this.process?.stdin?.writable) throw new Error("MCP stdio process is not writable");
    this.process.stdin.write(`${JSON.stringify(message)}\n`);
  }

  private onStdout(chunk: Buffer) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    const { messages, remainder } = extractJsonRpcFrames(this.buffer);
    this.buffer = remainder;
    for (const message of messages) {
      const id = message.id as string | number | null | undefined;
      if (id == null) continue;
      const request = this.pending.get(id);
      if (!request) continue;
      this.pending.delete(id);
      clearTimeout(request.timer);
      const error = message.error as { code?: number; message?: string } | undefined;
      if (error) request.reject(new Error(`MCP error ${error.code ?? "unknown"}: ${error.message ?? "unknown"}`));
      else request.resolve(message.result);
    }
  }

  async request<T = unknown>(method: string, params?: unknown): Promise<T> {
    if (!this.started) await this.start();
    const id = this.nextId++;
    const timeoutMs = this.config.timeoutMs ?? 30_000;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`MCP stdio request timed out after ${timeoutMs}ms: ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve: (value) => resolve(value as T), reject, timer });
      try { this.write(jsonRpcRequest(method, params, id)); }
      catch (error) { clearTimeout(timer); this.pending.delete(id); reject(error instanceof Error ? error : new Error(String(error))); }
    });
  }

  async listTools(): Promise<McpTool[]> {
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
    const result = await this.request<McpCallResult>("tools/call", { name, arguments: args, ...extras });
    const requiresInput = result.resultType === "input_required" || Boolean(result.inputRequests);
    if (result.isError && !requiresInput) throw new Error(`MCP tool ${name} reported an error: ${String(mcpResultToJson(result)).slice(0, 500)}`);
    return mcpResultToJson(result);
  }

  async close(): Promise<void> {
    if (!this.process || this.process.killed) return;
    this.process.stdin?.end();
    this.process.kill("SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 80));
    if (!this.process.killed) this.process.kill("SIGKILL");
  }
}
