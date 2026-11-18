import { NextResponse } from "next/server";
import crypto from "node:crypto";
import { query, transaction, databaseHealth } from "@/lib/db";
import { jsonError, jsonOk, ApiError, requireString, optionalString, requireObject, jsonb } from "@/lib/errors";
import { requireUser, type AuthUser } from "@/lib/auth";
import { enforceRateLimit } from "@/lib/rate-limit";
import { recordAudit, verifyAuditChain, recentAudit } from "@/lib/audit";
import { emitEvent, recentEvents } from "@/lib/events";
import { enqueueJob } from "@/lib/jobs";
import { enqueueCoordinateJob } from "@/lib/orchestrator/coordinator";
import { compileGraphToTasks, validateWorkflowGraph } from "@/lib/orchestrator/graph";
import { assertOwnerRecord, assertProjectAccess } from "@/lib/access";
import { getPublicConfig } from "@/lib/public-config";
import { configuredProviders } from "@/lib/ai/providers";
import { encryptionFingerprint, isEncryptionConfigured } from "@/lib/crypto";
import { TEMPLATES, DEFAULT_WORKFLOW_GRAPH } from "@/lib/templates";
import { listMemories, createMemory, updateMemory, deleteMemory, memoryVersions, searchMemories } from "@/lib/memory";
import type { MemoryScope } from "@/lib/types";
import { listKnowledgeDocuments, createKnowledgeDocument, deleteKnowledgeDocument, searchKnowledge } from "@/lib/knowledge";
import { listIntegrations, getIntegration, setIntegrationSecret, makeRestClient, testIntegration, bootstrapTunnelingFromEnv } from "@/lib/integrations";
import { TunnelingClient, summarizeTunnelingHealth } from "@/lib/tunneling/client";
import { listTools, getTool, createCustomTool, discoverMcpTools } from "@/lib/tools/registry";
import { requestToolExecution } from "@/lib/tools/requests";
import { sendMessage, listMessages, importBusMessages } from "@/lib/communication";
import { asObject, assertSafePathSegment, redactSecrets } from "@/lib/utils";
import type { WorkflowGraph } from "@/lib/types";

const MUTATING = new Set(["POST", "PATCH", "DELETE", "PUT"]);

function slugify(value: string): string {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || `item-${crypto.randomUUID().slice(0, 8)}`;
}

async function readBody(request: Request, method: string): Promise<Record<string, unknown>> {
  if (!MUTATING.has(method)) return {};
  const text = await request.text();
  if (!text.trim()) return {};
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("body must be an object");
    return parsed as Record<string, unknown>;
  } catch (error) {
    throw new ApiError(`Invalid JSON body: ${error instanceof Error ? error.message : String(error)}`, 400, "INVALID_JSON");
  }
}

function q(search: URLSearchParams, key: string): string | null {
  const value = search.get(key);
  return value && value.trim() ? value.trim() : null;
}

function limitOf(search: URLSearchParams, fallback = 100, max = 300): number {
  return Math.min(Math.max(Number(search.get("limit") ?? fallback) || fallback, 1), max);
}

function bodyObject(body: Record<string, unknown>, key: string, fallback: Record<string, unknown> = {}): Record<string, unknown> {
  return body[key] == null ? fallback : asObject(body[key]);
}

function bodyArray(body: Record<string, unknown>, key: string): unknown[] {
  return Array.isArray(body[key]) ? body[key] as unknown[] : [];
}

async function assertDb() {
  if (!(await import("@/lib/db")).isDatabaseConfigured()) throw new ApiError("DATABASE_URL is not configured.", 503, "DATABASE_NOT_CONFIGURED");
}

export async function handleFoundryRequest(request: Request, method: string, segments: string[]): Promise<NextResponse> {
  try {
    const user = await requireUser(request);
    await assertDb();
    const url = new URL(request.url);
    const body = await readBody(request, method);
    if (MUTATING.has(method)) await enforceRateLimit(user.id, `${method}:${segments.join("/")}`);
    const result = await dispatchFoundry({ request, method, segments, body, search: url.searchParams, user });
    const response = result instanceof Response ? result as NextResponse : jsonOk(result);
    if (MUTATING.has(method)) {
      await recordAudit({ ownerId: user.id, action: `${method}:${segments.join("/")}`, actor: "user", target: segments[1] ?? "", detail: redactSecrets(body) }).catch((error) => console.error("[foundry:audit]", error instanceof Error ? error.message : String(error)));
    }
    return response;
  } catch (error) {
    return jsonError(error);
  }
}

type DispatchContext = {
  request: Request;
  method: string;
  segments: string[];
  body: Record<string, unknown>;
  search: URLSearchParams;
  user: AuthUser;
};

async function dispatchFoundry(ctx: DispatchContext): Promise<unknown | NextResponse> {
  const { method, segments, body, search, user } = ctx;
  const ownerId = user.id;
  const [area, id, action, extra] = segments;

  if (area === "dashboard" && method === "GET") return dashboard(ownerId);
  if (area === "bootstrap" && method === "POST") return bootstrapWorkspace(ownerId);
  if (area === "config" && method === "GET") return { ...getPublicConfig(), encryptionFingerprint: encryptionFingerprint(), providers: configuredProviders() };

  if (area === "agents") {
    if (!id && method === "GET") return query("select * from public.agents where owner_id=$1 order by created_at desc limit $2", [ownerId, limitOf(search, 200, 500)]);
    if (!id && method === "POST") return createAgent(ownerId, body);
    if (id && action === "clone" && method === "POST") return cloneAgent(ownerId, id);
    if (id && action === "disable" && method === "POST") {
      await assertOwnerRecord("agents", ownerId, id);
      await query("update public.agents set enabled=false,status='disabled',updated_at=now() where id=$1 and owner_id=$2", [id, ownerId]);
      return { id, enabled: false, status: "disabled" };
    }
    if (id && method === "GET") return assertOwnerRecord("agents", ownerId, id);
    if (id && method === "PATCH") return patchAgent(ownerId, id, body);
    if (id && method === "DELETE") {
      await assertOwnerRecord("agents", ownerId, id);
      await query("delete from public.agents where id=$1 and owner_id=$2 returning id", [id, ownerId]);
      return { deleted: true };
    }
  }

  if (area === "templates") {
    if (method === "GET") return query("select * from public.agent_templates where owner_id is null or owner_id=$1 order by is_roblox desc, name asc", [ownerId]);
    if (id === "bootstrap" && method === "POST") return bootstrapWorkspace(ownerId);
    if (id && action === "apply" && method === "POST") return applyTemplate(ownerId, id);
    if (!id && method === "POST") return createTemplate(ownerId, body);
  }

  if (area === "projects") {
    if (!id && method === "GET") return query("select * from public.projects where owner_id=$1 order by created_at desc", [ownerId]);
    if (!id && method === "POST") return createProject(ownerId, body);
    if (id && action === "items" && method === "GET") {
      await assertProjectAccess(ownerId, id, "viewer");
      return query("select * from public.workspace_items where owner_id=$1 and project_id=$2 order by created_at desc limit $3", [ownerId, id, limitOf(search, 200, 500)]);
    }
    if (id && action === "items" && method === "POST") return createWorkspaceItem(ownerId, id, body);
    if (id && action === "members" && method === "GET") {
      await assertProjectAccess(ownerId, id, "viewer");
      return query("select * from public.project_members where project_id=$1 order by created_at asc", [id]);
    }
    if (id && action === "members" && method === "POST") {
      await assertProjectAccess(ownerId, id, "owner");
      const userId = requireString(body.userId, "userId", 100);
      const role = String(body.role ?? "viewer");
      if (!["viewer", "editor", "owner"].includes(role)) throw new ApiError("Invalid project role.");
      return (await query("insert into public.project_members (project_id,user_id,role) values ($1,$2,$3) on conflict (project_id,user_id) do update set role=excluded.role returning *", [id, userId, role]))[0];
    }
    if (id && method === "GET") { await assertProjectAccess(ownerId, id, "viewer"); return (await query("select * from public.projects where id=$1 and owner_id=$2", [id, ownerId]))[0] ?? null; }
    if (id && method === "PATCH") {
      await assertProjectAccess(ownerId, id, "editor");
      const name = optionalString(body.name, "name", 200);
      const description = optionalString(body.description, "description", 5000);
      const metadata = bodyObject(body, "metadata");
      return (await query("update public.projects set name=coalesce(nullif($3,''),name),description=coalesce(nullif($4,''),description),metadata=$5::jsonb,updated_at=now() where id=$1 and owner_id=$2 returning *", [id, ownerId, name, description, JSON.stringify(metadata)]))[0];
    }
    if (id && method === "DELETE") {
      await assertProjectAccess(ownerId, id, "owner");
      await query("delete from public.projects where id=$1 and owner_id=$2 returning id", [id, ownerId]);
      return { deleted: true };
    }
  }

  if (area === "workspace-items" && id) {
    if (method === "PATCH") {
      await assertOwnerRecord("workspace_items", ownerId, id);
      return (await query("update public.workspace_items set title=coalesce(nullif($3,''),title),content=coalesce(nullif($4,''),content),status=coalesce(nullif($5,''),status),metadata=$6::jsonb,updated_at=now() where id=$1 and owner_id=$2 returning *", [id, ownerId, optionalString(body.title, "title", 300), optionalString(body.content, "content", 100000), optionalString(body.status, "status", 40), JSON.stringify(bodyObject(body, "metadata"))]))[0];
    }
    if (method === "DELETE") {
      await assertOwnerRecord("workspace_items", ownerId, id);
      await query("delete from public.workspace_items where id=$1 and owner_id=$2 returning id", [id, ownerId]);
      return { deleted: true };
    }
  }

  if (area === "workflows") {
    if (!id && method === "GET") return query("select * from public.workflows where owner_id=$1 order by updated_at desc limit $2", [ownerId, limitOf(search, 200, 500)]);
    if (!id && method === "POST") return createWorkflow(ownerId, body);
    if (id && action === "runs" && method === "GET") return query("select * from public.workflow_runs where owner_id=$1 and workflow_id=$2 order by created_at desc limit $3", [ownerId, id, limitOf(search, 100, 300)]);
    if (id && action === "runs" && method === "POST") return startRun(ownerId, id, body);
    if (id && method === "GET") return assertOwnerRecord("workflows", ownerId, id);
    if (id && method === "PATCH") return patchWorkflow(ownerId, id, body);
    if (id && method === "DELETE") {
      await assertOwnerRecord("workflows", ownerId, id);
      await query("delete from public.workflows where id=$1 and owner_id=$2 returning id", [id, ownerId]);
      return { deleted: true };
    }
  }

  if (area === "runs" && !id && method === "GET") return query("select * from public.workflow_runs where owner_id=$1 order by created_at desc limit $2", [ownerId, limitOf(search, 100, 300)]);
  if (area === "runs" && id) {
    if (!action && method === "GET") return assertOwnerRecord("workflow_runs", ownerId, id);
    if (action === "tasks" && method === "GET") return query("select * from public.tasks where owner_id=$1 and run_id=$2 order by created_at asc", [ownerId, id]);
    if (action === "artifacts" && method === "GET") return query("select * from public.artifacts where owner_id=$1 and run_id=$2 order by created_at desc limit $3", [ownerId, id, limitOf(search, 100, 300)]);
    if (action === "cancel" && method === "POST") {
      await assertOwnerRecord("workflow_runs", ownerId, id);
      await query("update public.workflow_runs set status='cancelled',finished_at=now(),updated_at=now() where id=$1 and owner_id=$2", [id, ownerId]);
      await query("update public.tasks set status='cancelled',updated_at=now() where run_id=$1 and owner_id=$2 and status not in ('completed','failed','skipped','cancelled')", [id, ownerId]);
      await query("update public.durable_jobs set status='cancelled',updated_at=now() where owner_id=$1 and status in ('queued','running') and payload->>'runId'=$2", [ownerId, id]);
      await emitEvent({ ownerId, kind: "run.cancelled", level: "warn", data: {}, runId: id });
      return { cancelled: true };
    }
  }

  if (area === "tasks" && id) {
    if (!action && method === "GET") return assertOwnerRecord("tasks", ownerId, id);
    if (action === "retry" && method === "POST") {
      const task = await assertOwnerRecord("tasks", ownerId, id);
      await query("update public.tasks set status='todo',last_error=null,lease_owner=null,lease_expires_at=null,updated_at=now() where id=$1 and owner_id=$2", [id, ownerId]);
      const jobId = await enqueueJob(ownerId, "task.execute", { ownerId, runId: String(task.run_id), taskId: id, taskKey: String(task.key), agentId: task.assignee_agent_id, resourceLock: task.resource_lock });
      return { queued: true, jobId };
    }
  }

  if (area === "memories") {
    if (id === "search" && method === "POST") {
      return searchMemories(ownerId, {
        query: requireString(body.query, "query", 4000),
        scope: String(body.scope ?? "agent") as MemoryScope,
        agentId: body.agentId ? String(body.agentId) : null,
        projectId: body.projectId ? String(body.projectId) : null,
        runId: body.runId ? String(body.runId) : null,
        teamId: body.teamId ? String(body.teamId) : null,
        limit: Number(body.limit ?? 8)
      });
    }
    if (!id && method === "GET") return listMemories(ownerId, { scope: (q(search, "scope") ?? undefined) as MemoryScope | undefined, agentId: q(search, "agentId"), projectId: q(search, "projectId"), runId: q(search, "runId"), teamId: q(search, "teamId") }, limitOf(search, 100, 300));
    if (!id && method === "POST") return createMemory({
      ownerId,
      scope: String(body.scope ?? "agent") as MemoryScope,
      title: requireString(body.title, "title", 300),
      content: requireString(body.content, "content", 50000),
      agentId: body.agentId ? String(body.agentId) : null,
      projectId: body.projectId ? String(body.projectId) : null,
      runId: body.runId ? String(body.runId) : null,
      teamId: body.teamId ? String(body.teamId) : null,
      visibility: (["private", "team", "project"].includes(String(body.visibility)) ? body.visibility : "private") as "private" | "team" | "project",
      metadata: bodyObject(body, "metadata"),
      createdBy: "user"
    });
    if (id && action === "versions" && method === "GET") return memoryVersions(ownerId, id);
    if (id && method === "GET") return (await query("select * from public.memories where id=$1 and owner_id=$2", [id, ownerId]))[0] ?? null;
    if (id && method === "PATCH") return updateMemory(ownerId, id, {
      title: body.title ? String(body.title) : undefined,
      content: body.content ? String(body.content) : undefined,
      metadata: body.metadata ? asObject(body.metadata) : undefined,
      visibility: (["private", "team", "project"].includes(String(body.visibility)) ? body.visibility : undefined) as "private" | "team" | "project" | undefined,
      changeNote: body.changeNote ? String(body.changeNote) : undefined
    });
    if (id && method === "DELETE") return deleteMemory(ownerId, id);
  }

  if (area === "knowledge") {
    if (id === "search" && method === "POST") return searchKnowledge(ownerId, { query: requireString(body.query, "query", 4000), projectId: body.projectId ? String(body.projectId) : null, documentId: body.documentId ? String(body.documentId) : null, limit: Number(body.limit ?? 8) });
    if (!id && method === "GET") return listKnowledgeDocuments(ownerId, q(search, "projectId"));
    if (!id && method === "POST") return createKnowledgeDocument({
      ownerId, projectId: body.projectId ? String(body.projectId) : null,
      title: requireString(body.title, "title", 300), content: requireString(body.content, "content", 200000),
      sourceUri: body.sourceUri ? String(body.sourceUri) : null, mimeType: body.mimeType ? String(body.mimeType) : "text/plain", metadata: bodyObject(body, "metadata")
    });
    if (id && method === "DELETE") return deleteKnowledgeDocument(ownerId, id);
  }

  if (area === "messages") {
    if (method === "GET") return listMessages(ownerId, { runId: q(search, "runId"), projectId: q(search, "projectId"), limit: limitOf(search, 100, 300) });
    if (method === "POST") return sendMessage({
      ownerId, channel: (["direct", "broadcast", "task", "artifact", "handoff", "system"].includes(String(body.channel)) ? body.channel : "task") as "direct" | "broadcast" | "task" | "artifact" | "handoff" | "system",
      content: requireString(body.content, "content", 20000),
      senderAgentId: body.senderAgentId ? String(body.senderAgentId) : null,
      recipientAgentId: body.recipientAgentId ? String(body.recipientAgentId) : null,
      projectId: body.projectId ? String(body.projectId) : null,
      runId: body.runId ? String(body.runId) : null,
      taskId: body.taskId ? String(body.taskId) : null,
      artifactId: body.artifactId ? String(body.artifactId) : null,
      payload: bodyObject(body, "payload"),
      transport: (["foundry", "tunneling_bus", "external"].includes(String(body.transport)) ? body.transport : "foundry") as "foundry" | "tunneling_bus" | "external"
    });
  }

  if (area === "events" && method === "GET") return recentEvents(ownerId, limitOf(search, 100, 300), search.has("cursor") ? Number(search.get("cursor")) : undefined, q(search, "runId") ?? undefined);

  if (area === "approvals") {
    if (!id && method === "GET") return query("select * from public.approvals where owner_id=$1 and ($2::text is null or status=$2) order by created_at desc limit $3", [ownerId, q(search, "status"), limitOf(search, 100, 300)]);
    if (id && action === "decision" && method === "POST") return decideApproval(ownerId, id, body);
  }

  if (area === "tools") {
    if (id === "refresh" && method === "POST") {
      const integrationId = requireString(body.integrationId, "integrationId", 100);
      await getIntegration(ownerId, integrationId);
      const jobId = await enqueueJob(ownerId, "mcp.refresh", { ownerId, integrationId });
      return jsonOk({ queued: true, jobId, note: "Runtime MCP discovery runs on the persistent worker." }, 202);
    }
    if (!id && method === "GET") return listTools(ownerId, { integrationId: q(search, "integrationId") ?? undefined, query: q(search, "query") ?? undefined, limit: limitOf(search, 300, 500) });
    if (!id && method === "POST") return createCustomTool(ownerId, {
      name: requireString(body.name, "name", 200), kind: requireString(body.kind, "kind", 40), description: optionalString(body.description, "description", 2000),
      inputSchema: bodyObject(body, "inputSchema"), outputSchema: body.outputSchema ? asObject(body.outputSchema) : undefined,
      requiredPermissions: bodyArray(body, "requiredPermissions").map(String), timeoutMs: Number(body.timeoutMs ?? 30000),
      endpoint: body.endpoint ? String(body.endpoint) : undefined, httpAllowlist: bodyArray(body, "httpAllowlist").map(String), metadata: bodyObject(body, "metadata")
    });
    if (id && method === "GET") return getTool(ownerId, id);
  }

  if (area === "integrations") {
    if (id === "bootstrap" && method === "POST") return bootstrapTunnelingFromEnv(ownerId);
    if (!id && method === "GET") return listIntegrations(ownerId);
    if (!id && method === "POST") return createIntegration(ownerId, body);
    if (id && action === "test" && method === "POST") return testIntegration(ownerId, id);
    if (id && action === "discover" && method === "POST") {
      await getIntegration(ownerId, id);
      const jobId = await enqueueJob(ownerId, "mcp.refresh", { ownerId, integrationId: id });
      return jsonOk({ queued: true, jobId }, 202);
    }
    if (id && method === "GET") return getIntegration(ownerId, id);
    if (id && method === "PATCH") {
      await getIntegration(ownerId, id);
      const name = optionalString(body.name, "name", 200);
      const baseUrl = body.baseUrl ? String(body.baseUrl) : null;
      const config = bodyObject(body, "config");
      return (await query("update public.integrations set name=coalesce(nullif($3,''),name),base_url=coalesce($4,base_url),config=case when $5::jsonb='{}'::jsonb then config else $5::jsonb end,updated_at=now() where id=$1 and owner_id=$2 returning id,owner_id,kind,name,transport,base_url,config,status,last_checked_at,last_error", [id, ownerId, name, baseUrl, JSON.stringify(config)]))[0];
    }
    if (id && method === "DELETE") {
      await getIntegration(ownerId, id);
      await query("delete from public.integrations where id=$1 and owner_id=$2 returning id", [id, ownerId]);
      return { deleted: true };
    }
  }

  if (area === "tunneling") {
    if (id === "status" && method === "GET") return tunnelingStatus(ownerId, q(search, "integrationId"));
    if (id === "fleet" && method === "GET") return (await restFor(ownerId, q(search, "integrationId"))).client.studios();
    if (id === "peers" && method === "GET") return (await restFor(ownerId, q(search, "integrationId"))).client.peers();
    if (id === "ops" && method === "GET") return (await restFor(ownerId, q(search, "integrationId"))).client.ops();
    if (id === "tunnel" && method === "GET") return (await restFor(ownerId, q(search, "integrationId"))).client.tunnel();
    if (id === "events" && method === "GET") return (await restFor(ownerId, q(search, "integrationId"))).client.events({ once: 1, limit: limitOf(search, 100, 300) });
    if (id === "audit" && method === "GET") return (await restFor(ownerId, q(search, "integrationId"))).client.audit({ limit: limitOf(search, 100, 300), ...(q(search, "verify") ? { verify: 1 } : {}) });
    if (id === "confirmations" && method === "GET") return (await restFor(ownerId, q(search, "integrationId"))).client.confirmations();
    if (id === "confirmations" && method === "POST") return bridgeConfirmation(ownerId, body);
    if (id === "bus" && method === "GET") {
      const { client } = await restFor(ownerId, q(search, "integrationId"));
      const params: Record<string, string | number | boolean> = {};
      for (const [key, value] of search.entries()) if (!["integrationId"].includes(key)) params[key] = value;
      return client.bus(String(params.action ?? "stats"), params);
    }
    if (id === "bus" && method === "POST") return busAction(ownerId, body);
    if (id === "orchestrator" && method === "GET") {
      const { client } = await restFor(ownerId, q(search, "integrationId"));
      const params: Record<string, string | number | boolean> = {};
      for (const [key, value] of search.entries()) if (!["integrationId"].includes(key)) params[key] = value;
      return client.orchestrator(String(params.action ?? "status"), params);
    }
    if (id === "orchestrator" && method === "POST") return orchestratorAction(ownerId, body);
    if (id === "tools" && !action && method === "GET") return listTools(ownerId, { integrationId: q(search, "integrationId") ?? undefined, query: q(search, "query") ?? undefined, limit: limitOf(search, 300, 500) });
    if (id === "tools" && action === "call" && method === "POST") return queueTunnelingTool(ownerId, body);
    if (id === "evidence" && action && method === "GET") return evidenceResponse(ownerId, action, q(search, "integrationId"));
  }

  if (area === "activity" && method === "GET") return activity(ownerId, limitOf(search, 80, 300));
  if (area === "audit" && id === "verify" && method === "GET") return verifyAuditChain(ownerId);
  if (area === "jobs" && method === "GET") return query("select id,type,payload,status,attempts,max_attempts,lease_owner,lease_expires_at,available_at,last_error,created_at,updated_at from public.durable_jobs where owner_id=$1 order by created_at desc limit $2", [ownerId, limitOf(search, 100, 300)]);
  if (area === "workers" && method === "GET") return query("select * from public.execution_workers order by last_heartbeat desc");
  if (area === "search" && method === "GET") return globalSearch(ownerId, q(search, "q") ?? "");

  throw new ApiError(`Unknown FOUNDRY API route: ${method} /${segments.join("/")}`, 404, "ROUTE_NOT_FOUND");
}

async function dashboard(ownerId: string) {
  const [agents, workflows, runs, tasks, approvals, integrations, tools, items, events, jobs, workers] = await Promise.all([
    query<{ count: string }>("select count(*)::text as count from public.agents where owner_id=$1", [ownerId]),
    query<{ count: string }>("select count(*)::text as count from public.workflows where owner_id=$1", [ownerId]),
    query<{ count: string; active: string }>("select count(*)::text as count, count(*) filter (where status in ('queued','planning','running','waiting_approval','blocked'))::text as active from public.workflow_runs where owner_id=$1", [ownerId]),
    query<{ running: string; failed: string }>("select count(*) filter (where status='running')::text as running, count(*) filter (where status='failed')::text as failed from public.tasks where owner_id=$1", [ownerId]),
    query<{ count: string }>("select count(*)::text as count from public.approvals where owner_id=$1 and status='pending'", [ownerId]),
    query<{ count: string }>("select count(*)::text as count from public.integrations where owner_id=$1", [ownerId]),
    query<{ count: string }>("select count(*)::text as count from public.tools where owner_id=$1", [ownerId]),
    query<{ count: string }>("select count(*)::text as count from public.workspace_items where owner_id=$1", [ownerId]),
    recentEvents(ownerId, 12),
    query<{ queued: string; running: string; failed: string }>("select count(*) filter (where status='queued')::text as queued, count(*) filter (where status='running')::text as running, count(*) filter (where status='failed')::text as failed from public.durable_jobs where owner_id=$1", [ownerId]),
    query("select id,status,last_heartbeat,metadata from public.execution_workers order by last_heartbeat desc limit 5")
  ]);
  const activeRuns = await query("select * from public.workflow_runs where owner_id=$1 and status in ('queued','planning','running','waiting_approval','blocked') order by created_at desc limit 8", [ownerId]);
  const agentRows = await query("select id,name,status,enabled,model,updated_at from public.agents where owner_id=$1 order by updated_at desc limit 8", [ownerId]);
  return {
    config: getPublicConfig(),
    database: await databaseHealth(),
    metrics: {
      agents: Number(agents[0]?.count ?? 0), workflows: Number(workflows[0]?.count ?? 0), runs: Number(runs[0]?.count ?? 0), activeRuns: Number(runs[0]?.active ?? 0),
      runningTasks: Number(tasks[0]?.running ?? 0), failedTasks: Number(tasks[0]?.failed ?? 0), pendingApprovals: Number(approvals[0]?.count ?? 0),
      integrations: Number(integrations[0]?.count ?? 0), tools: Number(tools[0]?.count ?? 0), workspaceItems: Number(items[0]?.count ?? 0),
      jobs: { queued: Number(jobs[0]?.queued ?? 0), running: Number(jobs[0]?.running ?? 0), failed: Number(jobs[0]?.failed ?? 0) }
    },
    activeRuns, agents: agentRows, recentEvents: events, workers,
    security: { encryptionConfigured: isEncryptionConfigured(), encryptionFingerprint: encryptionFingerprint(), providers: configuredProviders() }
  };
}

async function createAgent(ownerId: string, body: Record<string, unknown>) {
  const name = requireString(body.name, "name", 200);
  const projectId = body.projectId ? String(body.projectId) : null;
  if (projectId) await assertProjectAccess(ownerId, projectId, "editor");
  const slug = slugify(String(body.slug ?? name));
  const rows = await query<{ id: string }>(
    `insert into public.agents
      (owner_id,project_id,name,slug,description,avatar,identity,system_instructions,model,generation,tool_permissions,memory_config,knowledge_sources,communication_permissions,execution_limits,assigned_projects,status,enabled)
     values ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,$16::jsonb,$17,$18)
     returning id`,
    [
      ownerId, projectId, name, slug, optionalString(body.description, "description", 5000), optionalString(body.avatar, "avatar", 80, "bot"),
      JSON.stringify(bodyObject(body, "identity")), optionalString(body.systemInstructions, "systemInstructions", 50000),
      JSON.stringify(Object.keys(bodyObject(body, "model")).length ? bodyObject(body, "model") : { provider: "openai", model: "gpt-5-mini" }),
      JSON.stringify(Object.keys(bodyObject(body, "generation")).length ? bodyObject(body, "generation") : { temperature: 0.2, maxTokens: 4096 }),
      JSON.stringify(body.toolPermissions ?? []), JSON.stringify(Object.keys(bodyObject(body, "memoryConfig")).length ? bodyObject(body, "memoryConfig") : { enabled: true, scope: "agent", retrievalLimit: 6 }),
      JSON.stringify(bodyArray(body, "knowledgeSources")), JSON.stringify(Object.keys(bodyObject(body, "communicationPermissions")).length ? bodyObject(body, "communicationPermissions") : { direct: true, broadcast: false, task: true, artifact: true, handoff: true }),
      JSON.stringify(Object.keys(bodyObject(body, "executionLimits")).length ? bodyObject(body, "executionLimits") : { maxParallelTasks: 1, maxTokensPerRun: 30000, maxToolCalls: 20, timeoutMs: 120000 }),
      JSON.stringify(bodyArray(body, "assignedProjects")),
      ["idle", "thinking", "executing", "waiting_approval", "blocked", "failed", "completed", "disabled"].includes(String(body.status)) ? String(body.status) : "idle",
      body.enabled === false ? false : true
    ]
  );
  await emitEvent({ ownerId, kind: "agent.created", data: { name, slug }, projectId, agentId: rows[0]?.id });
  return { id: rows[0]?.id, name, slug };
}

async function patchAgent(ownerId: string, id: string, body: Record<string, unknown>) {
  const current = await assertOwnerRecord("agents", ownerId, id);
  const fields: string[] = [];
  const params: unknown[] = [id, ownerId];
  const add = (column: string, value: unknown) => { params.push(value); fields.push(`${column}=$${params.length}`); };
  if (body.name) add("name", String(body.name));
  if (body.description != null) add("description", String(body.description));
  if (body.avatar) add("avatar", String(body.avatar));
  if (body.systemInstructions != null) add("system_instructions", String(body.systemInstructions));
  for (const [key, column] of Object.entries({ identity: "identity", model: "model", generation: "generation", toolPermissions: "tool_permissions", memoryConfig: "memory_config", knowledgeSources: "knowledge_sources", communicationPermissions: "communication_permissions", executionLimits: "execution_limits", assignedProjects: "assigned_projects" })) {
    if (body[key] != null) { params.push(JSON.stringify(body[key])); fields.push(`${column}=$${params.length}::jsonb`); }
  }
  if (body.status) add("status", String(body.status));
  if (body.enabled != null) add("enabled", Boolean(body.enabled));
  if (!fields.length) return current;
  fields.push("updated_at=now()");
  return (await query(`update public.agents set ${fields.join(",")} where id=$1 and owner_id=$2 returning *`, params))[0];
}

async function cloneAgent(ownerId: string, id: string) {
  const current = await assertOwnerRecord("agents", ownerId, id);
  const name = `${String(current.name)} Copy`;
  const rows = await query<{ id: string; slug: string }>(
    `insert into public.agents
      (owner_id,project_id,name,slug,description,avatar,identity,system_instructions,model,generation,tool_permissions,memory_config,knowledge_sources,communication_permissions,execution_limits,assigned_projects,status,enabled)
     values ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,$16::jsonb,'idle',true) returning id,slug`,
    [ownerId, current.project_id, name, `${String(current.slug)}-copy-${crypto.randomUUID().slice(0, 6)}`, current.description, current.avatar, JSON.stringify(current.identity ?? {}), current.system_instructions, JSON.stringify(current.model ?? {}), JSON.stringify(current.generation ?? {}), JSON.stringify(current.tool_permissions ?? []), JSON.stringify(current.memory_config ?? {}), JSON.stringify(current.knowledge_sources ?? []), JSON.stringify(current.communication_permissions ?? {}), JSON.stringify(current.execution_limits ?? {}), JSON.stringify(current.assigned_projects ?? [])]
  );
  return rows[0];
}

async function createTemplate(ownerId: string, body: Record<string, unknown>) {
  const name = requireString(body.name, "name", 200);
  const slug = slugify(String(body.slug ?? name));
  return (await query(
    `insert into public.agent_templates (owner_id,slug,name,description,category,is_roblox,config)
     values ($1,$2,$3,$4,$5,$6,$7::jsonb) returning *`,
    [ownerId, slug, name, optionalString(body.description, "description", 5000), optionalString(body.category, "category", 80, "general"), Boolean(body.isRoblox), JSON.stringify(bodyObject(body, "config"))]
  ))[0];
}

async function applyTemplate(ownerId: string, slug: string) {
  const template = TEMPLATES.find((item) => item.slug === slug) ?? null;
  const custom = (await query<Record<string, unknown>>("select * from public.agent_templates where slug=$1 and (owner_id is null or owner_id=$2) limit 1", [slug, ownerId]))[0];
  const config = asObject(custom?.config ?? (template ? template.config : {}));
  if (!custom && !template) throw new ApiError("Template not found.", 404, "TEMPLATE_NOT_FOUND");
  return createAgent(ownerId, {
    name: String(custom?.name ?? template?.name ?? slug),
    slug, description: String(custom?.description ?? template?.description ?? ""),
    avatar: config.avatar, identity: config.identity, systemInstructions: config.systemInstructions,
    model: config.model, generation: config.generation, toolPermissions: config.toolPermissions,
    memoryConfig: config.memoryConfig, knowledgeSources: config.knowledgeSources,
    communicationPermissions: config.communicationPermissions, executionLimits: config.executionLimits
  });
}

async function bootstrapWorkspace(ownerId: string) {
  let templates = 0, agents = 0, workflows = 0;
  for (const template of TEMPLATES) {
    await query(
      `insert into public.agent_templates (owner_id,slug,name,description,category,is_roblox,config)
       values (null,$1,$2,$3,$4,$5,$6::jsonb)
       on conflict ((coalesce(owner_id, '00000000-0000-0000-0000-000000000000'::uuid)), slug) do update
       set name=excluded.name,description=excluded.description,category=excluded.category,is_roblox=excluded.is_roblox,config=excluded.config`,
      [template.slug, template.name, template.description, template.category, template.isRoblox, JSON.stringify(template.config)]
    );
    templates++;
  }
  const project = (await query<{ id: string }>(
    `insert into public.projects (owner_id,name,slug,description,metadata)
     values ($1,'Personal Workspace','personal-workspace','General-purpose tasks, notes, prompts and saved outputs.','{"kind":"personal","source":"bootstrap"}'::jsonb)
     on conflict (owner_id,slug) do update set updated_at=now() returning id`,
    [ownerId]
  ))[0];
  for (const template of TEMPLATES) {
    const config = template.config;
    const rows = await query(
      `insert into public.agents
        (owner_id,project_id,name,slug,description,avatar,identity,system_instructions,model,generation,tool_permissions,memory_config,knowledge_sources,communication_permissions,execution_limits,assigned_projects,status,enabled)
       values ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,$16::jsonb,'idle',true)
       on conflict (owner_id,slug) do nothing returning id`,
      [ownerId, project.id, template.name, template.slug, template.description, config.avatar, JSON.stringify(config.identity), config.systemInstructions, JSON.stringify(config.model), JSON.stringify(config.generation), JSON.stringify(config.toolPermissions), JSON.stringify(config.memoryConfig), JSON.stringify(config.knowledgeSources), JSON.stringify(config.communicationPermissions), JSON.stringify(config.executionLimits), JSON.stringify([project.id])]
    );
    if (rows.length) agents++;
  }
  const validation = validateWorkflowGraph(DEFAULT_WORKFLOW_GRAPH);
  if (!validation.valid) throw new ApiError(`Default workflow template is invalid: ${validation.errors.join("; ")}`, 500, "DEFAULT_WORKFLOW_INVALID");
  const workflowRows = await query(
    `insert into public.workflows (owner_id,project_id,name,slug,description,graph,is_template)
     values ($1,$2,'General Supervisor','general-supervisor','Plan, approve, execute, branch and deliver.',$3::jsonb,true)
     on conflict (owner_id,slug) do update set graph=excluded.graph,updated_at=now() returning id`,
    [ownerId, project.id, JSON.stringify(DEFAULT_WORKFLOW_GRAPH)]
  );
  if (workflowRows.length) workflows = 1;
  await emitEvent({ ownerId, kind: "workspace.bootstrap", data: { templates, agents, workflows }, projectId: project.id });
  return { ok: true, templates, agents, workflows, projectId: project.id };
}

async function createProject(ownerId: string, body: Record<string, unknown>) {
  const name = requireString(body.name, "name", 200);
  const slug = slugify(String(body.slug ?? name));
  return (await query(
    `insert into public.projects (owner_id,name,slug,description,metadata) values ($1,$2,$3,$4,$5::jsonb) returning *`,
    [ownerId, name, slug, optionalString(body.description, "description", 5000), JSON.stringify(bodyObject(body, "metadata"))]
  ))[0];
}

async function createWorkspaceItem(ownerId: string, projectId: string, body: Record<string, unknown>) {
  await assertProjectAccess(ownerId, projectId, "editor");
  const kind = String(body.kind ?? "note");
  if (!["note", "task", "conversation", "prompt", "output", "attachment", "workflow_template", "saved_output"].includes(kind)) throw new ApiError("Unsupported workspace item kind.");
  return (await query(
    `insert into public.workspace_items (owner_id,project_id,kind,title,content,status,metadata) values ($1,$2,$3,$4,$5,$6,$7::jsonb) returning *`,
    [ownerId, projectId, kind, requireString(body.title, "title", 300), optionalString(body.content, "content", 200000), optionalString(body.status, "status", 40, "open"), JSON.stringify(bodyObject(body, "metadata"))]
  ))[0];
}

async function createWorkflow(ownerId: string, body: Record<string, unknown>) {
  const graph = bodyObject(body, "graph");
  const validation = validateWorkflowGraph(graph);
  if (!validation.valid) throw new ApiError(`Workflow graph is invalid: ${validation.errors.join("; ")}`, 400, "WORKFLOW_GRAPH_INVALID", validation.errors);
  const projectId = body.projectId ? String(body.projectId) : null;
  if (projectId) await assertProjectAccess(ownerId, projectId, "editor");
  const name = requireString(body.name, "name", 200);
  return (await query(
    `insert into public.workflows (owner_id,project_id,name,slug,description,graph,is_template) values ($1,$2,$3,$4,$5,$6::jsonb,$7) returning *`,
    [ownerId, projectId, name, slugify(String(body.slug ?? name)), optionalString(body.description, "description", 5000), JSON.stringify(graph), Boolean(body.isTemplate)]
  ))[0];
}

async function patchWorkflow(ownerId: string, id: string, body: Record<string, unknown>) {
  await assertOwnerRecord("workflows", ownerId, id);
  if (body.graph) {
    const validation = validateWorkflowGraph(body.graph);
    if (!validation.valid) throw new ApiError(`Workflow graph is invalid: ${validation.errors.join("; ")}`, 400, "WORKFLOW_GRAPH_INVALID", validation.errors);
  }
  return (await query(
    `update public.workflows set name=coalesce(nullif($3,''),name),description=coalesce(nullif($4,''),description),graph=coalesce($5::jsonb,graph),updated_at=now() where id=$1 and owner_id=$2 returning *`,
    [id, ownerId, optionalString(body.name, "name", 200), optionalString(body.description, "description", 5000), body.graph ? JSON.stringify(body.graph) : null]
  ))[0];
}

async function startRun(ownerId: string, workflowId: string, body: Record<string, unknown>) {
  const workflow = await assertOwnerRecord("workflows", ownerId, workflowId);
  const mode = String(body.mode ?? "graph");
  if (!["graph", "objective"].includes(mode)) throw new ApiError("Run mode must be graph or objective.");
  const input = bodyObject(body, "input");
  const objective = optionalString(body.objective, "objective", 10000);
  if (mode === "objective" && !objective) throw new ApiError("Objective runs require an objective.");
  const graph = asObject(workflow.graph) as unknown as WorkflowGraph;
  const compiled = mode === "graph" ? compileGraphToTasks(graph, { ownerId, runId: "pending", projectId: workflow.project_id as string | null, input }) : [];
  return transaction(async (client) => {
    const runs = await client.query<{ id: string }>(
      `insert into public.workflow_runs (owner_id,workflow_id,project_id,objective,mode,status,config,input)
       values ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb) returning id`,
      [ownerId, workflowId, workflow.project_id ?? null, objective || null, mode, mode === "objective" ? "planning" : "running", JSON.stringify({ maxParallelTasks: Number(body.maxParallelTasks ?? 4) }), JSON.stringify(input)]
    );
    const runId = runs.rows[0].id;
    for (const task of compiled) {
      await client.query(
        `insert into public.tasks
          (owner_id,run_id,project_id,key,node_id,kind,status,depends_on,branch_conditions,assignee_agent_id,input,attempts,max_attempts,timeout_ms,resource_lock)
         values ($1,$2,$3,$4,$5,$6,'todo',$7::jsonb,$8::jsonb,$9,$10::jsonb,0,$11,$12,$13)`,
        [ownerId, runId, workflow.project_id ?? null, task.key, task.node_id, task.kind, JSON.stringify(task.depends_on), JSON.stringify(task.branch_conditions ?? []), task.assignee_agent_id ?? null, JSON.stringify(task.input), task.max_attempts, task.timeout_ms, task.resource_lock ?? null]
      );
    }
    await client.query("select 1");
    return { runId };
  }).then(async ({ runId }) => {
    const jobId = await enqueueJob(ownerId, "run.coordinate", { ownerId, runId });
    await emitEvent({ ownerId, kind: "run.created", data: { mode, taskCount: compiled.length, objective: objective || null }, runId, projectId: workflow.project_id as string | null });
    return jsonOk({ queued: true, runId, jobId, taskCount: compiled.length, mode, workerRequired: true }, 202);
  });
}

async function decideApproval(ownerId: string, approvalId: string, body: Record<string, unknown>) {
  const approval = (await query<Record<string, unknown>>("select * from public.approvals where id=$1 and owner_id=$2", [approvalId, ownerId]))[0];
  if (!approval) throw new ApiError("Approval not found.", 404, "NOT_FOUND");
  if (approval.status !== "pending") throw new ApiError("Approval is no longer pending.", 409, "APPROVAL_NOT_PENDING");
  const decision = String(body.decision ?? "");
  if (!["approve", "deny"].includes(decision)) throw new ApiError("Decision must be approve or deny.");
  const note = optionalString(body.note, "note", 2000);
  const payload = asObject(approval.payload);
  const toolCall = asObject(payload.toolCall);
  await query("update public.approvals set status=$3,decided_by=$4,decision_note=$5,decided_at=now() where id=$1 and owner_id=$2", [approvalId, ownerId, decision === "approve" ? "approved" : "denied", ownerId, note]);
  await emitEvent({ ownerId, kind: `approval.${decision === "approve" ? "approved" : "denied"}`, data: { approvalId, kind: approval.kind, note }, runId: approval.run_id as string | null, taskId: approval.task_id as string | null });
  if (decision === "deny") {
    if (approval.task_id) {
      await query("update public.tasks set status='blocked',output=$3::jsonb,lease_owner=null,lease_expires_at=null,updated_at=now() where id=$1 and owner_id=$2", [approval.task_id, ownerId, JSON.stringify({ reason: "approval_denied", approvalId })]);
      await enqueueCoordinateJob(ownerId, String(approval.run_id));
    }
    if (approval.kind === "bridge_confirmation" && payload.requestId) await bridgeDecision(ownerId, String(payload.integrationId ?? ""), String(payload.requestId), "deny", note);
    return { status: "denied" };
  }
  if (approval.kind === "bridge_confirmation") {
    if (!payload.requestId) throw new ApiError("Bridge approval is missing requestId.", 409, "BRIDGE_REQUEST_ID_MISSING");
    await bridgeDecision(ownerId, String(payload.integrationId ?? ""), String(payload.requestId), "approve", note);
  }
  if (approval.task_id) {
    await query("update public.tasks set status='todo',lease_owner=null,lease_expires_at=null,last_error=null,updated_at=now() where id=$1 and owner_id=$2", [approval.task_id, ownerId]);
    if (approval.kind === "human_node") {
      const jobId = await enqueueJob(ownerId, "task.execute", { ownerId, runId: String(approval.run_id), taskId: String(approval.task_id), taskKey: "approved", agentId: null, resourceLock: null });
      await enqueueCoordinateJob(ownerId, String(approval.run_id));
      return { status: "approved", taskJobId: jobId };
    }
  }
  if (toolCall.toolId) {
    const jobId = await enqueueJob(ownerId, "tool.call", {
      ...toolCall, ownerId, signature: String(payload.signature ?? ""), workerId: "approval"
    });
    if (approval.run_id) await enqueueCoordinateJob(ownerId, String(approval.run_id));
    return { status: "approved", toolJobId: jobId };
  }
  if (approval.run_id) await enqueueCoordinateJob(ownerId, String(approval.run_id));
  return { status: "approved" };
}

async function bridgeDecision(ownerId: string, integrationId: string, requestId: string, action: "approve" | "deny", note: string) {
  const { client } = await restFor(ownerId, integrationId || null);
  return client.post("/api/confirmations", { action, requestId, note, source: "foundry", remember: action === "approve" });
}

async function activity(ownerId: string, limit: number) {
  const [events, messages, approvals, audit, jobs, workers] = await Promise.all([
    recentEvents(ownerId, limit), listMessages(ownerId, { limit }),
    query("select * from public.approvals where owner_id=$1 order by created_at desc limit $2", [ownerId, limit]),
    recentAudit(ownerId, limit),
    query("select id,type,status,attempts,max_attempts,last_error,created_at,updated_at from public.durable_jobs where owner_id=$1 order by created_at desc limit $2", [ownerId, limit]),
    query("select * from public.execution_workers order by last_heartbeat desc limit 10")
  ]);
  return { events, messages, approvals, audit, jobs, workers, chain: await verifyAuditChain(ownerId) };
}

async function globalSearch(ownerId: string, term: string) {
  if (!term.trim()) return { agents: [], projects: [], workflows: [], workspaceItems: [], knowledge: [], memories: [] };
  const like = `%${term}%`;
  const [agents, projects, workflows, workspaceItems, knowledge, memories] = await Promise.all([
    query("select id,name,description,status from public.agents where owner_id=$1 and (name ilike $2 or description ilike $2) limit 20", [ownerId, like]),
    query("select id,name,description from public.projects where owner_id=$1 and (name ilike $2 or description ilike $2) limit 20", [ownerId, like]),
    query("select id,name,description from public.workflows where owner_id=$1 and (name ilike $2 or description ilike $2) limit 20", [ownerId, like]),
    query("select id,project_id,kind,title,content from public.workspace_items where owner_id=$1 and (title ilike $2 or content ilike $2) limit 20", [ownerId, like]),
    query("select id,project_id,title,source_uri from public.knowledge_documents where owner_id=$1 and (title ilike $2 or content ilike $2) limit 20", [ownerId, like]),
    query("select id,scope,agent_id,project_id,title,content from public.memories where owner_id=$1 and (title ilike $2 or content ilike $2) limit 20", [ownerId, like])
  ]);
  return { agents, projects, workflows, workspaceItems, knowledge, memories };
}

async function createIntegration(ownerId: string, body: Record<string, unknown>) {
  const name = requireString(body.name, "name", 200);
  const kind = optionalString(body.kind, "kind", 80, "tunneling");
  const transport = String(body.transport ?? "streamable_http");
  if (!["streamable_http", "stdio", "http_api"].includes(transport)) throw new ApiError("Unsupported integration transport.");
  const baseUrl = body.baseUrl ? String(body.baseUrl) : null;
  if (!baseUrl) throw new ApiError("baseUrl is required.");
  const config = bodyObject(body, "config");
  if (!config.mcpEndpoint && transport === "streamable_http") config.mcpEndpoint = `${baseUrl.replace(/\/$/, "")}/mcp`;
  if (!config.protocolVersion) config.protocolVersion = "2026-07-28";
  const rows = await query<{ id: string }>(
    `insert into public.integrations (owner_id,kind,name,transport,base_url,config,status)
     values ($1,$2,$3,$4,$5,$6::jsonb,'unconfigured') returning id`,
    [ownerId, kind, name, transport, baseUrl, JSON.stringify(config)]
  );
  const token = body.token ? String(body.token) : "";
  if (token) await setIntegrationSecret(ownerId, rows[0].id, token);
  return { id: rows[0].id, name, transport, baseUrl, secretStored: Boolean(token) };
}

async function restFor(ownerId: string, integrationId: string | null) {
  let id = integrationId;
  if (!id) {
    const rows = await query<{ id: string }>("select id from public.integrations where owner_id=$1 and kind='tunneling' order by created_at asc limit 1", [ownerId]);
    id = rows[0]?.id ?? null;
  }
  if (!id) throw new ApiError("No Tunneling integration is configured.", 503, "TUNNELING_NOT_CONFIGURED");
  return makeRestClient(ownerId, id);
}

async function tunnelingStatus(ownerId: string, integrationId: string | null) {
  let resolvedId = integrationId;
  if (!resolvedId) {
    const rows = await query<{ id: string; name: string; status: string }>("select id,name,status from public.integrations where owner_id=$1 and kind='tunneling' order by created_at asc limit 1", [ownerId]);
    if (!rows[0]) return { configured: false, studioConnected: false, reason: "No Tunneling integration has been created." };
    resolvedId = rows[0].id;
  }
  const integration = await getIntegration(ownerId, resolvedId);
  const { client } = await makeRestClient(ownerId, resolvedId);
  try {
    const [health, peers, studios, tunnel, ops, confirmations, bus] = await Promise.all([
      client.health(), client.peers(), client.studios(), client.tunnel(), client.ops(), client.confirmations(), client.bus("stats")
    ]);
    await query("update public.integrations set status=$2,last_checked_at=now(),last_error=null,updated_at=now() where id=$1 and owner_id=$3", [resolvedId, health.studio_connected === true ? "connected" : "degraded", ownerId]);
    return {
      configured: true, integration: { id: integration.id, name: integration.name, transport: integration.transport, baseUrl: integration.base_url, status: integration.status },
      health: summarizeTunnelingHealth(health), peers, studios, tunnel, ops, confirmations, bus
    };
  } catch (error) {
    const message = String(redactSecrets(error instanceof Error ? error.message : String(error))).slice(0, 500);
    await query("update public.integrations set status='error',last_checked_at=now(),last_error=$2,updated_at=now() where id=$1 and owner_id=$3", [resolvedId, message, ownerId]);
    return { configured: true, ok: false, studioConnected: false, error: message, integration: { id: integration.id, name: integration.name, status: "error" } };
  }
}

async function bridgeConfirmation(ownerId: string, body: Record<string, unknown>) {
  const action = String(body.action ?? "");
  if (!["approve", "deny", "expire"].includes(action)) throw new ApiError("Confirmation action must be approve, deny or expire.");
  const { client } = await restFor(ownerId, body.integrationId ? String(body.integrationId) : null);
  return client.post("/api/confirmations", { action, requestId: requireString(body.requestId, "requestId", 200), note: optionalString(body.note, "note", 2000), remember: Boolean(body.remember), source: "foundry" });
}

async function busAction(ownerId: string, body: Record<string, unknown>) {
  const action = String(body.action ?? "stats");
  if (action === "import") return importBusMessages(ownerId, requireString(body.integrationId, "integrationId", 100), Number(body.limit ?? 50));
  if (action === "message") return sendMessage({
    ownerId, channel: (["direct", "broadcast", "task", "artifact", "handoff", "system"].includes(String(body.channel)) ? body.channel : "task") as "direct" | "broadcast" | "task" | "artifact" | "handoff" | "system",
    content: requireString(body.content, "content", 20000), senderAgentId: body.senderAgentId ? String(body.senderAgentId) : null,
    recipientAgentId: body.recipientAgentId ? String(body.recipientAgentId) : null, projectId: body.projectId ? String(body.projectId) : null,
    runId: body.runId ? String(body.runId) : null, transport: "tunneling_bus"
  });
  const { client } = await restFor(ownerId, body.integrationId ? String(body.integrationId) : null);
  return client.post("/api/bus", redactSecrets(body));
}

async function orchestratorAction(ownerId: string, body: Record<string, unknown>) {
  const action = String(body.action ?? "status");
  const allowed = ["status", "plan", "roster", "report", "evidence", "approve", "deny"];
  if (!allowed.includes(action)) throw new ApiError("Use a durable MCP tool call for this orchestrator action. Read-only and human decisions are allowed here.", 403, "USE_DURABLE_TOOL_CALL");
  const { client } = await restFor(ownerId, body.integrationId ? String(body.integrationId) : null);
  const payload = { ...body }; delete payload.integrationId;
  return client.post("/api/orchestrator", payload);
}

async function queueTunnelingTool(ownerId: string, body: Record<string, unknown>) {
  const agentId = requireString(body.agentId, "agentId", 100);
  const input = bodyObject(body, "input");
  let toolId = body.toolId ? String(body.toolId) : "";
  if (!toolId && body.toolName) {
    const rows = await query<{ id: string }>("select id from public.tools where owner_id=$1 and name=$2 order by availability asc limit 1", [ownerId, String(body.toolName)]);
    if (!rows[0]) throw new ApiError("Tool is not registered. Run runtime MCP discovery first.", 404, "TOOL_NOT_REGISTERED");
    toolId = rows[0].id;
  }
  if (!toolId) throw new ApiError("toolId or toolName is required.");
  const result = await requestToolExecution({
    ownerId, toolId, agentId, input,
    taskId: body.taskId ? String(body.taskId) : null,
    runId: body.runId ? String(body.runId) : null,
    projectId: body.projectId ? String(body.projectId) : null
  });
  return jsonOk(result, result.status === "queued" ? 202 : 200);
}

async function evidenceResponse(ownerId: string, filename: string, integrationId: string | null): Promise<NextResponse> {
  const safe = assertSafePathSegment(filename);
  const { client } = await restFor(ownerId, integrationId);
  const result = await client.evidence(safe);
  return new NextResponse(result.bytes, { status: 200, headers: { "Content-Type": result.contentType, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
}
