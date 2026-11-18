import { query, type PoolClient } from "@/lib/db";
import type { DurableJob } from "@/lib/types";
import { boundedJson, redactSecrets } from "@/lib/utils";

export type JobRow = Omit<DurableJob, "type"> & {
  owner_id: string;
  type: DurableJob["type"];
  available_at: string;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
};

export async function enqueueJob(
  ownerId: string,
  type: DurableJob["type"],
  payload: Record<string, unknown>,
  options: { maxAttempts?: number; availableAt?: Date; client?: PoolClient } = {}
): Promise<string> {
  const rows = await query<{ id: string }>(
    `insert into public.durable_jobs (owner_id, type, payload, max_attempts, available_at)
     values ($1,$2,$3::jsonb,$4,$5) returning id`,
    [ownerId, type, boundedJson(payload, 100_000), options.maxAttempts ?? 3, options.availableAt ?? new Date()],
    options.client
  );
  return rows[0].id;
}

export async function claimJob(workerId: string, leaseSeconds = 90): Promise<JobRow | null> {
  const rows = await query<JobRow>(
    `with candidate as (
       select id from public.durable_jobs
       where status = 'queued' and available_at <= now()
       order by available_at asc, created_at asc
       for update skip locked limit 1
     )
     update public.durable_jobs j
       set status = 'running', attempts = j.attempts + 1, lease_owner = $1,
           lease_expires_at = now() + ($2::text || ' seconds')::interval,
           updated_at = now()
     from candidate c
     where j.id = c.id
     returning j.*`,
    [workerId, String(Math.max(5, leaseSeconds))]
  );
  return rows[0] ?? null;
}

export async function completeJob(jobId: string, workerId: string): Promise<void> {
  await query(
    `update public.durable_jobs set status='done', lease_owner=null, lease_expires_at=null, last_error=null, updated_at=now()
     where id=$1 and lease_owner=$2`,
    [jobId, workerId]
  );
}

export async function failJob(job: JobRow, error: unknown, workerId: string): Promise<"retry" | "failed"> {
  const message = redactSecrets(error instanceof Error ? error.message : String(error));
  if (job.attempts < job.max_attempts) {
    const backoffSeconds = Math.min(60, 2 ** Math.max(0, job.attempts - 1));
    await query(
      `update public.durable_jobs
       set status='queued', lease_owner=null, lease_expires_at=null, last_error=$2,
           available_at=now() + ($3::text || ' seconds')::interval, updated_at=now()
       where id=$1 and lease_owner=$4`,
      [job.id, message, String(backoffSeconds), workerId]
    );
    return "retry";
  }
  await query(
    `update public.durable_jobs set status='failed', lease_owner=null, lease_expires_at=null, last_error=$2, updated_at=now()
     where id=$1 and lease_owner=$3`,
    [job.id, message, workerId]
  );
  return "failed";
}

export async function recoverStaleJobs(): Promise<number> {
  const rows = await query(
    `update public.durable_jobs
     set status='queued', lease_owner=null, lease_expires_at=null, available_at=now(), updated_at=now()
     where status='running' and lease_expires_at is not null and lease_expires_at < now() returning id`
  );
  return Array.isArray(rows) ? rows.length : 0;
}

export async function recoverStaleTasks(): Promise<number> {
  const rows = await query(
    `update public.tasks
     set status='todo', lease_owner=null, lease_expires_at=null, updated_at=now()
     where status='running' and lease_expires_at is not null and lease_expires_at < now() returning id`
  );
  return Array.isArray(rows) ? rows.length : 0;
}

export async function workerHeartbeat(workerId: string, metadata: Record<string, unknown> = {}): Promise<void> {
  await query(
    `insert into public.execution_workers (id,status,last_heartbeat,metadata)
     values ($1,'online',now(),$2::jsonb)
     on conflict (id) do update set status='online', last_heartbeat=now(), metadata=$2::jsonb`,
    [workerId, boundedJson(metadata, 20_000)]
  );
}
