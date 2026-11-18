import { query } from "@/lib/db";
import { ApiError } from "@/lib/errors";
import { assertAgentAccess, assertProjectAccess, assertRunAccess } from "@/lib/access";
import { emitEvent } from "@/lib/events";
import { recordAudit } from "@/lib/audit";
import { getIntegration, getIntegrationSecret } from "@/lib/integrations";
import { TunnelingClient } from "@/lib/tunneling/client";
import { asObject, redactSecrets } from "@/lib/utils";

export type MessageInput = {
  ownerId: string;
  channel: "direct" | "broadcast" | "task" | "artifact" | "handoff" | "system";
  content: string;
  senderAgentId?: string | null;
  recipientAgentId?: string | null;
  projectId?: string | null;
  runId?: string | null;
  taskId?: string | null;
  artifactId?: string | null;
  payload?: Record<string, unknown>;
  transport?: "foundry" | "tunneling_bus" | "external";
  externalId?: string | null;
};

const CHANNEL_PERMISSION: Record<string, string> = {
  direct: "direct", broadcast: "broadcast", task: "task", artifact: "artifact", handoff: "handoff", system: "system"
};

export async function sendMessage(input: MessageInput) {
  if (input.senderAgentId) await assertAgentAccess(input.ownerId, input.senderAgentId);
  if (input.recipientAgentId) await assertAgentAccess(input.ownerId, input.recipientAgentId);
  await assertProjectAccess(input.ownerId, input.projectId, "editor");
  await assertRunAccess(input.ownerId, input.runId);
  if (input.senderAgentId) {
    const agents = await query<{ communication_permissions: unknown; enabled: boolean; name: string }>("select communication_permissions,enabled,name from public.agents where id=$1 and owner_id=$2", [input.senderAgentId, input.ownerId]);
    const agent = agents[0];
    if (!agent) throw new ApiError("Sender agent not found.", 404, "NOT_FOUND");
    if (!agent.enabled) throw new ApiError("Sender agent is disabled.", 403, "AGENT_DISABLED");
    const permissions = asObject(agent.communication_permissions);
    const key = CHANNEL_PERMISSION[input.channel];
    if (key !== "system" && permissions[key] !== true) throw new ApiError(`Agent is not permitted to send ${input.channel} messages.`, 403, "COMMUNICATION_DENIED");
  }
  if (input.transport === "external") throw new ApiError("External communications require an explicit human approval record.", 403, "EXTERNAL_COMMUNICATION_APPROVAL_REQUIRED");
  let busResult: Record<string, unknown> | null = null;
  let externalId = input.externalId ?? null;
  if (input.transport === "tunneling_bus") {
    const integrations = await query<{ id: string }>("select id from public.integrations where owner_id=$1 and kind='tunneling' order by created_at asc limit 1", [input.ownerId]);
    if (!integrations[0]) throw new ApiError("No Tunneling integration is configured for Agent Bus forwarding.", 503, "BUS_INTEGRATION_NOT_CONFIGURED");
    const integration = await getIntegration(input.ownerId, integrations[0].id);
    if (!integration.base_url) throw new ApiError("Tunneling integration has no bridge URL.", 503, "BRIDGE_URL_UNAVAILABLE");
    const token = await getIntegrationSecret(input.ownerId, integration.id);
    const client = new TunnelingClient({ baseUrl: integration.base_url, token: token ?? undefined });
    const senderName = input.senderAgentId ? (await query<{ name: string }>("select name from public.agents where id=$1 and owner_id=$2", [input.senderAgentId, input.ownerId]))[0]?.name ?? "FOUNDRY" : "FOUNDRY";
    busResult = await client.post("/api/bus", {
      action: "post",
      agentId: senderName,
      from: senderName,
      body: input.content,
      to: input.channel === "broadcast" ? "all" : input.recipientAgentId ?? undefined,
      subject: input.channel === "task" ? "FOUNDRY task message" : "FOUNDRY message",
      kind: input.channel === "handoff" ? "handoff" : input.channel === "artifact" ? "result" : "note",
      meta: { layer: "foundry", projectId: input.projectId ?? undefined, runId: input.runId ?? undefined, taskId: input.taskId ?? undefined }
    });
    const maybeMessage = asObject((busResult as Record<string, unknown>).message ?? busResult ?? {});
    externalId = typeof maybeMessage.id === "string" ? maybeMessage.id : null;
  }
  const rows = await query<{ id: string }>(
    `insert into public.messages
      (owner_id,project_id,run_id,task_id,sender_agent_id,recipient_agent_id,channel,transport,content,payload,artifact_id,external_id)
     values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12)
     on conflict (transport, external_id) do update set content=excluded.content,payload=excluded.payload
     returning id`,
    [
      input.ownerId, input.projectId ?? null, input.runId ?? null, input.taskId ?? null, input.senderAgentId ?? null,
      input.recipientAgentId ?? null, input.channel, input.transport ?? "foundry", input.content,
      JSON.stringify(redactSecrets(input.payload ?? {})), input.artifactId ?? null, externalId
    ]
  );
  await emitEvent({ ownerId: input.ownerId, kind: "message.sent", data: { channel: input.channel, transport: input.transport ?? "foundry", preview: input.content.slice(0, 160) }, runId: input.runId, taskId: input.taskId, projectId: input.projectId, agentId: input.senderAgentId });
  await recordAudit({ ownerId: input.ownerId, action: "message.send", actor: input.senderAgentId ? `agent:${input.senderAgentId}` : "user", target: rows[0]?.id ?? "", detail: { channel: input.channel, transport: input.transport ?? "foundry" } });
  return { id: rows[0]?.id, externalId, busResult: redactSecrets(busResult) };
}

export async function listMessages(ownerId: string, options: { runId?: string | null; projectId?: string | null; limit?: number } = {}) {
  return query(
    `select m.*, s.name as sender_name, r.name as recipient_name
     from public.messages m
     left join public.agents s on s.id=m.sender_agent_id
     left join public.agents r on r.id=m.recipient_agent_id
     where m.owner_id=$1 and ($2::uuid is null or m.run_id=$2) and ($3::uuid is null or m.project_id=$3)
     order by m.created_at desc limit $4`,
    [ownerId, options.runId ?? null, options.projectId ?? null, Math.min(Math.max(options.limit ?? 100, 1), 300)]
  );
}

export async function importBusMessages(ownerId: string, integrationId: string, limit = 50) {
  const integration = await getIntegration(ownerId, integrationId);
  if (!integration.base_url) throw new ApiError("Tunneling integration has no bridge URL.", 503, "BRIDGE_URL_UNAVAILABLE");
  const token = await getIntegrationSecret(ownerId, integration.id);
  const client = new TunnelingClient({ baseUrl: integration.base_url, token: token ?? undefined });
  const result = await client.bus("inbox", { agentId: "FOUNDRY", limit: Math.min(Math.max(limit, 1), 200), includeBroadcast: 1 });
  const messages = Array.isArray(result.messages) ? result.messages as Array<Record<string, unknown>> : [];
  let imported = 0;
  for (const message of messages) {
    const id = typeof message.id === "string" ? message.id : null;
    if (!id) continue;
    const rows = await query(
      `insert into public.messages (owner_id,transport,channel,content,payload,external_id)
       values ($1,'tunneling_bus',$2,$3,$4::jsonb,$5)
       on conflict (transport, external_id) do nothing returning id`,
      [ownerId, message.to ? "direct" : "broadcast", String(message.body ?? message.subject ?? ""), JSON.stringify(redactSecrets(message)), id]
    );
    if (rows.length) imported++;
  }
  await emitEvent({ ownerId, kind: "bus.imported", data: { integrationId, seen: messages.length, imported } });
  return { seen: messages.length, imported };
}
