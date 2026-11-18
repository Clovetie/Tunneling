import { describe, expect, it } from "vitest";
import { extractJsonRpcFrames, parseMcpResponse, parseSsePayload } from "@/lib/mcp/jsonrpc";
import { McpHttpClient } from "@/lib/mcp/http";
import { validateStdioConfig } from "@/lib/mcp/stdio";
import { DEFAULT_WORKFLOW_GRAPH } from "@/lib/templates";

describe("MCP framing and integration", () => {
  it("parses SSE and Content-Length/NDJSON frames", () => {
    const events = parseSsePayload('event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n');
    expect(events).toHaveLength(1);
    const ndjson = Buffer.from('{"jsonrpc":"2.0","id":1,"result":{}}\n{"jsonrpc":"2.0","id":2,"result":{}}\npartial');
    const parsed = extractJsonRpcFrames(ndjson);
    expect(parsed.messages).toHaveLength(2);
    expect(parsed.remainder.toString()).toBe("partial");
    const body = '{"json":"2.0"}';
    const framed = extractJsonRpcFrames(Buffer.from(`Content-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`));
    expect((framed.messages[0] as Record<string, unknown>).json).toBe("2.0");
    expect(parseMcpResponse('data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n', "text/event-stream").result).toEqual({ tools: [] });
  });
  it("uses a real JSON-RPC handshake and runtime tools/list against an injected MCP transport", async () => {
    const calls: Array<Record<string, unknown>> = [];
    const fetchImpl = (async (_url: string | URL | Request, init?: RequestInit) => {
      const payload = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      calls.push(payload);
      const result = payload.method === "initialize" ? { protocolVersion: "2026-07-28", serverInfo: { name: "bridge", version: "6.0.0" } } : payload.method === "tools/list" ? { tools: [{ name: "get_studio_state", inputSchema: { type: "object" } }, { name: "agent_team", inputSchema: { type: "object" } }] } : { content: [{ type: "text", text: "{\"ok\":true}" }] };
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: payload.id, result }), { status: 200, headers: { "content-type": "application/json", "mcp-session-id": "session-1" } });
    }) as typeof fetch;
    const client = new McpHttpClient({ endpoint: "http://bridge.example/mcp", token: "secret-token", fetchImpl });
    const init = await client.initialize();
    const tools = await client.listTools();
    const output = await client.callTool("get_studio_state", {});
    expect(init.protocolVersion).toBe("2026-07-28");
    expect(tools.map((tool) => tool.name)).toContain("agent_team");
    expect(output).toEqual({ ok: true });
    expect(calls.map((call) => call.method)).toEqual(["initialize", "tools/list", "tools/call"]);
  });
  it("preserves signed ConfirmGate protocol extras on a retry", async () => {
    const calls: Array<Record<string, unknown>> = [];
    const fetchImpl = (async (_url: string | URL | Request, init?: RequestInit) => {
      const payload = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      if (payload.method !== "initialize") calls.push(payload);
      const params = (payload.params ?? {}) as Record<string, unknown>;
      const result = payload.method === "initialize"
        ? { protocolVersion: "2026-07-28", serverInfo: { name: "bridge", version: "6.0.0" } }
        : params.requestState
          ? { content: [{ type: "text", text: JSON.stringify({ ok: true, approved: true }) }] }
          : {
              isError: true,
              resultType: "input_required",
              inputRequests: { confirm: { mode: "write" } },
              requestState: "signed-state",
              content: [{ type: "text", text: "Human approval is required." }],
              structuredContent: { ok: false, reason: "confirmation_required", requestId: "cfm-1", tool: "start_project" }
            };
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: payload.id, result }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    const client = new McpHttpClient({ endpoint: "http://bridge.example/mcp", fetchImpl });
    const gated = await client.callTool("start_project", { action: "start_project" }) as Record<string, unknown>;
    expect(gated.resultType).toBe("input_required");
    expect(gated.requestState).toBe("signed-state");
    const retry = await client.callTool("start_project", { action: "start_project" }, {
      requestState: "signed-state",
      inputResponses: { confirm: { approved: true } }
    }) as Record<string, unknown>;
    expect(retry.approved).toBe(true);
    expect(calls).toHaveLength(2);
    const retryParams = calls[1].params as Record<string, unknown>;
    expect(retryParams.requestState).toBe("signed-state");
    expect(retryParams.inputResponses).toEqual({ confirm: { approved: true } });
    expect((retryParams.arguments as Record<string, unknown>).action).toBe("start_project");
  });
  it("rejects arbitrary stdio commands and unsafe arguments", () => {
    expect(() => validateStdioConfig({ command: "python3", args: ["mcp_server.py"], bridgeUrl: "http://127.0.0.1:8077" })).not.toThrow();
    expect(() => validateStdioConfig({ command: "rm", args: ["-rf", "/"], bridgeUrl: "http://127.0.0.1:8077" })).toThrow(/not allowlisted/);
    expect(() => validateStdioConfig({ command: "python3", args: ["evil;rm"], bridgeUrl: "http://127.0.0.1:8077" })).toThrow(/Unsafe/);
    expect(() => validateStdioConfig({ command: "python3", args: ["mcp_server.py"], cwd: "../roblox-bridge", bridgeUrl: "http://127.0.0.1:8077" })).not.toThrow();
    expect(() => validateStdioConfig({ command: "python3", args: ["mcp_server.py"], cwd: "../../etc", bridgeUrl: "http://127.0.0.1:8077" })).toThrow(/Unsafe stdio working directory/);
  });
  it("ships a valid default workflow template", async () => {
    const { validateWorkflowGraph } = await import("@/lib/orchestrator/graph");
    expect(validateWorkflowGraph(DEFAULT_WORKFLOW_GRAPH).valid).toBe(true);
  });
});
