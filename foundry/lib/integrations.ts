import { query } from "@/lib/db";
import { ApiError } from "@/lib/errors";
import { decryptSecret, encryptSecret, isEncryptionConfigured } from "@/lib/crypto";
import { TunnelingClient, summarizeTunnelingHealth } from "@/lib/tunneling/client";
import { McpHttpClient } from "@/lib/mcp/http";
import { McpStdioClient } from "@/lib/mcp/stdio";
import { assertOwnerRecord } from "@/lib/access";
import { asObject, redactSecrets } from "@/lib/utils";

export type IntegrationRow = {
  id: string;
  owner_id: string;
  kind: string;
  name: string;
  transport: "streamable_http" | "stdio" | "http_api";
  base_url: string | null;
  config: Record<string, unknown>;
  status: string;
  last_checked_at?: string | null;
  last_error?: string | null;
};

export async function listIntegrations(ownerId: string): Promise<IntegrationRow[]> {
  const rows = await query<IntegrationRow>(
    `select id, owner_id, kind, name, transport, base_url, config, status, last_checked_at, last_error
     from public.integrations where owner_id=$1 order by created_at desc`,
    [ownerId]
  );
  return rows.map((row) => ({ ...row, config: asObject(row.config) }));
}

export async function getIntegration(ownerId: string, integrationId: string): Promise<IntegrationRow> {
  const row = await assertOwnerRecord("integrations", ownerId, integrationId) as unknown as IntegrationRow;
  return { ...row, config: asObject(row.config) };
}

export async function getIntegrationSecret(ownerId: string, integrationId: string, name = "bridge_token"): Promise<string | null> {
  if (!isEncryptionConfigured()) throw new ApiError("Secret encryption is not configured.", 503, "ENCRYPTION_NOT_CONFIGURED");
  const rows = await query<{ ciphertext: string; iv: string; auth_tag: string; key_version: number }>(
    "select ciphertext, iv, auth_tag, key_version from public.integration_secrets where integration_id=$1 and owner_id=$2 and name=$3 limit 1",
    [integrationId, ownerId, name]
  );
  if (!rows[0]) return null;
  try { return decryptSecret(rows[0]); }
  catch (error) { throw new ApiError(`Stored integration secret cannot be decrypted: ${error instanceof Error ? error.message : String(error)}`, 500, "SECRET_DECRYPT_FAILED"); }
}

export async function setIntegrationSecret(ownerId: string, integrationId: string, plaintext: string, name = "bridge_token") {
  if (!isEncryptionConfigured()) throw new ApiError("FOUNDRY_SECRET_ENCRYPTION_KEY is not configured.", 503, "ENCRYPTION_NOT_CONFIGURED");
  const encrypted = encryptSecret(plaintext);
  await query(
    `insert into public.integration_secrets (owner_id, integration_id, name, ciphertext, iv, auth_tag, key_version)
     values ($1,$2,$3,$4,$5,$6,$7)
     on conflict (integration_id, name) do update
       set ciphertext=excluded.ciphertext, iv=excluded.iv, auth_tag=excluded.auth_tag, key_version=excluded.key_version, updated_at=now()`,
    [ownerId, integrationId, name, encrypted.ciphertext, encrypted.iv, encrypted.authTag, encrypted.keyVersion]
  );
  return { stored: true, name };
}

export function integrationMcpEndpoint(integration: IntegrationRow): string {
  const configured = integration.config.mcpEndpoint;
  if (typeof configured === "string" && configured) return configured;
  if (!integration.base_url) throw new ApiError("Integration has no base URL or MCP endpoint.", 400, "INTEGRATION_URL_REQUIRED");
  return `${integration.base_url.replace(/\/$/, "")}/mcp`;
}

export function stdioConfigFromIntegration(integration: IntegrationRow, token: string | null) {
  const config = integration.config;
  const command = typeof config.stdioCommand === "string" ? config.stdioCommand : process.env.FOUNDRY_STDIO_COMMAND ?? "python3";
  const args = Array.isArray(config.stdioArgs)
    ? config.stdioArgs.map(String)
    : (process.env.FOUNDRY_STDIO_ARGS ?? "mcp_server.py,--no-embedded").split(",").map((v) => v.trim()).filter(Boolean);
  const cwd = typeof config.stdioCwd === "string" ? config.stdioCwd : process.env.FOUNDRY_STDIO_CWD ?? "../roblox-bridge";
  return {
    command, args, cwd,
    bridgeUrl: integration.base_url ?? undefined,
    bridgeToken: token ?? undefined,
    readOnly: config.readOnly === true,
    timeoutMs: Number(config.timeoutMs ?? 30_000)
  };
}

export async function makeMcpClient(ownerId: string, integration: IntegrationRow) {
  const token = await getIntegrationSecret(ownerId, integration.id);
  if (integration.transport === "streamable_http") {
    return {
      transport: "streamable_http" as const,
      client: new McpHttpClient({ endpoint: integrationMcpEndpoint(integration), token: token ?? undefined, protocolVersion: typeof integration.config.protocolVersion === "string" ? integration.config.protocolVersion : "2026-07-28", timeoutMs: Number(integration.config.timeoutMs ?? 30_000) }),
      close: async () => undefined,
      token
    };
  }
  if (integration.transport === "stdio") {
    if (process.env.FOUNDRY_ALLOW_LOCAL_CONNECTOR !== "true") {
      throw new ApiError("The stdio connector is only available on a persistent local connector host. Set FOUNDRY_ALLOW_LOCAL_CONNECTOR=true there.", 503, "LOCAL_CONNECTOR_UNAVAILABLE");
    }
    const client = new McpStdioClient(stdioConfigFromIntegration(integration, token));
    await client.start();
    return { transport: "stdio" as const, client, close: () => client.close(), token };
  }
  throw new ApiError(`Integration transport ${integration.transport} has no MCP client.`, 400, "UNSUPPORTED_TRANSPORT");
}

export async function makeRestClient(ownerId: string, integrationId: string) {
  const integration = await getIntegration(ownerId, integrationId);
  if (!integration.base_url) throw new ApiError("Integration has no base URL.", 400, "INTEGRATION_URL_REQUIRED");
  const token = await getIntegrationSecret(ownerId, integration.id);
  return { integration, client: new TunnelingClient({ baseUrl: integration.base_url, token: token ?? undefined, timeoutMs: Number(integration.config.timeoutMs ?? 20_000) }) };
}

export async function testIntegration(ownerId: string, integrationId: string) {
  const integration = await getIntegration(ownerId, integrationId);
  const mcp = await makeMcpClient(ownerId, integration);
  try {
    const initialize = await mcp.client.initialize();
    const tools = await mcp.client.listTools();
    let healthSummary: Record<string, unknown> | null = null;
    if (integration.kind === "tunneling" && integration.base_url) {
      const rest = new TunnelingClient({ baseUrl: integration.base_url, token: mcp.token ?? undefined });
      try { healthSummary = summarizeTunnelingHealth(await rest.health()); }
      catch (error) { healthSummary = { ok: false, error: String(redactSecrets(error instanceof Error ? error.message : String(error))).slice(0, 300) }; }
    }
    const status = healthSummary?.ok === false ? "degraded" : "connected";
    await query("update public.integrations set status=$2, last_checked_at=now(), last_error=null, updated_at=now() where id=$1 and owner_id=$3", [integration.id, status, ownerId]);
    return {
      ok: true,
      status,
      transport: mcp.transport,
      protocolVersion: typeof initialize.protocolVersion === "string" ? initialize.protocolVersion : null,
      serverInfo: initialize.serverInfo ?? null,
      toolCount: tools.length,
      sampleTools: tools.slice(0, 8).map((tool) => tool.name),
      tunneling: healthSummary
    };
  } finally {
    await mcp.close();
  }
}

export async function bootstrapTunnelingFromEnv(ownerId: string) {
  const baseUrl = process.env.FOUNDRY_TUNNELING_URL?.trim();
  const token = process.env.FOUNDRY_TUNNELING_TOKEN?.trim();
  if (!baseUrl || !token) throw new ApiError("FOUNDRY_TUNNELING_URL and FOUNDRY_TUNNELING_TOKEN are not configured.", 400, "BRIDGE_BOOTSTRAP_NOT_CONFIGURED");
  if (!isEncryptionConfigured()) throw new ApiError("Secret encryption is not configured.", 503, "ENCRYPTION_NOT_CONFIGURED");
  const name = process.env.FOUNDRY_TUNNELING_NAME?.trim() || "Primary Roblox bridge";
  const transport = (process.env.FOUNDRY_TUNNELING_TRANSPORT ?? "streamable_http") as IntegrationRow["transport"];
  const rows = await query<{ id: string }>(
    `insert into public.integrations (owner_id, kind, name, transport, base_url, config, status)
     values ($1,'tunneling',$2,$3,$4,$5::jsonb,'unconfigured')
     on conflict (owner_id, kind, name) do update set transport=excluded.transport, base_url=excluded.base_url, updated_at=now()
     returning id`,
    [
      ownerId, name, transport, baseUrl,
      JSON.stringify({ mcpEndpoint: `${baseUrl.replace(/\/$/, "")}/mcp`, protocolVersion: "2026-07-28", timeoutMs: 30_000, httpAllowlist: [] })
    ]
  );
  await setIntegrationSecret(ownerId, rows[0].id, token);
  return { id: rows[0].id, name, transport, baseUrl, secretStored: true };
}
