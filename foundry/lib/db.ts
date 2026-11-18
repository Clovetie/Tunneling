import { Pool, type PoolClient, type QueryResultRow } from "pg";
import { redactSecrets } from "@/lib/utils";
export type { PoolClient } from "pg";

type GlobalWithPool = typeof globalThis & { __foundryPool?: Pool };

export function databaseUrl(): string {
  return (process.env.DATABASE_URL || process.env.SUPABASE_DB_URL || "").trim();
}

export function isDatabaseConfigured(): boolean {
  return databaseUrl().length > 0;
}

export function getPool(): Pool {
  const connectionString = databaseUrl();
  if (!connectionString) throw new Error("DATABASE_NOT_CONFIGURED");
  const globalWithPool = globalThis as GlobalWithPool;
  if (!globalWithPool.__foundryPool) {
    globalWithPool.__foundryPool = new Pool({
      connectionString,
      max: Number(process.env.PGPOOL_MAX || 8),
      idleTimeoutMillis: 20_000,
      connectionTimeoutMillis: 8_000,
      allowExitOnIdle: true
    });
    globalWithPool.__foundryPool.on("error", (error) => {
      console.error("[foundry:pg] idle client error", error.message);
    });
  }
  return globalWithPool.__foundryPool;
}

export async function query<T extends QueryResultRow = QueryResultRow>(sql: string, values: unknown[] = [], client?: PoolClient): Promise<T[]> {
  if (!client && !isDatabaseConfigured()) throw new Error("DATABASE_NOT_CONFIGURED");
  const runner = client ?? getPool();
  const result = await runner.query<T>(sql, values);
  return result.rows;
}

export async function transaction<T>(fn: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await getPool().connect();
  try {
    await client.query("BEGIN");
    const value = await fn(client);
    await client.query("COMMIT");
    return value;
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

export async function databaseHealth(): Promise<{ ok: boolean; latencyMs: number; error?: string }> {
  const started = Date.now();
  try {
    await query("select 1 as ok");
    return { ok: true, latencyMs: Date.now() - started };
  } catch (error) {
    return { ok: false, latencyMs: Date.now() - started, error: String(redactSecrets(error instanceof Error ? error.message : String(error))).slice(0, 500) };
  }
}
