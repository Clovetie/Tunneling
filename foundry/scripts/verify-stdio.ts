import "dotenv/config";
import path from "node:path";
import { McpStdioClient } from "@/lib/mcp/stdio";

async function main() {
  const bridgeUrl = process.env.FOUNDRY_TEST_BRIDGE_URL || process.env.FOUNDRY_TUNNELING_URL;
  const token = process.env.FOUNDRY_TEST_BRIDGE_TOKEN || process.env.FOUNDRY_TUNNELING_TOKEN;
  if (!bridgeUrl) throw new Error("FOUNDRY_TEST_BRIDGE_URL is required");
  const command = process.env.FOUNDRY_STDIO_COMMAND || "python3";
  const args = (process.env.FOUNDRY_STDIO_ARGS || "mcp_server.py,--no-embedded").split(",").map((v) => v.trim()).filter(Boolean);
  const cwd = path.resolve(process.env.FOUNDRY_STDIO_CWD || "../roblox-bridge");
  const client = new McpStdioClient({ command, args, cwd, bridgeUrl, bridgeToken: token, timeoutMs: 30_000 });
  try {
    const init = await client.initialize();
    const tools = await client.listTools();
    const state = await client.callTool("get_studio_state", {});
    console.log(JSON.stringify({
      ok: true,
      transport: "stdio",
      command,
      args,
      initialize: { protocolVersion: init.protocolVersion, serverInfo: init.serverInfo },
      toolCount: tools.length,
      hasAgentTeam: tools.some((tool) => tool.name === "agent_team"),
      stateKeys: state && typeof state === "object" ? Object.keys(state as Record<string, unknown>).slice(0, 20) : []
    }, null, 2));
    if (!tools.length) process.exit(2);
  } finally {
    await client.close();
  }
}

void main().catch((error) => { console.error("[foundry:verify:stdio] failed", error instanceof Error ? error.message : String(error)); process.exit(1); });
