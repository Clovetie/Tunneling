import { query } from "@/lib/db";
import { ApiError } from "@/lib/errors";

const ROLE_RANK: Record<string, number> = { viewer: 1, editor: 2, owner: 3 };

export async function assertProjectAccess(ownerId: string, projectId: string | null | undefined, minRole: "viewer" | "editor" | "owner" = "viewer"): Promise<void> {
  if (!projectId) return;
  const rows = await query<{ owner_id: string; role: string | null }>(
    `select p.owner_id, pm.role
     from public.projects p
     left join public.project_members pm on pm.project_id=p.id and pm.user_id=$2
     where p.id=$1 limit 1`,
    [projectId, ownerId]
  );
  const row = rows[0];
  if (!row) throw new ApiError("Project not found.", 404, "PROJECT_NOT_FOUND");
  if (row.owner_id !== ownerId) {
    const rank = ROLE_RANK[row.role ?? ""] ?? 0;
    if (rank < ROLE_RANK[minRole]) throw new ApiError("Project access denied.", 403, "PROJECT_ACCESS_DENIED");
  }
}

export async function assertOwnerRecord(table: "agents" | "workflows" | "workflow_runs" | "tasks" | "integrations" | "tools" | "approvals" | "workspace_items" | "knowledge_documents", ownerId: string, id: string): Promise<Record<string, unknown>> {
  const rows = await query<Record<string, unknown>>(`select * from public.${table} where id=$1 and owner_id=$2 limit 1`, [id, ownerId]);
  if (!rows[0]) throw new ApiError("Record not found.", 404, "NOT_FOUND");
  return rows[0];
}

export async function assertAgentAccess(ownerId: string, agentId: string | null | undefined): Promise<void> {
  if (!agentId) return;
  await assertOwnerRecord("agents", ownerId, agentId);
}

export async function assertRunAccess(ownerId: string, runId: string | null | undefined): Promise<void> {
  if (!runId) return;
  await assertOwnerRecord("workflow_runs", ownerId, runId);
}
