import { getPublicConfig } from "@/lib/public-config";
import { jsonError, jsonOk } from "@/lib/errors";
import { McpHttpClient } from "@/lib/mcp/http";
import type { McpTool } from "@/lib/mcp/jsonrpc";
import { TunnelingClient, summarizeTunnelingHealth } from "@/lib/tunneling/client";
import { asObject, redactSecrets } from "@/lib/utils";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Settled<T> = { value: T | null; error: string | null };

async function capture<T>(promise: Promise<T>): Promise<Settled<T>> {
  try { return { value: await promise, error: null }; }
  catch (error) { return { value: null, error: redactSecrets(error instanceof Error ? error.message : String(error)).slice(0, 300) }; }
}

function previewClient() {
  const config = getPublicConfig();
  if (!config.bridgePreviewEnabled) return null;
  const baseUrl = process.env.FOUNDRY_TUNNELING_URL!.trim();
  const token = process.env.FOUNDRY_TUNNELING_TOKEN!.trim();
  return { baseUrl, token, client: new TunnelingClient({ baseUrl, token, timeoutMs: 8_000 }) };
}

export async function GET() {
  const context = previewClient();
  if (!context) return jsonOk({ error: "Read-only bridge preview is disabled. Set FOUNDRY_TUNNELING_URL, FOUNDRY_TUNNELING_TOKEN, and explicitly enable FOUNDRY_PUBLIC_BRIDGE_PREVIEW in production.", code: "BRIDGE_PREVIEW_DISABLED" }, 503);
  try {
    const { client, baseUrl, token } = context;
    const [health, state, peers, studios, tunnel, ops, confirmations, bus, orchestrator, events, audit] = await Promise.all([
      capture(client.health()),
      capture(client.state()),
      capture(client.peers()),
      capture(client.studios()),
      capture(client.tunnel()),
      capture(client.ops()),
      capture(client.confirmations()),
      capture(client.bus("stats")),
      capture(client.orchestrator("status")),
      capture(client.events({ limit: 80, once: 1 })),
      capture(client.audit({ limit: 80 }))
    ]);
    const mcp = new McpHttpClient({ endpoint: `${baseUrl.replace(/\/$/, "")}/mcp`, token, timeoutMs: 8_000 });
    const [initialized, tools] = await Promise.all([
      capture(mcp.initialize()),
      capture(mcp.listTools())
    ]);
    const discovered = (tools.value ?? []) as McpTool[];
    const healthRecord = health.value ?? {};
    const studio = asObject(healthRecord.studio);
    const peerList = Array.isArray(asObject(peers.value).peers) ? asObject(peers.value).peers as Array<Record<string, unknown>> : [];
    const clientKind = String(studio.client ?? peerList[0]?.client ?? "unknown");
    const studioKind = clientKind === "mock" ? "MockWorld simulated Studio" : clientKind;
    const origin = new URL(baseUrl).origin;
    const errors = Object.entries({ health, state, peers, studios, tunnel, ops, confirmations, bus, orchestrator, events, audit, initialized, tools })
      .filter(([, result]) => result.error)
      .reduce<Record<string, string>>((acc, [key, result]) => { if (result.error) acc[key] = result.error; return acc; }, {});
    const status = {
      configured: true,
      preview: true,
      readOnly: true,
      source: "server-side FOUNDRY_TUNNELING_URL",
      bridgeOrigin: origin,
      studioKind,
      peerLabel: String(studio.place ?? peerList[0]?.place ?? "No peer reported"),
      integration: { id: "", name: "Environment bridge preview", transport: "streamable_http", base_url: origin, status: "preview" },
      health: summarizeTunnelingHealth(healthRecord),
      peers: peers.value ?? { peers: [], onlineCount: 0 },
      studios: studios.value ?? { studios: [], routing: "unknown", online: 0, count: 0 },
      tunnel: tunnel.value ?? {},
      ops: ops.value ?? {},
      confirmations: confirmations.value ?? {},
      bus: bus.value ?? {},
      errors
    };
    return jsonOk({
      preview: true,
      readOnly: true,
      source: "server-side FOUNDRY_TUNNELING_URL",
      bridgeOrigin: origin,
      studioKind,
      errors,
      status,
      state: state.value ?? {},
      confirmations: confirmations.value ?? {},
      orchestrator: orchestrator.value ?? {},
      bus: bus.value ?? {},
      events: events.value ?? { events: [] },
      audit: audit.value ?? { entries: [] },
      tools: discovered.map((tool) => ({
        id: `preview:${tool.name}`,
        name: tool.name,
        description: tool.description ?? "",
        input_schema: tool.inputSchema ?? { type: "object", properties: {} },
        availability: "available",
        metadata: { runtimeDiscovered: true, source: "live tools/list preview", annotations: tool.annotations ?? {}, studioRequired: Boolean(tool.annotations?.studioRequired) },
        required_permissions: tool.annotations?.readOnlyHint === true ? ["read"] : ["write"]
      })),
      mcp: {
        initialize: initialized.value ?? {},
        toolCount: discovered.length,
        hasAgentTeam: discovered.some((tool) => tool.name === "agent_team"),
        error: initialized.error ?? tools.error ?? null
      }
    });
  } catch (error) {
    return jsonError(error);
  }
}
