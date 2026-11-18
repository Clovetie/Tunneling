import "dotenv/config";
import fs from "node:fs";
import path from "node:path";
import { getPool, isDatabaseConfigured } from "@/lib/db";

async function main() {
  if (!isDatabaseConfigured()) throw new Error("DATABASE_URL is required. Set the Supabase Postgres connection string server-side.");
  const migration = fs.readFileSync(path.resolve("supabase/migrations/0001_foundry.sql"), "utf8");
  const pool = getPool();
  console.log("[foundry:migrate] applying 0001_foundry.sql");
  await pool.query(migration);
  console.log("[foundry:migrate] done");
  await pool.end();
}

void main().catch((error) => { console.error("[foundry:migrate] failed", error instanceof Error ? error.message : String(error)); process.exit(1); });
