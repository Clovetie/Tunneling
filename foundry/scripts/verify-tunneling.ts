import "dotenv/config";
import { McpHttpClient } from "@/lib/mcp/http";
import { TunnelingClient, summarizeTunnelingHealth } from "@/lib/tunneling/client";

async function main() {
  const baseUrl = process.env.FOUNDRY_TEST_BRIDGE_URL || process.env.FOUNDRY_TUNNELING_URL;
  const token = process.env.FOUNDRY_TEST_BRIDGE_TOKEN || process.env.FOUNDRY_TUNNELING_TOKEN;
  if (!baseUrl) throw new Error("FOUNDRY_TEST_BRIDGE_URL is required");
  const rest = new TunnelingClient({ baseUrl, token });
  const health = await rest.health();
  const summary = summarizeTunnelingHealth(health);
  const client = new McpHttpClient({ endpoint: `${baseUrl.replace(/\/$/, "")}/mcp`, token, protocolVersion: "2026-07-28" });
  const init = await client.initialize();
  const tools = await client.listTools();
  const hasAgentTeam = tools.some((tool) => tool.name === "agent_team");
  const studios = await rest.studios();
  const bus = await rest.bus("stats");
  console.log(JSON.stringify({
    ok: true,
    transport: "streamable_http",
    health: summary,
    initialize: { protocolVersion: init.protocolVersion, serverInfo: init.serverInfo },
    toolCount: tools.length,
    hasAgentTeam,
    studios: { count: studios.count, online: studios.online, routing: studios.routing },
    bus: { agents: bus.agents, tasks: bus.tasks, messages: bus.messages, events: bus.events }
  }, null, 2));
  if (!tools.length || !hasAgentTeam) process.exit(2);
}

void main().catch((error) => { console.error("[foundry:verify:tunneling] failed", error instanceof Error ? error.message : String(error)); process.exit(1); });
