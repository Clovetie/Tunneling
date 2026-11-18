import { query, transaction } from "@/lib/db";
import { emitEvent } from "@/lib/events";
import { enqueueJob } from "@/lib/jobs";
import { recordAudit } from "@/lib/audit";
import { planObjective, reviewRun } from "@/lib/orchestrator/supervisor";
import { hasResourceConflict, isTerminalStatus, taskDecision } from "@/lib/orchestrator/graph";
import { asObject, boundedJson, redactSecrets } from "@/lib/utils";
import type { TaskRecord, TaskStatus } from "@/lib/types";

type RunRow = {
  id: string; owner_id: string; workflow_id: string | null; project_id: string | null;
  objective: string | null; mode: string; status: string; config: Record<string, unknown>;
  input: Record<string, unknown>; output?: unknown; started_at?: string | null; finished_at?: string | null;
};

type TaskRow = {
  id: string; owner_id: string; run_id: string; project_id: string | null; key: string; node_id: string;
  kind: string; status: TaskStatus; depends_on: string[]; branch_conditions: Array<{edgeId:string;source:string;sourceHandle?:string|null}>;
  assignee_agent_id: string | null; input: Record<string, unknown>; output?: unknown; artifacts?: unknown;
  attempts: number; max_attempts: number; timeout_ms: number; resource_lock: string | null;
  lease_owner: string | null; lease_expires_at: string | null; last_error?: string | null;
};

function normalizeTask(row: TaskRow): TaskRow {
  return {
    ...row,
    depends_on: Array.isArray(row.depends_on) ? row.depends_on : [],
    branch_conditions: Array.isArray(row.branch_conditions) ? row.branch_conditions : [],
    input: asObject(row.input),
    config: asObject(row.input)
  } as TaskRow;
}

export async function enqueueCoordinateJob(ownerId: string, runId: string, delayMs = 0) {
  const existing = await query(
    `select id from public.durable_jobs
     where owner_id=$1 and type='run.coordinate' and status in ('queued','running') and payload->>'runId'=$2 limit 1`,
    [ownerId, runId]
  );
  if (existing.length) return null;
  return enqueueJob(ownerId, "run.coordinate", { ownerId, runId }, { maxAttempts: 4, availableAt: new Date(Date.now() + delayMs) });
}

function taskOrder(tasks: TaskRow[]): TaskRow[] {
  const byKey = new Map(tasks.map((task) => [task.key, task]));
  const indegree = new Map<string, number>();
  const adjacency = new Map<string, string[]>();
  for (const task of tasks) { indegree.set(task.key, 0); adjacency.set(task.key, []); }
  for (const task of tasks) {
    for (const dependency of task.depends_on) {
      if (!byKey.has(dependency)) continue;
      adjacency.get(dependency)!.push(task.key);
      indegree.set(task.key, (indegree.get(task.key) ?? 0) + 1);
    }
  }
  const queue = tasks.filter((task) => (indegree.get(task.key) ?? 0) === 0).map((task) => task.key);
  const ordered: string[] = [];
  while (queue.length) {
    const key = queue.shift()!;
    ordered.push(key);
    for (const target of adjacency.get(key) ?? []) {
      const next = (indegree.get(target) ?? 0) - 1;
      indegree.set(target, next);
      if (next === 0) queue.push(target);
    }
  }
  if (ordered.length !== tasks.length) throw new Error("TASK_GRAPH_CYCLE");
  return ordered.map((key) => byKey.get(key)!).filter(Boolean);
}

export async function coordinateRun(ownerId: string, runId: string, workerId = "worker") {
  const runs = await query<RunRow>("select * from public.workflow_runs where id=$1 and owner_id=$2 limit 1", [runId, ownerId]);
  const run = runs[0];
  if (!run) throw new Error("RUN_NOT_FOUND");
  if (["completed", "failed", "cancelled"].includes(run.status)) return { status: run.status, dispatched: 0 };
  run.config = asObject(run.config); run.input = asObject(run.input);

  if (run.status === "planning" || run.status === "queued" && run.mode === "objective") {
    const planned = await planObjective(ownerId, run);
    await transaction(async (client) => {
      for (const task of planned.tasks) {
        await client.query(
          `insert into public.tasks
            (owner_id, run_id, project_id, key, node_id, kind, status, depends_on, branch_conditions, assignee_agent_id, input, attempts, max_attempts, timeout_ms, resource_lock)
           values ($1,$2,$3,$4,$5,$6,'todo',$7::jsonb,$8::jsonb,$9,$10::jsonb,0,$11,$12,$13)
           on conflict (run_id,key) do nothing`,
          [ownerId, runId, run.project_id, task.key, task.node_id, task.kind, JSON.stringify(task.depends_on), JSON.stringify(task.branch_conditions), task.assignee_agent_id ?? null, JSON.stringify(task.input), task.max_attempts, task.timeout_ms, task.resource_lock ?? null]
        );
      }
      await client.query("update public.workflow_runs set status='running',started_at=coalesce(started_at,now()),updated_at=now() where id=$1 and owner_id=$2", [runId, ownerId]);
    });
    await emitEvent({ ownerId, kind: "run.planned", data: { taskCount: planned.tasks.length, model: planned.model, usage: planned.usage }, runId, projectId: run.project_id });
    await recordAudit({ ownerId, action: "run.plan", actor: "supervisor", target: runId, detail: { taskCount: planned.tasks.length, reviewMode: "model" } });
  }

  const taskRows = (await query<TaskRow>("select * from public.tasks where run_id=$1 and owner_id=$2 order by created_at asc", [runId, ownerId])).map(normalizeTask);
  if (!taskRows.length) {
    await query("update public.workflow_runs set status='failed',finished_at=now(),output=$3::jsonb,updated_at=now() where id=$1 and owner_id=$2", [runId, ownerId, JSON.stringify({ error: "No durable tasks were compiled." })]);
    await emitEvent({ ownerId, kind: "run.failed", level: "error", data: { reason: "NO_TASKS" }, runId, projectId: run.project_id });
    return { status: "failed", dispatched: 0 };
  }

  const ordered = taskOrder(taskRows);
  const byKey = new Map<string, { key: string; status: TaskStatus; output?: unknown }>();
  for (const task of ordered) byKey.set(task.key, { key: task.key, status: task.status, output: task.output });

  for (const task of ordered) {
    if (task.status !== "todo") continue;
    const decision = taskDecision({ depends_on: task.depends_on, branch_conditions: task.branch_conditions }, byKey);
    if (decision.skip) {
      await query("update public.tasks set status='skipped',output=$3::jsonb,updated_at=now() where id=$1 and owner_id=$2", [task.id, ownerId, JSON.stringify({ reason: decision.reason })]);
      byKey.set(task.key, { key: task.key, status: "skipped", output: { reason: decision.reason } });
      await emitEvent({ ownerId, kind: "task.skipped", data: { key: task.key, reason: decision.reason }, runId, taskId: task.id, projectId: task.project_id, agentId: task.assignee_agent_id });
    } else if (decision.blocked) {
      await query("update public.tasks set status='blocked',output=$3::jsonb,updated_at=now() where id=$1 and owner_id=$2", [task.id, ownerId, JSON.stringify({ reason: decision.reason })]);
      byKey.set(task.key, { key: task.key, status: "blocked", output: { reason: decision.reason } });
      await emitEvent({ ownerId, kind: "task.blocked", level: "warn", data: { key: task.key, reason: decision.reason }, runId, taskId: task.id, projectId: task.project_id, agentId: task.assignee_agent_id });
    }
  }

  const refreshed = (await query<TaskRow>("select * from public.tasks where run_id=$1 and owner_id=$2 order by created_at asc", [runId, ownerId])).map(normalizeTask);
  const byKey2 = new Map(refreshed.map((task) => [task.key, { key: task.key, status: task.status, output: task.output }]));
  const allTerminal = refreshed.every((task) => isTerminalStatus(task.status));
  if (refreshed.some((task) => task.status === "waiting_approval")) {
    await query("update public.workflow_runs set status='waiting_approval',updated_at=now() where id=$1 and owner_id=$2", [runId, ownerId]);
    await emitEvent({ ownerId, kind: "run.waiting_approval", data: { pending: refreshed.filter((task) => task.status === "waiting_approval").map((task) => task.key) }, runId, projectId: run.project_id });
    return { status: "waiting_approval", dispatched: 0 };
  }
  if (allTerminal) {
    const failed = refreshed.filter((task) => ["failed", "blocked", "cancelled"].includes(task.status));
    const status = failed.length ? "failed" : "completed";
    const review = await reviewRun(ownerId, { ...run, id: runId }, refreshed.map((task) => ({ key: task.key, kind: task.kind, status: task.status, output: task.output, last_error: task.last_error })));
    const output = redactSecrets({ status, review, tasks: refreshed.map((task) => ({ key: task.key, kind: task.kind, status: task.status, attempts: task.attempts, output: task.output, error: task.last_error })) });
    await query(
      `update public.workflow_runs set status=$3,output=$4::jsonb,finished_at=now(),updated_at=now() where id=$1 and owner_id=$2`,
      [runId, ownerId, status, JSON.stringify(output)]
    );
    await query(
      `insert into public.artifacts (owner_id,project_id,run_id,name,kind,content) values ($1,$2,$3,$4,'json',$5::jsonb)`,
      [ownerId, run.project_id, runId, `Run ${runId.slice(0, 8)} consolidated result`, boundedJson(output, 200_000)]
    );
    await query("update public.agents set status='idle',updated_at=now() where owner_id=$1 and status in ('thinking','executing','waiting_approval','completed','failed')", [ownerId]);
    await emitEvent({ ownerId, kind: status === "completed" ? "run.completed" : "run.failed", level: status === "completed" ? "info" : "error", data: { reviewMode: asObject(review).reviewMode, failed: failed.length }, runId, projectId: run.project_id });
    await recordAudit({ ownerId, action: `run.${status}`, actor: "supervisor", target: runId, outcome: status === "completed" ? "ok" : "failed", detail: { taskCount: refreshed.length, failed: failed.length } });
    return { status, dispatched: 0 };
  }

  if (refreshed.some((task) => ["failed", "blocked", "cancelled"].includes(task.status))) {
    await enqueueCoordinateJob(ownerId, runId, 400);
    return { status: "blocked", dispatched: 0 };
  }

  const maxParallel = Math.min(Math.max(Number(run.config.maxParallelTasks ?? 4), 1), 16);
  const activeJobs = await query<{ count: string }>(
    `select count(*)::text as count from public.durable_jobs
     where owner_id=$1 and type='task.execute' and status in ('queued','running') and payload->>'runId'=$2`,
    [ownerId, runId]
  );
  let slots = Math.max(0, maxParallel - Number(activeJobs[0]?.count ?? 0));
  let dispatched = 0;
  const runningTasks = refreshed.filter((task) => task.status === "running");
  for (const task of refreshed) {
    if (slots <= 0 || dispatched >= slots) break;
    if (task.status !== "todo") continue;
    const decision = taskDecision({ depends_on: task.depends_on, branch_conditions: task.branch_conditions }, byKey2);
    if (!decision.ready) continue;
    if (hasResourceConflict({ id: task.id, resource_lock: task.resource_lock }, runningTasks)) {
      await emitEvent({ ownerId, kind: "task.conflict", level: "warn", data: { key: task.key, resourceLock: task.resource_lock }, runId, taskId: task.id, projectId: task.project_id });
      continue;
    }
    if (task.resource_lock) {
      const locked = await query(
        `select id from public.durable_jobs
         where owner_id=$1 and type='task.execute' and status in ('queued','running') and payload->>'resourceLock'=$2 limit 1`,
        [ownerId, task.resource_lock]
      );
      if (locked.length) continue;
    }
    const existing = await query(
      `select id from public.durable_jobs
       where owner_id=$1 and type='task.execute' and status in ('queued','running') and payload->>'taskId'=$2 limit 1`,
      [ownerId, task.id]
    );
    if (existing.length) continue;
    if (task.assignee_agent_id) {
      const agentRows = await query<{ execution_limits: unknown }>("select execution_limits from public.agents where id=$1 and owner_id=$2", [task.assignee_agent_id, ownerId]);
      const limit = Math.max(1, Number(asObject(agentRows[0]?.execution_limits).maxParallelTasks ?? 1));
      const agentJobs = await query<{ count: string }>(
        `select count(*)::text as count from public.durable_jobs
         where owner_id=$1 and type='task.execute' and status in ('queued','running') and payload->>'agentId'=$2`,
        [ownerId, task.assignee_agent_id]
      );
      if (Number(agentJobs[0]?.count ?? 0) >= limit) continue;
    }
    const jobId = await enqueueJob(ownerId, "task.execute", {
      ownerId, runId, taskId: task.id, taskKey: task.key, agentId: task.assignee_agent_id, resourceLock: task.resource_lock
    }, { maxAttempts: task.max_attempts });
    await emitEvent({ ownerId, kind: "task.dispatched", data: { jobId, key: task.key, kind: task.kind, resourceLock: task.resource_lock }, runId, taskId: task.id, projectId: task.project_id, agentId: task.assignee_agent_id });
    dispatched++;
  }
  await query("update public.workflow_runs set status='running',started_at=coalesce(started_at,now()),updated_at=now() where id=$1 and owner_id=$2", [runId, ownerId]);
  return { status: "running", dispatched, activeJobs: Number(activeJobs[0]?.count ?? 0), taskCount: refreshed.length };
}
