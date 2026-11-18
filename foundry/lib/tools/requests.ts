import crypto from "node:crypto";
import { query } from "@/lib/db";
import { ApiError } from "@/lib/errors";
import { assertOwnerRecord } from "@/lib/access";
import { emitEvent } from "@/lib/events";
import { recordAudit } from "@/lib/audit";
import { enqueueJob } from "@/lib/jobs";
import { makeMcpClient, getIntegration, getIntegrationSecret } from "@/lib/integrations";
import { TunnelingClient } from "@/lib/tunneling/client";
import { getTool, type ToolRow } from "@/lib/tools/registry";
import { approvalDecision, assertHttpUrlAllowed, assertToolAllowed } from "@/lib/tools/policy";
import { validateJsonSchema } from "@/lib/tools/validator";
import { createMemory } from "@/lib/memory";
import { searchKnowledge } from "@/lib/knowledge";
import { asObject, boundedJson, redactSecrets } from "@/lib/utils";
import type { ApprovalKind } from "@/lib/tools/policy";

export type ToolCallInput = {
  ownerId: string;
  toolId: string;
  agentId: string;
  input: Record<string, unknown>;
  taskId?: string | null;
  runId?: string | null;
  projectId?: string | null;
  reason?: string;
  workerId?: string;
  protocolExtras?: Record<string, unknown>;
};

function signatureOf(tool: ToolRow, input: Record<string, unknown>, taskId?: string | null) {
  return crypto.createHash("sha256").update(JSON.stringify({ toolId: tool.id, name: tool.name, taskId: taskId ?? null, input })).digest("hex");
}

export type PreparedToolCall = { status: "ready"; tool: ToolRow; agent: Record<string, unknown>; signature: string } | { status: "awaiting_approval"; approvalId: string; kind: ApprovalKind; reason: string; signature: string };

export async function prepareToolCall(call: ToolCallInput): Promise<PreparedToolCall> {
  const tool = await getTool(call.ownerId, call.toolId);
  if (!call.agentId) throw new ApiError("A registered agent is required for every tool call.", 403, "AGENT_REQUIRED");
  const agent = await assertOwnerRecord("agents", call.ownerId, call.agentId);
  assertToolAllowed(agent, { name: tool.name, kind: tool.kind, required_permissions: tool.required_permissions });
  const validation = validateJsonSchema(tool.input_schema, call.input);
  if (!validation.valid) throw new ApiError(`Tool input failed runtime schema validation: ${validation.errors.map((e) => `${e.path} ${e.message}`).join("; ")}`, 400, "TOOL_SCHEMA_INVALID", validation.errors);
  const signature = signatureOf(tool, call.input, call.taskId);
  const decision = approvalDecision(agent, { metadata: tool.metadata, name: tool.name }, call.input);
  if (decision) {
    const approved = await query<{ id: string }>(
      `select id from public.approvals
       where owner_id=$1 and tool_id=$2 and status='approved' and payload->>'signature'=$3
         and ($4::uuid is null or task_id=$4)
       order by decided_at desc limit 1`,
      [call.ownerId, tool.id, signature, call.taskId ?? null]
    );
    if (approved[0]) return { status: "ready", tool, agent, signature };
    const pending = await query<{ id: string }>(
      `select id from public.approvals
       where owner_id=$1 and tool_id=$2 and status='pending' and payload->>'signature'=$3
         and ($4::uuid is null or task_id=$4)
       limit 1`,
      [call.ownerId, tool.id, signature, call.taskId ?? null]
    );
    if (pending[0]) return { status: "awaiting_approval", approvalId: pending[0].id, kind: decision.kind, reason: decision.reason, signature };
    const payload = {
      signature,
      toolCall: { ownerId: call.ownerId, toolId: call.toolId, agentId: call.agentId, input: call.input, taskId: call.taskId ?? null, runId: call.runId ?? null, projectId: call.projectId ?? null, protocolExtras: call.protocolExtras ?? {} },
      reason: decision.reason
    };
    const rows = await query<{ id: string }>(
      `insert into public.approvals (owner_id, run_id, task_id, tool_id, kind, status, reason, payload, requested_by)
       values ($1,$2,$3,$4,$5,'pending',$6,$7::jsonb,$8) returning id`,
      [call.ownerId, call.runId ?? null, call.taskId ?? null, tool.id, decision.kind, decision.reason, JSON.stringify(payload), call.workerId ?? "system"]
    );
    if (call.taskId) {
      await query("update public.tasks set status='waiting_approval', updated_at=now() where id=$1 and owner_id=$2", [call.taskId, call.ownerId]);
    }
    await emitEvent({ ownerId: call.ownerId, kind: "approval.requested", level: "warn", data: { approvalId: rows[0].id, tool: tool.name, kind: decision.kind, reason: decision.reason }, taskId: call.taskId, runId: call.runId, projectId: call.projectId, agentId: call.agentId });
    return { status: "awaiting_approval", approvalId: rows[0].id, kind: decision.kind, reason: decision.reason, signature };
  }
  return { status: "ready", tool, agent, signature };
}

export async function requestToolExecution(call: ToolCallInput) {
  const prepared = await prepareToolCall(call);
  if (prepared.status === "awaiting_approval") return { status: "awaiting_approval", approvalId: prepared.approvalId, reason: prepared.reason };
  const jobId = await enqueueJob(call.ownerId, "tool.call", {
    ownerId: call.ownerId, toolId: call.toolId, agentId: call.agentId, input: call.input,
    taskId: call.taskId ?? null, runId: call.runId ?? null, projectId: call.projectId ?? null, signature: prepared.signature, protocolExtras: call.protocolExtras ?? {}
  });
  await emitEvent({ ownerId: call.ownerId, kind: "tool.queued", data: { jobId, tool: prepared.tool.name }, taskId: call.taskId, runId: call.runId, projectId: call.projectId, agentId: call.agentId });
  return { status: "queued", jobId, tool: prepared.tool.name };
}

async function requireStudioIfNeeded(ownerId: string, tool: ToolRow): Promise<void> {
  const metadata = asObject(tool.metadata);
  if (metadata.studioRequired !== true || !tool.integration_id) return;
  const integration = await getIntegration(ownerId, tool.integration_id);
  if (!integration.base_url) throw new ApiError("Bridge URL is unavailable.", 503, "BRIDGE_URL_UNAVAILABLE");
  const token = await getIntegrationSecret(ownerId, integration.id);
  const client = new TunnelingClient({ baseUrl: integration.base_url!, token: token ?? undefined });
  const health = await client.health();
  if (health.studio_connected !== true && (health.studio as Record<string, unknown> | undefined)?.online !== true) {
    throw new ApiError("Tunneling reports no connected Roblox Studio peer. This tool is unavailable.", 503, "STUDIO_DISCONNECTED");
  }
}

export async function performToolCall(call: ToolCallInput): Promise<unknown> {
  const prepared = await prepareToolCall(call);
  if (prepared.status === "awaiting_approval") throw new ApiError(`Tool call requires approval ${prepared.approvalId}: ${prepared.reason}`, 403, "APPROVAL_REQUIRED", { approvalId: prepared.approvalId });
  const tool = prepared.tool;
  if (tool.availability === "unavailable") throw new ApiError("This tool is registered as unavailable; no isolated executor is configured.", 503, "TOOL_UNAVAILABLE");
  await requireStudioIfNeeded(call.ownerId, tool);
  const started = Date.now();
  let output: unknown;
  const metadata = asObject(tool.metadata);
  if (tool.kind === "mcp") {
    if (!tool.integration_id) throw new ApiError("MCP tool is missing its integration.", 500, "TOOL_INTEGRATION_MISSING");
    const integration = await getIntegration(call.ownerId, tool.integration_id);
    const mcp = await makeMcpClient(call.ownerId, integration);
    try { output = await mcp.client.callTool(tool.name, call.input, call.protocolExtras ?? {}); }
    finally { await mcp.close(); }
    const response = asObject(output);
    if (response.resultType === "input_required" || response.inputRequests) {
      const structured = asObject(response.structuredContent);
      const requestId = String(response.requestId ?? structured.requestId ?? "");
      const requestState = typeof response.requestState === "string" ? response.requestState : null;
      const bridgePayload = {
        signature: prepared.signature,
        requestId,
        integrationId: tool.integration_id,
        toolCall: {
          ownerId: call.ownerId, toolId: call.toolId, agentId: call.agentId, input: call.input,
          taskId: call.taskId ?? null, runId: call.runId ?? null, projectId: call.projectId ?? null,
          protocolExtras: requestState ? { requestState, inputResponses: { confirm: { approved: true } } } : {}
        },
        bridgeResponse: redactSecrets(response)
      };
      const approvalRows = await query<{ id: string }>(
        `insert into public.approvals (owner_id, run_id, task_id, tool_id, kind, status, reason, payload, requested_by)
         values ($1,$2,$3,$4,'bridge_confirmation','pending',$5,$6::jsonb,$7) returning id`,
        [call.ownerId, call.runId ?? null, call.taskId ?? null, tool.id, "Tunneling ConfirmGate returned input_required; human decision is required.", JSON.stringify(bridgePayload), call.workerId ?? "worker"]
      );
      if (call.taskId) await query("update public.tasks set status='waiting_approval',lease_owner=null,lease_expires_at=null,updated_at=now() where id=$1 and owner_id=$2", [call.taskId, call.ownerId]);
      await emitEvent({ ownerId: call.ownerId, kind: "approval.requested", level: "warn", data: { approvalId: approvalRows[0]?.id, kind: "bridge_confirmation", requestId }, runId: call.runId, taskId: call.taskId, projectId: call.projectId, agentId: call.agentId });
      throw new ApiError("Tunneling ConfirmGate requires a human decision before retrying this tool.", 403, "BRIDGE_CONFIRMATION_REQUIRED", { approvalId: approvalRows[0]?.id, requestId });
    }
  } else if (tool.kind === "http") {
    const endpoint = String(metadata.endpoint ?? call.input.url ?? "");
    const url = assertHttpUrlAllowed(endpoint, metadata.httpAllowlist ?? []);
    const method = String(call.input.method ?? "GET").toUpperCase();
    if (!["GET", "POST", "PUT", "PATCH", "DELETE"].includes(method)) throw new ApiError("Unsupported HTTP method.", 400, "HTTP_METHOD_DENIED");
    const response = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: method === "GET" ? undefined : JSON.stringify(call.input.body ?? {}),
      signal: AbortSignal.timeout(tool.timeout_ms),
      cache: "no-store"
    });
    const text = await response.text();
    output = { status: response.status, ok: response.ok, body: safeParse(text) };
    if (!response.ok) throw new ApiError(`HTTP tool returned ${response.status}`, 502, "HTTP_TOOL_FAILED", output);
  } else if (tool.kind === "knowledge") {
    output = await searchKnowledge(call.ownerId, {
      query: String(call.input.query ?? ""), projectId: (call.input.projectId as string | undefined) ?? call.projectId ?? null,
      documentId: call.input.documentId as string | undefined, limit: Number(call.input.limit ?? 6)
    });
  } else if (tool.kind === "memory") {
    const operation = String(call.input.operation ?? "search");
    if (operation === "search") {
      output = await import("@/lib/memory").then((module) => module.searchMemories(call.ownerId, {
        query: String(call.input.query ?? ""), scope: call.input.scope as "agent" | "project" | "team" | "workflow",
        agentId: (call.input.agentId as string | undefined) ?? call.agentId, projectId: call.projectId ?? null, runId: call.runId ?? null,
        teamId: call.input.teamId as string | undefined, limit: Number(call.input.limit ?? 6)
      }));
    } else if (operation === "write") {
      output = await createMemory({
        ownerId: call.ownerId,
        scope: (call.input.scope as "agent" | "project" | "team" | "workflow") ?? "agent",
        title: String(call.input.title ?? "Agent memory"),
        content: String(call.input.content ?? ""),
        agentId: (call.input.agentId as string | undefined) ?? call.agentId,
        projectId: call.projectId ?? null,
        runId: call.runId ?? null,
        teamId: call.input.teamId as string | undefined,
        visibility: (call.input.visibility as "private" | "team" | "project" | undefined) ?? "private",
        metadata: asObject(call.input.metadata),
        createdBy: `agent:${call.agentId}`
      });
    } else {
      throw new ApiError("Unsupported memory tool operation.", 400, "MEMORY_OPERATION_DENIED");
    }
  } else {
    throw new ApiError(`Tool kind ${tool.kind} has no configured isolated executor.`, 503, "TOOL_UNAVAILABLE");
  }
  const safeOutput = redactSecrets(output);
  await emitEvent({ ownerId: call.ownerId, kind: "tool.completed", data: { tool: tool.name, durationMs: Date.now() - started, output: summarize(safeOutput) }, taskId: call.taskId, runId: call.runId, projectId: call.projectId, agentId: call.agentId });
  await recordAudit({ ownerId: call.ownerId, action: "tool.call", actor: `agent:${call.agentId}`, target: tool.id, outcome: "ok", detail: { tool: tool.name, durationMs: Date.now() - started, signature: prepared.signature } });
  if (call.runId || call.projectId) {
    await query(
      `insert into public.artifacts (owner_id, project_id, run_id, task_id, name, kind, content)
       values ($1,$2,$3,$4,$5,'json',$6::jsonb)`,
      [call.ownerId, call.projectId ?? null, call.runId ?? null, call.taskId ?? null, `${tool.name} result`, boundedJson(safeOutput, 100_000)]
    );
  }
  return safeOutput;
}

function safeParse(text: string): unknown {
  try { return JSON.parse(text); } catch { return text.slice(0, 20_000); }
}

function summarize(value: unknown): unknown {
  return boundedJson(value, 1200);
}
