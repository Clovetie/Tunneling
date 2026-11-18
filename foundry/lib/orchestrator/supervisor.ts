import { z } from "zod";
import { query } from "@/lib/db";
import { runModelJson, configuredProviders } from "@/lib/ai/providers";
import { emitEvent } from "@/lib/events";
import type { TaskRecord } from "@/lib/types";
import { topologicalOrder } from "@/lib/orchestrator/graph";
import { redactSecrets } from "@/lib/utils";

const planSchema = z.object({
  summary: z.string().min(1).max(2000),
  review: z.string().min(1).max(2000),
  tasks: z.array(z.object({
    key: z.string().min(1).max(120),
    title: z.string().min(1).max(300),
    kind: z.string().min(1).max(40).default("task"),
    assigneeAgentId: z.string().nullable().optional(),
    dependsOn: z.array(z.string()).max(20).default([]),
    input: z.record(z.unknown()).default({}),
    maxAttempts: z.number().int().min(1).max(8).optional(),
    timeoutMs: z.number().int().min(1000).max(900000).optional(),
    resourceLock: z.string().nullable().optional()
  })).min(1).max(30)
});

type SupervisorPlan = z.infer<typeof planSchema>;

function supervisorSpec() {
  const providers = configuredProviders();
  const provider = process.env.FOUNDRY_SUPERVISOR_PROVIDER || providers[0];
  if (!provider) throw new Error("MODEL_PROVIDER_UNAVAILABLE: no provider key is configured for supervisor planning");
  const defaults: Record<string, string> = {
    openai: "gpt-5-mini", anthropic: "claude-sonnet-4-5", google: "gemini-2.5-pro", openai_compatible: "gpt-4o-mini"
  };
  return { provider, model: process.env.FOUNDRY_SUPERVISOR_MODEL || defaults[provider] || "gpt-5-mini", temperature: 0.1, maxTokens: 8000, timeoutMs: 120_000 };
}

export async function planObjective(ownerId: string, run: { id: string; objective: string | null; input: unknown; project_id?: string | null }) {
  const agents = await query<{ id: string; name: string; description: string; model: unknown; tool_permissions: unknown; execution_limits: unknown; status: string }>(
    "select id,name,description,model,tool_permissions,execution_limits,status from public.agents where owner_id=$1 and enabled=true order by created_at asc",
    [ownerId]
  );
  if (!agents.length) throw new Error("SUPERVISOR_NO_AGENTS: register at least one enabled agent before objective planning");
  const spec = supervisorSpec();
  const prompt = [
    "You are FOUNDRY's supervisor. Convert the objective into a durable execution plan.",
    "Select agents by their declared responsibilities. Respect dependencies, parallelism opportunities, resource locks, failure handling and human approval needs.",
    "Return ONLY a JSON object with keys summary, review, tasks.",
    "Each task key must be unique; dependsOn must refer to earlier task keys; assigneeAgentId must be one of the roster ids or null for deterministic work.",
    "Do not invent tools or credentials. Do not include secrets.",
    "",
    `Objective: ${run.objective ?? "(none)"}`,
    `Run input: ${JSON.stringify(run.input ?? {}).slice(0, 8000)}`,
    `Agent roster: ${JSON.stringify(agents.map((agent) => ({ id: agent.id, name: agent.name, description: agent.description, model: agent.model, toolPermissions: agent.tool_permissions, executionLimits: agent.execution_limits }))).slice(0, 12000)}`
  ].join("\n");
  const result = await runModelJson<SupervisorPlan>({
    spec,
    system: "You produce strict JSON execution plans for a durable multi-agent orchestrator. Never produce prose outside JSON.",
    prompt
  });
  const plan = planSchema.parse(result.value);
  const agentIds = new Set(agents.map((agent) => agent.id));
  const keys = new Set<string>();
  for (const task of plan.tasks) {
    if (keys.has(task.key)) throw new Error("SUPERVISOR_PLAN_INVALID: duplicate task key");
    keys.add(task.key);
    if (task.assigneeAgentId && !agentIds.has(task.assigneeAgentId)) throw new Error("SUPERVISOR_PLAN_INVALID: unknown assigneeAgentId");
    for (const dependency of task.dependsOn) if (!keys.has(dependency)) throw new Error("SUPERVISOR_PLAN_INVALID: dependency must refer to an earlier task");
  }
  topologicalOrder({ nodes: plan.tasks.map((task, index) => ({ id: task.key, type: "task", position: { x: 0, y: index * 80 }, data: { label: task.title, config: {} } })), edges: plan.tasks.flatMap((task) => task.dependsOn.map((source, index) => ({ id: `${task.key}-${source}-${index}`, source, target: task.key }))) });
  await emitEvent({ ownerId, kind: "supervisor.plan", data: { summary: plan.summary, taskCount: plan.tasks.length, usage: result.usage, provider: spec.provider, model: spec.model }, runId: run.id, projectId: run.project_id ?? null });
  const tasks: TaskRecord[] = plan.tasks.map((task) => ({
    run_id: run.id,
    key: task.key,
    node_id: task.key,
    kind: (task.kind === "supervisor" || task.kind === "tool" ? task.kind : "task") as TaskRecord["kind"],
    status: "todo",
    depends_on: task.dependsOn,
    branch_conditions: [],
    assignee_agent_id: task.assigneeAgentId ?? null,
    input: { ...task.input, title: task.title, supervisor: true },
    attempts: 0,
    max_attempts: task.maxAttempts ?? 2,
    timeout_ms: task.timeoutMs ?? 120_000,
    resource_lock: task.resourceLock ?? null
  }));
  return { plan, tasks, usage: result.usage, model: { provider: spec.provider, model: spec.model } };
}

export async function reviewRun(ownerId: string, run: Record<string, unknown>, tasks: Array<Record<string, unknown>>): Promise<Record<string, unknown>> {
  const deterministic = {
    reviewMode: "deterministic",
    summary: `Run ${String(run.id).slice(0, 8)} completed with ${tasks.length} durable task records.`,
    deliverable: tasks.filter((task) => task.status === "completed").map((task) => ({ key: task.key, output: task.output })),
    risks: tasks.filter((task) => ["failed", "blocked", "skipped"].includes(String(task.status))).map((task) => ({ key: task.key, status: task.status, error: task.last_error }))
  };
  const providers = configuredProviders();
  if (!providers.length) return { ...deterministic, note: "No model provider key is configured; review is deterministic, not model-generated." };
  try {
    const spec = supervisorSpec();
    const result = await runModelJson<{ summary: string; deliverable: string; risks: string[] }>({
      spec: { ...spec, temperature: 0, maxTokens: 4000 },
      system: "You are a strict supervisor reviewer. Return JSON with summary, deliverable, risks. Cite task keys. Do not invent results.",
      prompt: `Objective: ${String(run.objective ?? "Graph workflow")}\nTasks: ${JSON.stringify(tasks.map((task) => ({ key: task.key, kind: task.kind, status: task.status, output: task.output, error: task.last_error }))).slice(0, 20_000)}`
    });
    return { ...result.value, reviewMode: "model", provider: spec.provider, model: spec.model, usage: redactSecrets(result.usage) };
  } catch (error) {
    return { ...deterministic, note: `Model review unavailable; deterministic consolidation used. ${String(redactSecrets(error instanceof Error ? error.message : String(error))).slice(0, 180)}` };
  }
}
