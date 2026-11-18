import { databaseHealth, isDatabaseConfigured, query } from "@/lib/db";
import { jsonOk } from "@/lib/errors";
import { getPublicConfig } from "@/lib/public-config";
import { configuredProviders } from "@/lib/ai/providers";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const config = getPublicConfig();
  const database = isDatabaseConfigured() ? await databaseHealth() : { ok: false, latencyMs: 0, error: "DATABASE_URL not configured" };
  let migration = false;
  let worker = false;
  if (database.ok) {
    try {
      const result = await query<{ present: boolean }>("select to_regclass('public.agents') is not null as present");
      migration = Boolean(result[0]?.present);
      const workers = await query<{ online: boolean }>("select exists(select 1 from public.execution_workers where status='online' and last_heartbeat > now() - interval '30 seconds') as online");
      worker = Boolean(workers[0]?.online);
    } catch { migration = false; }
  }
  return jsonOk({
    ok: config.authConfigured && database.ok && migration && config.encryptionConfigured,
    app: "FOUNDRY",
    version: config.version,
    auth: config.authConfigured,
    database,
    migration,
    realtime: config.realtimeConfigured,
    encryption: config.encryptionConfigured,
    worker,
    providers: configuredProviders(),
    mode: config.mode
  });
}
