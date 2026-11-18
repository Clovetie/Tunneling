import { ApiError } from "@/lib/errors";
import { asObject } from "@/lib/utils";
import type { McpTool } from "@/lib/mcp/jsonrpc";

export type BridgeRole = "read" | "write" | "admin";
export type ApprovalKind = "human_node" | "destructive_tool" | "publish" | "external_communication" | "credential" | "project_wide" | "bridge_confirmation";

export type ToolClassification = {
  requiredPermissions: BridgeRole[];
  approvalKind: ApprovalKind | null;
  approvalReason: string | null;
  studioRequired: boolean;
  risk: "read" | "write" | "admin" | "external" | "destructive";
};

const ADMIN_NAME = /(token|configure|permission|tunnel|queue|reset|clear|shutdown|restart|credential)/i;
const CREDENTIAL_NAME = /(token|credential|api[-_]?key|oauth|secret|password)/i;
const PUBLISH_NAME = /(publish|deploy|release|upload_place|save_to_roblox)/i;
const EXTERNAL_NAME = /(email|slack|discord|webhook|notify|tweet|send_message|post_)/i;
const DESTRUCTIVE_NAME = /(delete|remove|clear|destroy|replace|revert|undo|overwrite|set_active|configure|reset|start_project|apply_patch|edit_script)/i;
const STUDIO_TOOL_HINT = /(studio|script|instance|datamodel|place|viewport|playtest|luau|asset|selection|scene|tree|peer|confirm|gate|console|snapshot)/i;

export function classifyMcpTool(tool: Pick<McpTool, "name" | "annotations" | "description" | "execution">): ToolClassification {
  const name = tool.name;
  const annotations = asObject(tool.annotations);
  const readOnly = annotations.readOnlyHint === true;
  const destructiveHint = annotations.destructiveHint === true;
  const confirmationRequired = asObject(tool.execution).confirmationRequired === true || Boolean(tool.execution && asObject(tool.execution).confirmationRequired);
  let requiredPermissions: BridgeRole[] = [readOnly ? "read" : "write"];
  if (ADMIN_NAME.test(name)) requiredPermissions = ["admin"];
  let approvalKind: ApprovalKind | null = null;
  let approvalReason: string | null = null;
  if (CREDENTIAL_NAME.test(name)) { approvalKind = "credential"; approvalReason = "Credential or token modification requires explicit human approval."; }
  else if (PUBLISH_NAME.test(name)) { approvalKind = "publish"; approvalReason = "Publishing or deployment requires explicit human approval."; }
  else if (EXTERNAL_NAME.test(name)) { approvalKind = "external_communication"; approvalReason = "External communication requires explicit human approval."; }
  else if (destructiveHint || DESTRUCTIVE_NAME.test(name)) { approvalKind = "destructive_tool"; approvalReason = "Destructive or project-wide mutation requires explicit human approval."; }
  else if (!readOnly && (name === "agent_team" || name === "agent_bus" || name.startsWith("arena_"))) { approvalKind = "project_wide"; approvalReason = "Multi-agent coordination can create project-wide changes."; }
  else if (confirmationRequired) { approvalKind = "project_wide"; approvalReason = "Tunneling declares this tool as requiring confirmation."; }
  const risk = approvalKind === "credential" ? "admin" : approvalKind === "publish" || approvalKind === "external_communication" ? "external" : destructiveHint || DESTRUCTIVE_NAME.test(name) ? "destructive" : readOnly ? "read" : "write";
  return {
    requiredPermissions,
    approvalKind,
    approvalReason,
    studioRequired: STUDIO_TOOL_HINT.test(name) || STUDIO_TOOL_HINT.test(String(tool.description ?? "")),
    risk
  };
}

export type AgentPermissions = {
  allow: string[];
  deny: string[];
  maxRole: BridgeRole;
  requireApproval: boolean;
};

const ROLE_RANK: Record<BridgeRole, number> = { read: 1, write: 2, admin: 3 };

export function parseToolPermissions(value: unknown): AgentPermissions {
  if (Array.isArray(value)) return { allow: value.map(String), deny: [], maxRole: "write", requireApproval: false };
  const object = asObject(value);
  return {
    allow: Array.isArray(object.allow) ? object.allow.map(String) : [],
    deny: Array.isArray(object.deny) ? object.deny.map(String) : [],
    maxRole: (["read", "write", "admin"].includes(String(object.maxRole)) ? object.maxRole : "write") as BridgeRole,
    requireApproval: object.requireApproval === true
  };
}

function matches(pattern: string, toolName: string, kind: string): boolean {
  if (pattern === "*" || pattern === "all") return true;
  if (pattern.endsWith(":*")) return toolName.startsWith(pattern.slice(0, -1)) || kind === pattern.slice(0, -2);
  return pattern === toolName || pattern === kind;
}

export function assertToolAllowed(agent: { tool_permissions?: unknown; enabled?: boolean; status?: string }, tool: { name: string; kind: string; required_permissions?: unknown }): AgentPermissions {
  if (agent.enabled === false || agent.status === "disabled") throw new ApiError("Agent is disabled.", 403, "AGENT_DISABLED");
  const permissions = parseToolPermissions(agent.tool_permissions);
  const required = (Array.isArray(tool.required_permissions) ? tool.required_permissions : ["read"]).map(String) as BridgeRole[];
  const highest = required.reduce<BridgeRole>((acc, role) => ROLE_RANK[role] > ROLE_RANK[acc] ? role : acc, "read");
  if (ROLE_RANK[highest] > ROLE_RANK[permissions.maxRole]) throw new ApiError(`Agent role ${permissions.maxRole} cannot use ${highest} tool ${tool.name}.`, 403, "TOOL_ROLE_DENIED");
  if (permissions.deny.some((pattern) => matches(pattern, tool.name, tool.kind))) throw new ApiError(`Tool ${tool.name} is denied for this agent.`, 403, "TOOL_DENIED");
  if (!permissions.allow.some((pattern) => matches(pattern, tool.name, tool.kind))) throw new ApiError(`Agent has not been granted ${tool.name}.`, 403, "TOOL_NOT_GRANTED");
  return permissions;
}

export function approvalDecision(agent: { tool_permissions?: unknown }, tool: { metadata?: unknown; approval_kind?: string | null; name?: string }, input: Record<string, unknown> = {}): { required: boolean; kind: ApprovalKind; reason: string } | null {
  const permissions = parseToolPermissions(agent.tool_permissions);
  const metadata = asObject(tool.metadata);
  const kind = (tool.approval_kind ?? metadata.approvalKind ?? null) as ApprovalKind | null;
  const reason = String(metadata.approvalReason ?? "Consequential action requires explicit human approval.");
  if (kind) return { required: true, kind, reason };
  if (permissions.requireApproval) return { required: true, kind: "project_wide", reason: "Agent policy requires approval for every tool call." };
  const action = String(input.action ?? "");
  if (/(delete|remove|clear|publish|deploy|reset|credential|token|external|send|notify|start_project|apply|edit|write|save|set_)/i.test(action)) {
    return { required: true, kind: "project_wide", reason: `Action ${action} requires explicit human approval.` };
  }
  return null;
}

export function assertHttpUrlAllowed(rawUrl: string, allowlist: unknown): URL {
  const url = new URL(rawUrl);
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new ApiError("Only http and https URLs are allowed.", 400, "HTTP_PROTOCOL_DENIED");
  const allowed = Array.isArray(allowlist) ? allowlist.map(String).filter(Boolean) : [];
  if (!allowed.length) throw new ApiError("No HTTP origin allowlist is configured for this integration.", 403, "HTTP_ALLOWLIST_EMPTY");
  if (!allowed.includes(url.origin)) throw new ApiError(`HTTP origin ${url.origin} is not allowlisted.`, 403, "HTTP_ORIGIN_DENIED");
  const hostname = url.hostname.toLowerCase();
  if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "0.0.0.0" || hostname === "::1" || hostname.endsWith(".local")) {
    throw new ApiError("Loopback HTTP targets are blocked by default.", 403, "HTTP_LOOPBACK_DENIED");
  }
  return url;
}
