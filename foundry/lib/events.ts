import { query, type PoolClient } from "@/lib/db";
import { boundedJson, redactSecrets } from "@/lib/utils";

export type EventInput = {
  ownerId: string;
  kind: string;
  level?: "debug" | "info" | "warn" | "error";
  data?: unknown;
  projectId?: string | null;
  runId?: string | null;
  taskId?: string | null;
  agentId?: string | null;
};

export async function emitEvent(input: EventInput, client?: PoolClient): Promise<number> {
  const rows = await query<{ id: string }>(
    `insert into public.events (owner_id, project_id, run_id, task_id, agent_id, kind, level, data)
     values ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) returning id`,
    [
      input.ownerId, input.projectId ?? null, input.runId ?? null, input.taskId ?? null, input.agentId ?? null,
      input.kind, input.level ?? "info", boundedJson(redactSecrets(input.data ?? {}), 20_000)
    ],
    client
  );
  return Number(rows[0]?.id ?? 0);
}

export async function recentEvents(ownerId: string, limit = 80, cursor?: number, runId?: string) {
  return query(
    `select id, event_id, kind, level, data, project_id, run_id, task_id, agent_id, created_at
     from public.events
     where owner_id = $1 and ($2::bigint is null or id < $2) and ($3::uuid is null or run_id = $3)
     order by id desc limit $4`,
    [ownerId, cursor ?? null, runId ?? null, Math.min(Math.max(limit, 1), 300)]
  );
}
