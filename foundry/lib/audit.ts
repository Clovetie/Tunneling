import crypto from "node:crypto";
import { query, transaction, type PoolClient } from "@/lib/db";
import { redactSecrets } from "@/lib/utils";

export type AuditEntryInput = {
  ownerId: string;
  action: string;
  actor?: string;
  target?: string;
  outcome?: string;
  detail?: unknown;
};

function canonical(value: unknown): string {
  if (value == null) return "null";
  if (typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(key)}:${canonical(object[key])}`).join(",")}}`;
}

export function computeAuditHash(prevHash: string, entry: Omit<AuditEntryInput, "ownerId">): string {
  const material = canonical({
    action: entry.action,
    actor: entry.actor ?? "user",
    target: entry.target ?? "",
    outcome: entry.outcome ?? "ok",
    detail: redactSecrets(entry.detail ?? {}),
    prevHash
  });
  return crypto.createHash("sha256").update(material).digest("hex");
}

export async function recordAudit(input: AuditEntryInput, client?: PoolClient): Promise<string> {
  const write = async (runner: PoolClient): Promise<string> => {
    await runner.query("select pg_advisory_xact_lock(hashtext($1))", [input.ownerId]);
    const previous = await runner.query<{ hash: string }>(
      "select hash from public.audit_logs where owner_id = $1 order by id desc limit 1",
      [input.ownerId]
    );
    const prevHash = previous.rows[0]?.hash ?? "0".repeat(64);
    const entry = {
      action: input.action,
      actor: input.actor ?? "user",
      target: input.target ?? "",
      outcome: input.outcome ?? "ok",
      detail: redactSecrets(input.detail ?? {})
    };
    const hash = computeAuditHash(prevHash, entry);
    await runner.query(
      `insert into public.audit_logs (owner_id, action, actor, target, outcome, detail, prev_hash, hash)
       values ($1,$2,$3,$4,$5,$6::jsonb,$7,$8)`,
      [input.ownerId, entry.action, entry.actor, entry.target, entry.outcome, JSON.stringify(entry.detail), prevHash, hash]
    );
    return hash;
  };
  if (client) return write(client);
  return transaction(write);
}

export async function verifyAuditChain(ownerId: string): Promise<{ valid: boolean; count: number; firstMismatchId?: number }> {
  const rows = await query<{ id: number; action: string; actor: string; target: string; outcome: string; detail: unknown; prev_hash: string; hash: string }>(
    "select id, action, actor, target, outcome, detail, prev_hash, hash from public.audit_logs where owner_id = $1 order by id asc",
    [ownerId]
  );
  let previous = "0".repeat(64);
  for (const row of rows) {
    const expected = computeAuditHash(previous, { action: row.action, actor: row.actor, target: row.target, outcome: row.outcome, detail: row.detail });
    if (expected !== row.hash || row.prev_hash !== previous) return { valid: false, count: rows.length, firstMismatchId: row.id };
    previous = row.hash;
  }
  return { valid: true, count: rows.length };
}

export async function recentAudit(ownerId: string, limit = 50) {
  return query(
    "select id, action, actor, target, outcome, detail, prev_hash, hash, created_at from public.audit_logs where owner_id = $1 order by id desc limit $2",
    [ownerId, Math.min(Math.max(limit, 1), 200)]
  );
}
