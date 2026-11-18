"use client";

import { RobloxWorkspace } from "@/components/roblox/workspace";
import { SectionHeader } from "@/components/ui";

export default function RobloxPage() {
  return <div><SectionHeader eyebrow="Tunneling integration" title="Roblox Development Workspace" description="Real bridge health, fleet routing, peer/DataModel state, runtime MCP schemas, ConfirmGate, evidence, playtest surfaces and the existing arena_team layer. Disconnected Studio is never presented as available." /><RobloxWorkspace /></div>;
}
