import { query } from "@/lib/db";
import { ApiError } from "@/lib/errors";
import { assertOwnerRecord } from "@/lib/access";
import { emitEvent } from "@/lib/events";
import { getIntegration, makeMcpClient } from "@/lib/integrations";
import { TunnelingClient } from "@/lib/tunneling/client";
import { classifyMcpTool, assertHttpUrlAllowed } from "@/lib/tools/policy";
import { asObject } from "@/lib/utils";

export type ToolRow = {
  id: string;
  owner_id: string;
  integration_id: string | null;
  name: string;
  kind: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown> | null;
  required_permissions: string[];
  timeout_ms: number;
  availability: string;
  metadata: Record<string, unknown>;
  last_seen_at: string;
};

export async function listTools(ownerId: string, options: { integrationId?: string; query?: string; limit?: number } = {}): Promise<ToolRow[]> {
  const params: unknown[] = [ownerId];
  const clauses = ["owner_id = $1"];
  if (options.integrationId) { params.push(options.integrationId); clauses.push(`integration_id = $${params.length}`); }
  if (options.query) { params.push(`%${options.query}%`); clauses.push(`(name ilike $${params.length} or description ilike $${params.length})`); }
  params.push(Math.min(Math.max(options.limit ?? 200, 1), 500));
  const rows = await query<ToolRow>(
    `select * from public.tools where ${clauses.join(" and ")} order by availability asc, name asc limit $${params.length}`,
    params
  );
  return rows.map((row) => ({ ...row, input_schema: asObject(row.input_schema), metadata: asObject(row.metadata) }));
}

export async function getTool(ownerId: string, toolId: string): Promise<ToolRow> {
  const row = await assertOwnerRecord("tools", ownerId, toolId) as unknown as ToolRow;
  return { ...row, input_schema: asObject(row.input_schema), metadata: asObject(row.metadata) };
}

export async function discoverMcpTools(ownerId: string, integrationId: string) {
  const integration = await getIntegration(ownerId, integrationId);
  const mcp = await makeMcpClient(ownerId, integration);
  try {
    const initialize = await mcp.client.initialize();
    const discovered = await mcp.client.listTools();
    let studioConnected: boolean | null = null;
    if (integration.kind === "tunneling" && integration.base_url) {
      const rest = new TunnelingClient({ baseUrl: integration.base_url, token: mcp.token ?? undefined });
      try {
        const health = await rest.health();
        studioConnected = health.studio_connected === true || (health.studio as Record<string, unknown> | undefined)?.online === true;
      } catch { studioConnected = false; }
    }
    let upserted = 0;
    for (const tool of discovered) {
      const classification = classifyMcpTool(tool);
      const availability = classification.approvalKind
        ? "approval_required"
        : studioConnected === false && classification.studioRequired
          ? "degraded"
          : "available";
      const metadata = {
        source: "runtime_mcp_discovery",
        transport: mcp.transport,
        protocolVersion: typeof initialize.protocolVersion === "string" ? initialize.protocolVersion : integration.config.protocolVersion ?? "2026-07-28",
        annotations: tool.annotations ?? {},
        execution: tool.execution ?? {},
        approvalKind: classification.approvalKind,
        approvalReason: classification.approvalReason,
        studioRequired: classification.studioRequired,
        risk: classification.risk,
        discoveredAt: new Date().toISOString(),
        studioConnected
      };
      await query(
        `insert into public.tools
          (owner_id, integration_id, name, kind, description, input_schema, output_schema, required_permissions, timeout_ms, availability, metadata, last_seen_at)
         values ($1,$2,$3,'mcp',$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$10::jsonb,now())
         on conflict (owner_id, integration_id, name) do update set
           description=excluded.description, input_schema=excluded.input_schema, output_schema=excluded.output_schema,
           required_permissions=excluded.required_permissions, timeout_ms=excluded.timeout_ms,
           availability=excluded.availability, metadata=excluded.metadata, last_seen_at=now(), updated_at=now()`,
        [
          ownerId, integration.id, tool.name, tool.description ?? "", JSON.stringify(tool.inputSchema ?? { type: "object", properties: {} }),
          tool.outputSchema ? JSON.stringify(tool.outputSchema) : null,
          JSON.stringify(classification.requiredPermissions), Number(integration.config.timeoutMs ?? 30_000), availability, JSON.stringify(metadata)
        ]
      );
      upserted++;
    }
    const status = studioConnected === false ? "degraded" : "connected";
    await query("update public.integrations set status=$2,last_checked_at=now(),last_error=null,updated_at=now() where id=$1 and owner_id=$3", [integration.id, status, ownerId]);
    await emitEvent({ ownerId, kind: "tools.discovered", data: { integrationId, count: upserted, transport: mcp.transport, studioConnected } });
    return {
      ok: true,
      integrationId,
      transport: mcp.transport,
      protocolVersion: typeof initialize.protocolVersion === "string" ? initialize.protocolVersion : null,
      discovered: discovered.length,
      upserted,
      studioConnected,
      tools: discovered.slice(0, 20).map((tool) => ({ name: tool.name, readOnly: asObject(tool.annotations).readOnlyHint === true }))
    };
  } finally {
    await mcp.close();
  }
}

export async function createCustomTool(ownerId: string, input: {
  name: string;
  kind: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
  outputSchema?: Record<string, unknown>;
  requiredPermissions?: string[];
  timeoutMs?: number;
  endpoint?: string;
  httpAllowlist?: string[];
  metadata?: Record<string, unknown>;
}) {
  const allowedKinds = ["http", "knowledge", "memory", "custom", "code", "file", "database", "search"];
  if (!allowedKinds.includes(input.kind)) throw new ApiError("Unsupported custom tool kind.", 400, "TOOL_KIND_DENIED");
  const metadata: Record<string, unknown> = { ...(input.metadata ?? {}), source: "custom_registry" };
  let availability = "available";
  if (input.kind === "http") {
    if (!input.endpoint) throw new ApiError("HTTP tools require an endpoint.", 400, "HTTP_ENDPOINT_REQUIRED");
    const url = assertHttpUrlAllowed(input.endpoint, input.httpAllowlist ?? []);
    metadata.endpoint = url.toString();
    metadata.httpAllowlist = input.httpAllowlist ?? [];
  } else if (["code", "file", "database", "custom", "search"].includes(input.kind)) {
    availability = "unavailable";
    metadata.disabledReason = "No isolated sandbox or external provider is configured. This tool is registered but cannot execute arbitrary host operations.";
  }
  const rows = await query<{ id: string }>(
    `insert into public.tools
      (owner_id, integration_id, name, kind, description, input_schema, output_schema, required_permissions, timeout_ms, availability, metadata)
     values ($1,null,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$10::jsonb) returning id`,
    [
      ownerId, input.name, input.kind, input.description ?? "", JSON.stringify(input.inputSchema ?? { type: "object", properties: {} }),
      input.outputSchema ? JSON.stringify(input.outputSchema) : null, JSON.stringify(input.requiredPermissions ?? ["write"]),
      Math.min(Math.max(input.timeoutMs ?? 30_000, 1000), 300_000), availability, JSON.stringify(metadata)
    ]
  );
  return { id: rows[0].id, name: input.name, availability };
}
