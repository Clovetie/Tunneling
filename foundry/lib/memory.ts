import { query, transaction } from "@/lib/db";
import { ApiError } from "@/lib/errors";
import { assertAgentAccess, assertProjectAccess, assertRunAccess } from "@/lib/access";
import { embedText, toVectorLiteral, embeddingStatus } from "@/lib/embeddings";
import { redactSecrets } from "@/lib/utils";
import type { MemoryScope } from "@/lib/types";

export type MemoryFilter = {
  scope?: MemoryScope;
  agentId?: string | null;
  projectId?: string | null;
  runId?: string | null;
  teamId?: string | null;
  visibility?: string | null;
};

export type MemoryInput = {
  ownerId: string;
  scope: MemoryScope;
  title: string;
  content: string;
  agentId?: string | null;
  projectId?: string | null;
  runId?: string | null;
  teamId?: string | null;
  visibility?: "private" | "team" | "project";
  metadata?: Record<string, unknown>;
  createdBy?: string;
};

export function validateMemoryScope(filter: MemoryFilter, requireScope = true): MemoryFilter | null {
  if (requireScope && !filter.scope) throw new ApiError("A memory scope is required for retrieval.", 400, "MEMORY_SCOPE_REQUIRED");
  const scope = filter.scope;
  if (scope === "agent" && !filter.agentId) throw new ApiError("Agent memory requires an agentId.", 400, "MEMORY_AGENT_REQUIRED");
  if (scope === "project" && !filter.projectId) throw new ApiError("Project memory requires a projectId.", 400, "MEMORY_PROJECT_REQUIRED");
  if (scope === "team" && (!filter.projectId || !filter.teamId)) throw new ApiError("Team memory requires both projectId and teamId.", 400, "MEMORY_TEAM_REQUIRED");
  if (scope === "workflow" && !filter.runId) throw new ApiError("Workflow memory requires a runId.", 400, "MEMORY_RUN_REQUIRED");
  return filter;
}

export async function assertMemoryScopeAccess(ownerId: string, filter: MemoryFilter): Promise<void> {
  if (filter.agentId) await assertAgentAccess(ownerId, filter.agentId);
  if (filter.projectId) await assertProjectAccess(ownerId, filter.projectId, "viewer");
  if (filter.runId) await assertRunAccess(ownerId, filter.runId);
}

export function buildMemoryScopeSql(ownerId: string, filter: MemoryFilter, startIndex = 2): { sql: string; params: unknown[] } {
  const params: unknown[] = [ownerId];
  while (params.length < startIndex - 1) params.push(null);
  const clauses: string[] = ["owner_id = $1"];
  const next = () => { params.push(null); return params.length; };
  const add = (value: unknown) => { const index = next(); params[index - 1] = value; return `$${index}`; };
  if (filter.scope) clauses.push(`scope = ${add(filter.scope)}`);
  if (filter.scope === "agent") {
    clauses.push(`agent_id = ${add(filter.agentId ?? null)}`);
    clauses.push(`(visibility = 'private' or (visibility <> 'private' and metadata->>'explicitShare' = 'true'))`);
  } else if (filter.scope === "project") {
    clauses.push(`project_id = ${add(filter.projectId ?? null)}`);
    clauses.push(`visibility in ('private','project','team')`);
  } else if (filter.scope === "team") {
    clauses.push(`project_id = ${add(filter.projectId ?? null)}`);
    clauses.push(`team_id = ${add(filter.teamId ?? null)}`);
    clauses.push(`visibility in ('team','project')`);
  } else if (filter.scope === "workflow") {
    clauses.push(`run_id = ${add(filter.runId ?? null)}`);
    if (filter.projectId) clauses.push(`project_id = ${add(filter.projectId)}`);
  }
  if (filter.visibility) clauses.push(`visibility = ${add(filter.visibility)}`);
  return { sql: clauses.join(" and "), params };
}

export async function listMemories(ownerId: string, filter: MemoryFilter, limit = 100) {
  validateMemoryScope(filter, false);
  await assertMemoryScopeAccess(ownerId, filter);
  const { sql, params } = buildMemoryScopeSql(ownerId, filter);
  return query(
    `select id, scope, agent_id, project_id, run_id, team_id, title, content, visibility, metadata, version, created_by, created_at, updated_at
     from public.memories where ${sql} order by updated_at desc limit $${params.length + 1}`,
    [...params, Math.min(Math.max(limit, 1), 300)]
  );
}

export async function createMemory(input: MemoryInput) {
  const filter: MemoryFilter = { scope: input.scope, agentId: input.agentId, projectId: input.projectId, runId: input.runId, teamId: input.teamId };
  validateMemoryScope(filter, true);
  await assertMemoryScopeAccess(input.ownerId, filter);
  const visibility = input.visibility ?? "private";
  const metadata = { ...(input.metadata ?? {}) };
  if (input.scope === "agent" && visibility !== "private" && metadata.explicitShare !== true) {
    throw new ApiError("Sharing private agent memory requires metadata.explicitShare=true.", 403, "EXPLICIT_SHARE_REQUIRED");
  }
  const embedding = await embedText(`${input.title}\n${input.content}`);
  const rows = await query<{ id: string; version: number }>(
    `insert into public.memories
      (owner_id, scope, agent_id, project_id, run_id, team_id, title, content, visibility, embedding, metadata, created_by)
     values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::vector,$11::jsonb,$12)
     returning id, version`,
    [
      input.ownerId, input.scope, input.agentId ?? null, input.projectId ?? null, input.runId ?? null, input.teamId ?? null,
      input.title, input.content, visibility, embedding.vector ? toVectorLiteral(embedding.vector) : null,
      JSON.stringify({ ...metadata, embeddingMode: embedding.mode, embeddingModel: embedding.model, embeddingReason: embedding.reason ?? null }),
      input.createdBy ?? "user"
    ]
  );
  return { ...rows[0], embeddingMode: embedding.mode, embeddingReason: embedding.reason ?? null };
}

export async function updateMemory(ownerId: string, memoryId: string, patch: { title?: string; content?: string; metadata?: Record<string, unknown>; visibility?: "private" | "team" | "project"; changeNote?: string }) {
  const existing = await query<Record<string, unknown>>("select * from public.memories where id=$1 and owner_id=$2 limit 1", [memoryId, ownerId]);
  const row = existing[0];
  if (!row) throw new ApiError("Memory not found.", 404, "NOT_FOUND");
  const visibility = patch.visibility ?? (row.visibility as MemoryInput["visibility"]) ?? "private";
  const metadata = { ...((row.metadata as Record<string, unknown>) ?? {}), ...(patch.metadata ?? {}) };
  if (row.scope === "agent" && visibility !== "private" && metadata.explicitShare !== true) {
    throw new ApiError("Sharing private agent memory requires metadata.explicitShare=true.", 403, "EXPLICIT_SHARE_REQUIRED");
  }
  if (patch.changeNote) metadata.changeNote = patch.changeNote;
  const title = patch.title ?? String(row.title);
  const content = patch.content ?? String(row.content);
  const changed = title !== row.title || content !== row.content || JSON.stringify(metadata) !== JSON.stringify(row.metadata ?? {});
  const embedding = changed ? await embedText(`${title}\n${content}`) : null;
  const rows = await query<{ version: number }>(
    `update public.memories
     set title=$3, content=$4, visibility=$5, metadata=$6::jsonb,
         embedding=coalesce($7::vector, embedding)
     where id=$1 and owner_id=$2
     returning version`,
    [
      memoryId, ownerId, title, content, visibility,
      JSON.stringify({ ...metadata, ...(embedding ? { embeddingMode: embedding.mode, embeddingModel: embedding.model, embeddingReason: embedding.reason ?? null } : {}) }),
      embedding?.vector ? toVectorLiteral(embedding.vector) : null
    ]
  );
  return { id: memoryId, version: rows[0]?.version ?? Number(row.version), embeddingMode: embedding?.mode ?? "unchanged" };
}

export async function deleteMemory(ownerId: string, memoryId: string) {
  const rows = await query("delete from public.memories where id=$1 and owner_id=$2 returning id", [memoryId, ownerId]);
  return { deleted: rows.length > 0 };
}

export async function memoryVersions(ownerId: string, memoryId: string) {
  return query(
    "select id, version, title, content, metadata, change_note, created_at from public.memory_versions where memory_id=$1 and owner_id=$2 order by version desc",
    [memoryId, ownerId]
  );
}

export async function searchMemories(ownerId: string, search: { query: string; limit?: number } & MemoryFilter) {
  const filter = validateMemoryScope(search, true)!;
  await assertMemoryScopeAccess(ownerId, filter);
  const limit = Math.min(Math.max(search.limit ?? 8, 1), 30);
  const embedding = await embedText(search.query);
  if (embedding.vector) {
    const rows = await query<Record<string, unknown>>(
      `select * from public.match_memories($1::vector,$2,$3,$4,$5,$6,$7,$8)`,
      [toVectorLiteral(embedding.vector), limit, ownerId, filter.scope ?? null, filter.agentId ?? null, filter.projectId ?? null, filter.runId ?? null, filter.teamId ?? null]
    );
    return {
      mode: "vector",
      embedding: { provider: embedding.provider, model: embedding.model },
      results: rows.map((row) => ({ ...row, citation: { type: "memory", id: String(row.id), title: String(row.title), version: Number(row.version), similarity: Number(row.similarity) } }))
    };
  }
  const { sql, params } = buildMemoryScopeSql(ownerId, filter);
  const likeIndex = params.length + 1;
  const rows = await query<Record<string, unknown>>(
    `select id, scope, agent_id, project_id, run_id, title, content, version, metadata,
            case when title ilike $${likeIndex} then 0.72 else 0.58 end as similarity
     from public.memories where ${sql} and (content ilike $${likeIndex} or title ilike $${likeIndex})
     order by similarity desc, updated_at desc limit $${likeIndex + 1}`,
    [...params, `%${search.query}%`, limit]
  );
  return {
    mode: "lexical",
    embedding: { ...embeddingStatus(), reason: embedding.reason },
    results: rows.map((row) => ({ ...row, citation: { type: "memory", id: String(row.id), title: String(row.title), version: Number(row.version), similarity: Number(row.similarity) } }))
  };
}

export async function buildAgentContext(ownerId: string, opts: { agentId?: string | null; projectId?: string | null; runId?: string | null; query: string; limit?: number }) {
  const bundles: Array<Record<string, unknown>> = [];
  if (opts.agentId) {
    const result = await searchMemories(ownerId, { query: opts.query, scope: "agent", agentId: opts.agentId, limit: opts.limit ?? 5 });
    bundles.push({ scope: "agent", mode: result.mode, results: result.results });
  }
  if (opts.projectId) {
    const result = await searchMemories(ownerId, { query: opts.query, scope: "project", projectId: opts.projectId, limit: opts.limit ?? 4 });
    bundles.push({ scope: "project", mode: result.mode, results: result.results });
  }
  if (opts.runId) {
    const result = await searchMemories(ownerId, { query: opts.query, scope: "workflow", runId: opts.runId, limit: opts.limit ?? 4 });
    bundles.push({ scope: "workflow", mode: result.mode, results: result.results });
  }
  return { bundles: redactSecrets(bundles), modes: [...new Set(bundles.map((bundle) => String(bundle.mode)))] };
}
