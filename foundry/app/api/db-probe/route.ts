import { NextResponse } from "next/server";
import { Client } from "pg";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type ProbeResult = {
  host: string;
  port: string;
  ok: boolean;
  latencyMs?: number;
  error?: string;
};

function label(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.hostname}:${parsed.port || "5432"}`;
  } catch {
    return "invalid-url";
  }
}

function safeError(error: unknown): string {
  return String(error instanceof Error ? error.message : error).slice(0, 300);
}

export async function GET() {
  const raw = process.env.FOUNDRY_DB_PROBE_URLS;
  if (!raw) {
    return NextResponse.json({ error: "Database probe URLs are not configured." }, { status: 503 });
  }
  let urls: string[];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== "string")) throw new Error("expected a JSON string array");
    urls = parsed as string[];
  } catch (error) {
    return NextResponse.json({ error: `Invalid probe configuration: ${safeError(error)}` }, { status: 500 });
  }
  const results: ProbeResult[] = [];
  for (const url of urls.slice(0, 12)) {
    const started = Date.now();
    const client = new Client({ connectionString: url, connectionTimeoutMillis: 6000 });
    const item: ProbeResult = { host: label(url).split(":")[0], port: label(url).split(":")[1] ?? "5432", ok: false };
    try {
      await client.connect();
      await client.query("select 1 as ok");
      item.ok = true;
      item.latencyMs = Date.now() - started;
    } catch (error) {
      item.error = safeError(error);
      item.latencyMs = Date.now() - started;
    } finally {
      await client.end().catch(() => undefined);
    }
    results.push(item);
  }
  return NextResponse.json({ checked: results.length, results, configuredDatabaseUrl: Boolean(process.env.DATABASE_URL) });
}
