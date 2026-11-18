export type JsonRpcError = { code: number; message: string; data?: unknown };
export type JsonRpcResponse<T = unknown> = {
  jsonrpc: "2.0";
  id: string | number | null;
  result?: T;
  error?: JsonRpcError;
};

export type McpTool = {
  name: string;
  title?: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
  outputSchema?: Record<string, unknown>;
  annotations?: Record<string, unknown>;
  execution?: Record<string, unknown>;
  [key: string]: unknown;
};

export type McpContent = { type: string; text?: string; data?: string; mimeType?: string; [key: string]: unknown };

export type McpCallResult = {
  content?: McpContent[];
  structuredContent?: unknown;
  isError?: boolean;
  resultType?: string;
  inputRequests?: unknown;
  _meta?: Record<string, unknown>;
  [key: string]: unknown;
};

export function jsonRpcRequest(method: string, params?: unknown, id: string | number = crypto.randomUUID()): Record<string, unknown> {
  return { jsonrpc: "2.0", id, method, ...(params === undefined ? {} : { params }) };
}

export function parseSsePayload(text: string): unknown[] {
  const messages: unknown[] = [];
  const events = text.replace(/\r\n/g, "\n").split("\n\n");
  for (const event of events) {
    const dataLines = event.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart());
    if (!dataLines.length) continue;
    const payload = dataLines.join("\n").trim();
    if (!payload || payload === "[DONE]") continue;
    try { messages.push(JSON.parse(payload)); } catch { /* ignore malformed event */ }
  }
  return messages;
}

export function parseMcpResponse<T = unknown>(body: string, contentType = "application/json"): JsonRpcResponse<T> {
  if (contentType.toLowerCase().includes("text/event-stream")) {
    const messages = parseSsePayload(body) as JsonRpcResponse<T>[];
    const response = [...messages].reverse().find((message) => message && (message.result !== undefined || message.error !== undefined));
    if (!response) throw new Error("MCP stream did not contain a JSON-RPC response");
    if (response.error) throw new Error(`MCP error ${response.error.code}: ${response.error.message}`);
    return response;
  }
  const parsed = JSON.parse(body) as JsonRpcResponse<T>;
  if (parsed.error) throw new Error(`MCP error ${parsed.error.code}: ${parsed.error.message}`);
  return parsed;
}

export function extractJsonRpcFrames(buffer: Buffer<ArrayBufferLike>): { messages: Array<Record<string, unknown>>; remainder: Buffer<ArrayBufferLike> } {
  const messages: Array<Record<string, unknown>> = [];
  let offset = 0;
  while (offset < buffer.length) {
    const slice = buffer.subarray(offset);
    const headerText = slice.subarray(0, Math.min(slice.length, 256)).toString("utf8");
    const lengthMatch = /^content-length:\s*(\d+)/i.exec(headerText);
    if (lengthMatch) {
      const headerEnd = slice.indexOf("\r\n\r\n");
      if (headerEnd < 0) break;
      const length = Number(lengthMatch[1]);
      if (!Number.isFinite(length) || length < 0 || length > 5_000_000) throw new Error("Invalid MCP Content-Length frame");
      const bodyStart = headerEnd + 4;
      if (slice.length < bodyStart + length) break;
      const body = slice.subarray(bodyStart, bodyStart + length).toString("utf8");
      if (body.trim()) messages.push(JSON.parse(body) as Record<string, unknown>);
      offset += bodyStart + length;
      continue;
    }
    const newline = slice.indexOf("\n");
    if (newline < 0) break;
    const line = slice.subarray(0, newline).toString("utf8").trim();
    offset += newline + 1;
    if (!line) continue;
    try { messages.push(JSON.parse(line) as Record<string, unknown>); }
    catch { throw new Error(`Invalid MCP NDJSON frame: ${line.slice(0, 120)}`); }
  }
  return { messages, remainder: buffer.subarray(offset) };
}

export function mcpText(result: McpCallResult): string {
  if (result.structuredContent !== undefined) return JSON.stringify(result.structuredContent);
  return (result.content ?? [])
    .map((part) => part.type === "text" ? (part.text ?? "") : part.type === "image" ? `[image:${part.mimeType ?? "unknown"}]` : "")
    .filter(Boolean)
    .join("\n");
}

export function mcpResultToJson(result: McpCallResult): unknown {
  const text = mcpText(result);
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { parsed = text; }
  // Tunneling's ConfirmGate puts resultType/requestState/inputRequests at the MCP result
  // level while the human-readable structuredContent carries requestId. Preserve both.
  const protocol: Record<string, unknown> = {};
  for (const key of ["resultType", "inputRequests", "requestState", "requestId", "expiresAt", "_meta"]) {
    if (result[key] !== undefined) protocol[key] = result[key];
  }
  if (!Object.keys(protocol).length) return parsed;
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return { ...(parsed as Record<string, unknown>), ...protocol };
  return { value: parsed, ...protocol };
}
