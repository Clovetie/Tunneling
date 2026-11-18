import crypto from "node:crypto";
import { query, transaction } from "@/lib/db";
import { ApiError } from "@/lib/errors";
import { emitEvent } from "@/lib/events";
import { enqueueJob } from "@/lib/jobs";
import { recordAudit } from "@/lib/audit";
import { enqueueCoordinateJob } from "@/lib/orchestrator/coordinator";
import { evaluateCondition, evaluateLoop, evaluateRouter, evaluateTransform, getPath, retryDelayMs, shouldRetry } from "@/lib/orchestrator/graph";
import { runModelText, type ModelSpec } from "@/lib/ai/providers";
import { buildAgentContext, createMemory, searchMemories } from "@/lib/memory";
import type { MemoryScope } from "@/lib/types";
import { searchKnowledge } from "@/lib/knowledge";
import { listMessages } from "@/lib/communication";
import { performToolCall } from "@/lib/tools/requests";
import { getTool } from "@/lib/tools/registry";
import { discoverMcpTools } from "@/lib/tools/registry";
import { asObject, boundedJson, redactSecrets, safeJson } from "@/lib/utils";
import type { TaskStatus } from "@/lib/types";

type TaskRow = {
  id: string; owner_id: string; run_id: string; project_id: string | null; key: string; node_id: string;
  kind: string; status: TaskStatus; depends_on: string[]; branch_conditions: unknown[];
  assignee_agent_id: string | null; input: Record<string, unknown>; output?: unknown; attempts: number;
  max_attempts: number; timeout_ms: number; resource_lock: string | null; lease_owner: string | null; last_error?: string | null;
};

type AgentRow = {
  id: string; name: string; system_instructions: string; model: Record<string, unknown>;
  generation: Record<string, unknown>; execution_limits: Record<string, unknown>; status: string;
};

type NodeResult = { output: unknown; awaiting?: boolean; approvalId?: string | null; usage?: unknown };

async function claimTask(ownerId: string, taskId: string, workerId: string): Promise<TaskRow | null> {
  return transaction(async (client) => {
    const result = await client.query<TaskRow>(
      `update public.tasks
       set status='running', attempts=attempts+1, lease_owner=$3,
           lease_expires_at=now() + ((timeout_ms + 30000)::text || ' milliseconds')::interval,
           updated_at=now()
       where id=$1 and owner_id=$2 and status='todo'
       returning *`,
      [taskId, ownerId, workerId]
    );
    return result.rows[0] ?? null;
  });
}

function normalizeTask(row: TaskRow): TaskRow {
  return { ...row, depends_on: Array.isArray(row.depends_on) ? row.depends_on : [], input: asObject(row.input), model: undefined } as TaskRow;
}

async function loadContext(task: TaskRow, run: Record<string, unknown>, agent: AgentRow | null) {
  const dependencies = task.depends_on.length
    ? await query<{ key: string; status: string; output: unknown }>(
      "select key,status,output from public.tasks where run_id=$1 and owner_id=$2 and key=any($3::text[])",
      [task.run_id, task.owner_id, task.depends_on]
    )
    : [];
  const messages = await listMessages(task.owner_id, { runId: task.run_id, limit: 20 });
  const queryText = [String(run.objective ?? ""), JSON.stringify(task.input ?? {})].join(" ").slice(0, 4000);
  const memory = await buildAgentContext(task.owner_id, { agentId: task.assignee_agent_id, projectId: task.project_id, runId: task.run_id, query: queryText, limit: 5 });
  const knowledge = await searchKnowledge(task.owner_id, { query: queryText, projectId: task.project_id, limit: 5 });
  const structured = {
    run: { id: run.id, objective: run.objective, mode: run.mode, input: run.input },
    task: { key: task.key, kind: task.kind, input: task.input, attempts: task.attempts },
    agent: agent ? { id: agent.id, name: agent.name } : null,
    dependencies,
    recentMessages: messages.slice(0, 20),
    memory,
    knowledge: { mode: knowledge.mode, results: knowledge.results },
    citations: [
      ...(memory.bundles as Array<Record<string, unknown>>).flatMap((bundle) => ((bundle.results as Array<Record<string, unknown>>) ?? []).map((result) => result.citation)),
      ...knowledge.results.map((result) => result.citation)
    ].filter(Boolean)
  };
  return { structured, prompt: safeJson(redactSecrets(structured)) };
}

function modelSpecFrom(agent: AgentRow | null, config: Record<string, unknown>): ModelSpec {
  const model = asObject(agent?.model);
  const generation = asObject(agent?.generation);
  override: {
    const overrideModel = asObject(config.model);
    Object.assign(model, overrideModel);
  }
  return {
    provider: String(config.provider ?? model.provider ?? "openai"),
    model: String(config.modelName ?? (typeof config.model === "string" ? config.model : model.model) ?? "gpt-5-mini"),
    temperature: Number(config.temperature ?? generation.temperature ?? 0.2),
    maxTokens: Number(config.maxTokens ?? generation.maxTokens ?? 4096),
    topP: config.topP == null ? (generation.topP as number | undefined) : Number(config.topP),
    timeoutMs: Number(config.timeoutMs ?? 120_000)
  };
}

async function findTool(ownerId: string, config: Record<string, unknown>) {
  if (typeof config.toolId === "string") return getTool(ownerId, config.toolId);
  const name = String(config.toolName ?? config.name ?? "");
  if (!name) throw new ApiError("Tool node requires toolId or toolName.", 400, "TOOL_REFERENCE_REQUIRED");
  const rows = await query<{ id: string }>(
    `select id from public.tools where owner_id=$1 and name=$2 and ($3::uuid is null or integration_id=$3)
     order by case availability when 'available' then 0 when 'approval_required' then 1 when 'degraded' then 2 else 3 end limit 1`,
    [ownerId, name, typeof config.integrationId === "string" ? config.integrationId : null]
  );
  if (!rows[0]) throw new ApiError(`Tool ${name} is not registered. Run runtime MCP discovery first.`, 404, "TOOL_NOT_REGISTERED");
  return getTool(ownerId, rows[0].id);
}

async function humanApprovalNode(task: TaskRow, config: Record<string, unknown>, workerId: string): Promise<NodeResult> {
  const signature = crypto.createHash("sha256").update(JSON.stringify({ taskId: task.id, reason: config.reason, input: config.input ?? {} })).digest("hex");
  const approved = await query<{ id: string }>(
    "select id from public.approvals where owner_id=$1 and task_id=$2 and kind='human_node' and status='approved' and payload->>'signature'=$3 limit 1",
    [task.owner_id, task.id, signature]
  );
  if (approved[0]) return { output: { approved: true, approvalId: approved[0].id, reason: config.reason }, approvalId: approved[0].id };
  const pending = await query<{ id: string }>(
    "select id from public.approvals where owner_id=$1 and task_id=$2 and kind='human_node' and status='pending' and payload->>'signature'=$3 limit 1",
    [task.owner_id, task.id, signature]
  );
  const approvalId = pending[0]?.id ?? (await query<{ id: string }>(
    `insert into public.approvals (owner_id,run_id,task_id,kind,status,reason,payload,requested_by)
     values ($1,$2,$3,'human_node','pending',$4,$5::jsonb,$6) returning id`,
    [task.owner_id, task.run_id, task.id, String(config.reason), JSON.stringify({ signature, input: redactSecrets(config.input ?? {}), resume: { taskId: task.id, runId: task.run_id } }), workerId]
  ))[0].id;
  await query("update public.tasks set status='waiting_approval',lease_owner=null,lease_expires_at=null,updated_at=now() where id=$1 and owner_id=$2", [task.id, task.owner_id]);
  await emitEvent({ ownerId: task.owner_id, kind: "approval.requested", level: "warn", data: { approvalId, reason: config.reason, kind: "human_node" }, runId: task.run_id, taskId: task.id, projectId: task.project_id, agentId: task.assignee_agent_id });
  return { output: { approved: false, awaiting: true, approvalId }, awaiting: true, approvalId };
}

async function executeNode(task: TaskRow, run: Record<string, unknown>, agent: AgentRow | null, context: Awaited<ReturnType<typeof loadContext>>, workerId: string): Promise<NodeResult> {
  const config = task.input;
  const inputForNode = context.structured;
  if (task.kind === "human_approval") return humanApprovalNode(task, config, workerId);
  if (["agent", "llm", "supervisor"].includes(task.kind)) {
    const spec = modelSpecFrom(agent, config);
    const system = [
      agent?.system_instructions ?? "You are a FOUNDRY agent. Work only within the supplied task and declared tool boundaries.",
      typeof config.system === "string" ? config.system : "",
      "Memory and knowledge are explicitly scoped. Never mix unrelated projects or private agent contexts. Never reveal credentials."
    ].filter(Boolean).join("\n\n");
    const prompt = [
      typeof config.prompt === "string" ? config.prompt : "Execute the assigned task and return a concise, structured result.",
      "", "CONTEXT:", context.prompt
    ].join("\n");
    let lastEmit = 0;
    const result = await runModelText({
      spec, system, prompt,
      onChunk: async (_delta, accumulated) => {
        if (accumulated.length - lastEmit < 500) return;
        lastEmit = accumulated.length;
        await emitEvent({ ownerId: task.owner_id, kind: "model.chunk", data: { chars: accumulated.length, preview: redactSecrets(accumulated.slice(-240)) }, runId: task.run_id, taskId: task.id, projectId: task.project_id, agentId: task.assignee_agent_id });
      }
    });
    await emitEvent({ ownerId: task.owner_id, kind: "model.completed", data: { provider: result.provider, model: result.model, usage: result.usage, finishReason: result.finishReason }, runId: task.run_id, taskId: task.id, projectId: task.project_id, agentId: task.assignee_agent_id });
    return { output: { text: redactSecrets(result.text), provider: result.provider, model: result.model, finishReason: result.finishReason, citations: context.structured.citations }, usage: result.usage };
  }
  if (["tool", "mcp_server", "http_request"].includes(task.kind)) {
    if (!task.assignee_agent_id) throw new ApiError("Tool nodes require an assigned agent for permission checks.", 403, "AGENT_REQUIRED");
    const tool = await findTool(task.owner_id, config);
    const explicitInput = asObject(config.arguments ?? config.input);
    const toolInput = { ...(typeof config.action === "string" ? { action: config.action } : {}), ...explicitInput };
    if (typeof config.inputPath === "string") Object.assign(toolInput, asObject(getPath(inputForNode, config.inputPath)));
    try {
      const output = await performToolCall({
        ownerId: task.owner_id, toolId: tool.id, agentId: task.assignee_agent_id, input: toolInput,
        taskId: task.id, runId: task.run_id, projectId: task.project_id, workerId
      });
      return { output };
    } catch (error) {
      if (error instanceof ApiError && ["APPROVAL_REQUIRED", "BRIDGE_CONFIRMATION_REQUIRED"].includes(error.code)) {
        await query("update public.tasks set status='waiting_approval',lease_owner=null,lease_expires_at=null,updated_at=now() where id=$1 and owner_id=$2", [task.id, task.owner_id]);
        const details = asObject(error.details);
        return { output: { awaitingApproval: true, code: error.code, reason: error.message }, awaiting: true, approvalId: typeof details.approvalId === "string" ? details.approvalId : null };
      }
      throw error;
    }
  }
  if (task.kind === "condition") return { output: evaluateCondition(config, inputForNode) };
  if (task.kind === "router") return { output: evaluateRouter(config, inputForNode) };
  if (task.kind === "loop") return { output: evaluateLoop(config, inputForNode) };
  if (task.kind === "memory") {
    const operation = String(config.operation ?? "search");
    const scope = config.scope as MemoryScope | undefined;
    if (operation === "search") {
      const result = await searchMemories(task.owner_id, {
        query: String(config.query ?? ""), scope: scope ?? "agent", agentId: (config.agentId as string | undefined) ?? task.assignee_agent_id,
        projectId: task.project_id, runId: task.run_id, teamId: config.teamId as string | undefined, limit: Number(config.limit ?? 6)
      });
      return { output: result };
    }
    if (operation === "write") {
      const result = await createMemory({
        ownerId: task.owner_id, scope: scope ?? "agent", title: String(config.title ?? "Workflow memory"), content: String(config.content ?? ""),
        agentId: (config.agentId as string | undefined) ?? task.assignee_agent_id, projectId: task.project_id, runId: task.run_id,
        teamId: config.teamId as string | undefined, visibility: (config.visibility as "private" | "team" | "project" | undefined) ?? "private",
        metadata: asObject(config.metadata), createdBy: `workflow:${task.run_id}`
      });
      return { output: result };
    }
    throw new ApiError("Unsupported memory operation.", 400, "MEMORY_OPERATION_DENIED");
  }
  if (task.kind === "knowledge_retrieval") {
    const queryText = typeof config.query === "string" ? config.query : typeof config.queryPath === "string" ? String(getPath(inputForNode, config.queryPath) ?? "") : String(run.objective ?? "");
    return { output: await searchKnowledge(task.owner_id, { query: queryText, projectId: task.project_id, documentId: config.documentId as string | undefined, limit: Number(config.limit ?? 6) }) };
  }
  if (task.kind === "task") return { output: { title: String(config.title ?? task.input.label ?? task.key), status: "completed", input: config, dependencies: context.structured.dependencies } };
  if (task.kind === "output") {
    const format = String(config.format ?? "json");
    const collected = context.structured.dependencies.map((dependency) => ({ key: dependency.key, status: dependency.status, output: dependency.output }));
    return { output: { format, final: format === "text" ? collected.map((item) => safeJson(item.output)).join("\n\n") : collected, citations: context.structured.citations } };
  }
  if (config.transform) return { output: evaluateTransform(asObject(config.transform), inputForNode) };
  return { output: { completed: true, kind: task.kind, input: config } };
}

export async function executeTask(ownerId: string, taskId: string, workerId: string) {
  const claimed = await claimTask(ownerId, taskId, workerId);
  if (!claimed) return { status: "skipped", reason: "task_not_claimable" };
  const task = normalizeTask(claimed);
  const runs = await query<Record<string, unknown>>("select * from public.workflow_runs where id=$1 and owner_id=$2", [task.run_id, ownerId]);
  const run = runs[0];
  if (!run) throw new Error("RUN_NOT_FOUND");
  let agent: AgentRow | null = null;
  if (task.assignee_agent_id) {
    const rows = await query<AgentRow>("select id,name,system_instructions,model,generation,execution_limits,status from public.agents where id=$1 and owner_id=$2", [task.assignee_agent_id, ownerId]);
    agent = rows[0] ?? null;
    if (agent) await query("update public.agents set status=$3,updated_at=now() where id=$1 and owner_id=$2", [agent.id, ownerId, ["agent", "llm", "supervisor"].includes(task.kind) ? "thinking" : "executing"]);
  }
  await emitEvent({ ownerId, kind: "task.started", data: { key: task.key, kind: task.kind, attempts: task.attempts }, runId: task.run_id, taskId, projectId: task.project_id, agentId: task.assignee_agent_id });
  try {
    const context = await loadContext(task, run, agent);
    const result = await executeNode(task, run, agent, context, workerId);
    if (result.awaiting) {
      await emitEvent({ ownerId, kind: "task.waiting_approval", data: { key: task.key, approvalId: result.approvalId }, runId: task.run_id, taskId, projectId: task.project_id, agentId: task.assignee_agent_id });
      return { status: "waiting_approval", approvalId: result.approvalId };
    }
    const output = redactSecrets(result.output);
    await query(
      `update public.tasks set status='completed',output=$3::jsonb,lease_owner=null,lease_expires_at=null,last_error=null,updated_at=now()
       where id=$1 and owner_id=$2`,
      [taskId, ownerId, boundedJson(output, 200_000)]
    );
    await query(
      `insert into public.artifacts (owner_id,project_id,run_id,task_id,name,kind,content) values ($1,$2,$3,$4,$5,'json',$6::jsonb)`,
      [ownerId, task.project_id, task.run_id, task.id, `${task.key} output`, boundedJson(output, 200_000)]
    );
    if (agent) await query("update public.agents set status='idle',updated_at=now() where id=$1 and owner_id=$2", [agent.id, ownerId]);
    await emitEvent({ ownerId, kind: "task.completed", data: { key: task.key, kind: task.kind, output: boundedJson(output, 1200), usage: result.usage }, runId: task.run_id, taskId, projectId: task.project_id, agentId: task.assignee_agent_id });
    await recordAudit({ ownerId, action: "task.completed", actor: agent ? `agent:${agent.id}` : workerId, target: taskId, detail: { key: task.key, kind: task.kind, attempts: task.attempts } });
    await enqueueCoordinateJob(ownerId, task.run_id);
    return { status: "completed", key: task.key };
  } catch (error) {
    const message = String(redactSecrets(error instanceof Error ? error.message : String(error))).slice(0, 2000);
    const retry = shouldRetry({ attempts: task.attempts, max_attempts: task.max_attempts }, error);
    if (retry) {
      await query("update public.tasks set status='todo',lease_owner=null,lease_expires_at=null,last_error=$3,updated_at=now() where id=$1 and owner_id=$2", [taskId, ownerId, message]);
      await enqueueJob(ownerId, "task.execute", { ownerId, runId: task.run_id, taskId, taskKey: task.key, agentId: task.assignee_agent_id, resourceLock: task.resource_lock }, { availableAt: new Date(Date.now() + retryDelayMs(task.attempts)), maxAttempts: task.max_attempts });
      await emitEvent({ ownerId, kind: "task.retry", level: "warn", data: { key: task.key, attempts: task.attempts, error: message }, runId: task.run_id, taskId, projectId: task.project_id, agentId: task.assignee_agent_id });
      return { status: "retry", key: task.key };
    }
    await query("update public.tasks set status='failed',output=$3::jsonb,lease_owner=null,lease_expires_at=null,last_error=$4,updated_at=now() where id=$1 and owner_id=$2", [taskId, ownerId, JSON.stringify({ error: message }), message]);
    if (agent) await query("update public.agents set status='failed',updated_at=now() where id=$1 and owner_id=$2", [agent.id, ownerId]);
    await emitEvent({ ownerId, kind: "task.failed", level: "error", data: { key: task.key, error: message, attempts: task.attempts }, runId: task.run_id, taskId, projectId: task.project_id, agentId: task.assignee_agent_id });
    await recordAudit({ ownerId, action: "task.failed", actor: agent ? `agent:${agent.id}` : workerId, target: taskId, outcome: "failed", detail: { key: task.key, error: message } });
    await enqueueCoordinateJob(ownerId, task.run_id);
    return { status: "failed", key: task.key, error: message };
  }
}

export async function executeToolJob(payload: Record<string, unknown>, workerId: string) {
  const call = {
    ownerId: String(payload.ownerId),
    toolId: String(payload.toolId),
    agentId: String(payload.agentId),
    input: asObject(payload.input),
    taskId: payload.taskId ? String(payload.taskId) : null,
    runId: payload.runId ? String(payload.runId) : null,
    projectId: payload.projectId ? String(payload.projectId) : null,
    workerId,
    protocolExtras: asObject(payload.protocolExtras)
  };
  try {
    const output = await performToolCall(call);
    await emitEvent({ ownerId: call.ownerId, kind: "tool.job.completed", data: { toolId: call.toolId, output: boundedJson(redactSecrets(output), 1200) }, runId: call.runId, taskId: call.taskId, projectId: call.projectId, agentId: call.agentId });
    return output;
  } catch (error) {
    if (error instanceof ApiError && ["APPROVAL_REQUIRED", "BRIDGE_CONFIRMATION_REQUIRED"].includes(error.code)) {
      await emitEvent({ ownerId: call.ownerId, kind: "tool.job.awaiting_approval", level: "warn", data: { toolId: call.toolId, code: error.code }, runId: call.runId, taskId: call.taskId, projectId: call.projectId, agentId: call.agentId });
      return { status: "awaiting_approval", code: error.code, message: error.message };
    }
    throw error;
  }
}

export async function executeMcpRefreshJob(ownerId: string, integrationId: string) {
  return discoverMcpTools(ownerId, integrationId);
}
