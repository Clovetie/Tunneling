import { describe, expect, it } from "vitest";
import { approvalDecision, assertHttpUrlAllowed, assertToolAllowed, classifyMcpTool, parseToolPermissions } from "@/lib/tools/policy";
import { validateJsonSchema } from "@/lib/tools/validator";

describe("tool permissions", () => {
  const agent = { tool_permissions: { allow: ["mcp:*"], deny: ["delete_instance"], maxRole: "write", requireApproval: false }, enabled: true, status: "idle" };
  it("grants allowlisted tools and denies explicit denials, missing grants and role escalation", () => {
    expect(() => assertToolAllowed(agent, { name: "get_studio_state", kind: "mcp", required_permissions: ["read"] })).not.toThrow();
    expect(() => assertToolAllowed(agent, { name: "delete_instance", kind: "mcp", required_permissions: ["write"] })).toThrow(/denied/);
    expect(() => assertToolAllowed({ ...agent, tool_permissions: [] }, { name: "get_studio_state", kind: "mcp", required_permissions: ["read"] })).toThrow(/not been granted/);
    expect(() => assertToolAllowed(agent, { name: "configure", kind: "mcp", required_permissions: ["admin"] })).toThrow(/cannot use admin/);
    expect(parseToolPermissions([]).maxRole).toBe("write");
  });
  it("classifies destructive, credential and read-only tools from runtime annotations", () => {
    expect(classifyMcpTool({ name: "delete_instance", annotations: { destructiveHint: true } }).approvalKind).toBe("destructive_tool");
    expect(classifyMcpTool({ name: "create_token", annotations: {} }).approvalKind).toBe("credential");
    expect(classifyMcpTool({ name: "get_studio_state", annotations: { readOnlyHint: true } }).requiredPermissions).toEqual(["read"]);
    expect(classifyMcpTool({ name: "agent_team", annotations: {} }).approvalKind).toBe("project_wide");
  });
  it("requires approval for consequential categories and agent-wide policy", () => {
    expect(approvalDecision(agent, { metadata: { approvalKind: "credential" } })?.kind).toBe("credential");
    expect(approvalDecision({ tool_permissions: { requireApproval: true } }, { metadata: {} })?.required).toBe(true);
    expect(approvalDecision(agent, { metadata: {} }, { action: "start_project" })?.required).toBe(true);
    expect(approvalDecision(agent, { metadata: {} }, { action: "status" })).toBeNull();
  });
  it("enforces HTTP origin allowlists and rejects loopback", () => {
    expect(() => assertHttpUrlAllowed("https://api.example.com/v1", ["https://api.example.com"])).not.toThrow();
    expect(() => assertHttpUrlAllowed("https://evil.example.com", ["https://api.example.com"])).toThrow(/not allowlisted/);
    expect(() => assertHttpUrlAllowed("http://127.0.0.1:8077", ["http://127.0.0.1:8077"])).toThrow(/Loopback/);
    expect(() => assertHttpUrlAllowed("file:///etc/passwd", ["file://"])).toThrow(/Only http/);
  });
  it("validates discovered MCP input schemas before calls", () => {
    const schema = { type: "object", properties: { action: { type: "string", enum: ["status", "start"] } }, required: ["action"], additionalProperties: false };
    expect(validateJsonSchema(schema, { action: "status" }).valid).toBe(true);
    expect(validateJsonSchema(schema, { action: "delete" }).valid).toBe(false);
    expect(validateJsonSchema(schema, []).valid).toBe(false);
  });
});
