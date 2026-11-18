import "dotenv/config";
import { databaseHealth, isDatabaseConfigured, query } from "@/lib/db";
import { claimJob, completeJob, failJob, recoverStaleJobs, recoverStaleTasks, workerHeartbeat, type JobRow } from "@/lib/jobs";
import { coordinateRun } from "@/lib/orchestrator/coordinator";
import { executeMcpRefreshJob, executeTask, executeToolJob } from "@/lib/orchestrator/executor";
import { asObject, redactSecrets } from "@/lib/utils";

const WORKER_ID = process.env.FOUNDRY_WORKER_ID || `foundry-worker-${process.pid}`;
const POLL_MS = Math.max(250, Number(process.env.FOUNDRY_WORKER_POLL_MS || 1000));
const LEASE_SECONDS = Math.max(15, Number(process.env.FOUNDRY_JOB_LEASE_SECONDS || 90));

function sleep(ms: number) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function dispatch(job: JobRow): Promise<unknown> {
  const payload = asObject(job.payload);
  const ownerId = String(payload.ownerId ?? job.owner_id);
  switch (job.type) {
    case "run.workflow":
    case "run.coordinate":
    case "approval.continue":
      return coordinateRun(ownerId, String(payload.runId), WORKER_ID);
    case "task.execute":
      return executeTask(ownerId, String(payload.taskId), WORKER_ID);
    case "tool.call":
      return executeToolJob(payload, WORKER_ID);
    case "mcp.refresh":
      return executeMcpRefreshJob(ownerId, String(payload.integrationId));
    default:
      throw new Error(`UNSUPPORTED_JOB_TYPE:${job.type}`);
  }
}

async function shutdown() {
  await query("update public.execution_workers set status='offline',last_heartbeat=now() where id=$1", [WORKER_ID]).catch(() => undefined);
  process.exit(0);
}

async function main() {
  if (!isDatabaseConfigured()) {
    console.error("[foundry:worker] DATABASE_URL is not configured. The worker needs persistent Postgres; no in-memory fallback is provided.");
    process.exit(1);
  }
  const health = await databaseHealth();
  if (!health.ok) {
    console.error("[foundry:worker] database connection failed", redactSecrets(health.error));
    process.exit(1);
  }
  await workerHeartbeat(WORKER_ID, { pid: process.pid, node: process.version, leaseSeconds: LEASE_SECONDS });
  setInterval(() => { void workerHeartbeat(WORKER_ID, { pid: process.pid, node: process.version }).catch((error) => console.error("[foundry:worker] heartbeat failed", redactSecrets(error instanceof Error ? error.message : String(error)))); }, 15_000).unref();
  process.on("SIGTERM", () => { void shutdown(); });
  process.on("SIGINT", () => { void shutdown(); });
  console.log(`[foundry:worker] ${WORKER_ID} online; polling durable_jobs every ${POLL_MS}ms`);
  let cycle = 0;
  while (true) {
    try {
      cycle++;
      if (cycle % 10 === 0) {
        const jobs = await recoverStaleJobs();
        const tasks = await recoverStaleTasks();
        if (jobs || tasks) console.log(`[foundry:worker] recovered ${jobs} jobs and ${tasks} stale task leases`);
      }
      const job = await claimJob(WORKER_ID, LEASE_SECONDS);
      if (!job) { await sleep(POLL_MS); continue; }
      const started = Date.now();
      try {
        const result = await dispatch(job);
        await completeJob(job.id, WORKER_ID);
        console.log(`[foundry:worker] ${job.type} ${job.id.slice(0, 8)} ok in ${Date.now() - started}ms`, redactSecrets(result));
      } catch (error) {
        const outcome = await failJob(job, error, WORKER_ID);
        console.error(`[foundry:worker] ${job.type} ${job.id.slice(0, 8)} ${outcome}`, redactSecrets(error instanceof Error ? error.message : String(error)));
        if (outcome === "retry") await sleep(250);
      }
    } catch (error) {
      console.error("[foundry:worker] loop error", redactSecrets(error instanceof Error ? error.message : String(error)));
      await sleep(Math.min(POLL_MS * 5, 5000));
    }
  }
}

void main().catch((error) => {
  console.error("[foundry:worker] fatal", redactSecrets(error));
  process.exit(1);
});
