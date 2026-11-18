"use client";

import * as React from "react";
import {
  Activity, Boxes, CheckCircle2, CircleOff, Database, Eye, FileCode2, Gamepad2,
  GitBranch, Images, MonitorDot, Play, Radio, RefreshCw, ShieldCheck, Terminal, Unplug, Wrench
} from "lucide-react";
import { useApiResource, useFoundry } from "@/components/auth-gate";
import { useRealtimeRefresh } from "@/components/realtime";
import {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, InlineNotice,
  Input, JsonBlock, Label, Select, StatusPill, Tabs, TabsContent, TabsList, TabsTrigger, Textarea
} from "@/components/ui";
import { TEMPLATES } from "@/lib/templates";
import { asObject, cn, formatRelative, safeJson, shortId } from "@/lib/utils";

type IntegrationRow = { id: string; name: string; transport: string; base_url: string; status: string };
type ToolRow = { id: string; name: string; description: string; input_schema: Record<string, unknown>; availability: string; metadata: Record<string, unknown>; required_permissions: string[] };
type AgentRow = { id: string; name: string; status: string; enabled: boolean };
type Resource<T> = { data: T | null; error: string | null; loading: boolean; refresh: () => void };
type TunnelingStatus = {
  configured: boolean; ok?: boolean; error?: string;
  preview?: boolean; readOnly?: boolean; source?: string; bridgeOrigin?: string; studioKind?: string; peerLabel?: string;
  errors?: Record<string, string>;
  integration?: IntegrationRow;
  health?: { ok: boolean; version: string | null; studioConnected: boolean; mcpTools: number; peerCount: number; tunnelState: string; publicUrl: string | null; authMode: string | null; confirmGate: Record<string, unknown> };
  peers?: { peers: Array<Record<string, unknown>>; onlineCount: number };
  studios?: { studios: Array<Record<string, unknown>>; routing: string; online: number; count: number };
  tunnel?: Record<string, unknown>;
  confirmations?: { pending?: unknown[]; pendingCount?: number; mode?: string; recentDecisions?: unknown[] };
  bus?: Record<string, unknown>;
  ops?: Record<string, unknown>;
};
type PreviewPayload = {
  preview: true;
  readOnly: true;
  source: string;
  bridgeOrigin: string;
  studioKind: string;
  errors: Record<string, string>;
  status: TunnelingStatus;
  state: Record<string, unknown>;
  confirmations: { pending?: unknown[]; pendingCount?: number; mode?: string; recentDecisions?: unknown[] };
  orchestrator: Record<string, unknown>;
  bus: Record<string, unknown>;
  events: { events?: unknown[] };
  audit: { entries?: unknown[] };
  tools: ToolRow[];
  mcp: { initialize: Record<string, unknown>; toolCount: number; hasAgentTeam: boolean; error: string | null };
};

function resource<T>(data: T | null, error: string | null, loading: boolean, refresh: () => void): Resource<T> {
  return { data, error, loading, refresh };
}

function evidenceNames(value: unknown): string[] {
  const candidates: unknown[] = [];
  const object = asObject(value);
  for (const key of ["evidence", "evidenceFiles", "screenshots", "images"]) {
    const nested = object[key] ?? asObject(object.report)[key];
    if (Array.isArray(nested)) candidates.push(...nested);
  }
  return candidates.map((entry) => {
    if (typeof entry === "string") return entry;
    const record = asObject(entry);
    return String(record.filename ?? record.name ?? record.path ?? "");
  }).filter((name) => /^[A-Za-z0-9._-]+\.(png|jpg|jpeg)$/i.test(name)).slice(0, 12);
}

function EvidencePreview({ filename, integrationId, previewMode }: { filename: string; integrationId?: string; previewMode?: boolean }) {
  const { session } = useFoundry();
  const [url, setUrl] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => {
    let objectUrl: string | null = null;
    if (!previewMode && !session?.accessToken) return;
    setError(null);
    const query = integrationId ? `?integrationId=${encodeURIComponent(integrationId)}` : "";
    const endpoint = previewMode
      ? `/api/bridge/evidence/${encodeURIComponent(filename)}`
      : `/api/foundry/tunneling/evidence/${encodeURIComponent(filename)}${query}`;
    const headers = previewMode || !session?.accessToken ? undefined : { Authorization: `Bearer ${session.accessToken}` };
    void fetch(endpoint, { headers, cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.text()).slice(0, 300));
        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [filename, integrationId, previewMode, session?.accessToken]);
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-graphite-950/70">
      <div className="flex items-center justify-between border-b border-line px-2 py-1.5 font-mono text-2xs text-ink-faint"><span>{filename}</span><Badge tone="neutral">PNG</Badge></div>
      {url ? <img src={url} alt={`Tunneling evidence ${filename}`} className="max-h-72 w-full object-contain" /> : <div className="p-4 text-2xs text-ink-faint">{error ?? (previewMode ? "Loading read-only evidence…" : "Loading authenticated evidence…")}</div>}
    </div>
  );
}

export function RobloxWorkspace() {
  const { apiFetch, config } = useFoundry();
  const previewMode = config.bridgePreviewEnabled && (!config.authConfigured || !config.databaseConfigured);
  const authStatus = useApiResource<TunnelingStatus>(previewMode ? null : "/api/foundry/tunneling/status");
  const authIntegrations = useApiResource<IntegrationRow[]>(previewMode ? null : "/api/foundry/integrations");
  const authTools = useApiResource<ToolRow[]>(previewMode ? null : "/api/foundry/tunneling/tools?limit=300");
  const authAgents = useApiResource<AgentRow[]>(previewMode ? null : "/api/foundry/agents");
  const authConfirmations = useApiResource<{ pending?: unknown[]; pendingCount?: number; mode?: string; recentDecisions?: unknown[] }>(previewMode ? null : "/api/foundry/tunneling/confirmations");
  const authOrchestrator = useApiResource<Record<string, unknown>>(previewMode ? null : "/api/foundry/tunneling/orchestrator?action=status");
  const authBus = useApiResource<Record<string, unknown>>(previewMode ? null : "/api/foundry/tunneling/bus?action=stats");
  const authEvents = useApiResource<{ events?: unknown[] }>(previewMode ? null : "/api/foundry/tunneling/events?limit=40");
  const authAudit = useApiResource<{ entries?: unknown[] }>(previewMode ? null : "/api/foundry/tunneling/audit?limit=40");
  const preview = useApiResource<PreviewPayload>(previewMode ? "/api/bridge/preview" : null);
  const status: Resource<TunnelingStatus> = previewMode ? resource(preview.data?.status ?? null, preview.error, preview.loading, preview.refresh) : authStatus;
  const integrations: Resource<IntegrationRow[]> = previewMode ? resource([], null, false, preview.refresh) : authIntegrations;
  const tools: Resource<ToolRow[]> = previewMode ? resource(preview.data?.tools ?? [], preview.error, preview.loading, preview.refresh) : authTools;
  const agents: Resource<AgentRow[]> = previewMode ? resource([], null, false, preview.refresh) : authAgents;
  const confirmations: Resource<{ pending?: unknown[]; pendingCount?: number; mode?: string; recentDecisions?: unknown[] }> = previewMode ? resource(preview.data?.confirmations ?? {}, preview.error, preview.loading, preview.refresh) : authConfirmations;
  const orchestrator: Resource<Record<string, unknown>> = previewMode ? resource(preview.data?.orchestrator ?? {}, preview.error, preview.loading, preview.refresh) : authOrchestrator;
  const bus: Resource<Record<string, unknown>> = previewMode ? resource(preview.data?.bus ?? {}, preview.error, preview.loading, preview.refresh) : authBus;
  const events: Resource<{ events?: unknown[] }> = previewMode ? resource(preview.data?.events ?? { events: [] }, preview.error, preview.loading, preview.refresh) : authEvents;
  const audit: Resource<{ entries?: unknown[] }> = previewMode ? resource(preview.data?.audit ?? { entries: [] }, preview.error, preview.loading, preview.refresh) : authAudit;
  const [selectedToolId, setSelectedToolId] = React.useState("");
  const [selectedAgentId, setSelectedAgentId] = React.useState("");
  const [toolArgs, setToolArgs] = React.useState("{\n  \"action\": \"status\"\n}");
  const [actionMessage, setActionMessage] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [toolQuery, setToolQuery] = React.useState("");

  const refresh = React.useCallback(() => {
    if (previewMode) { preview.refresh(); return; }
    authStatus.refresh(); authIntegrations.refresh(); authTools.refresh(); authAgents.refresh(); authConfirmations.refresh();
    authOrchestrator.refresh(); authBus.refresh(); authEvents.refresh(); authAudit.refresh();
  }, [previewMode, preview.refresh, authStatus.refresh, authIntegrations.refresh, authTools.refresh, authAgents.refresh, authConfirmations.refresh, authOrchestrator.refresh, authBus.refresh, authEvents.refresh, authAudit.refresh]);
  const realtime = useRealtimeRefresh(refresh, ["approvals", "events", "tools", "agents"]);

  const previewOnly = Boolean(previewMode && status.data?.preview);
  const integrationId = previewOnly ? "" : (status.data?.integration?.id ?? integrations.data?.[0]?.id ?? "");
  const studioKind = status.data?.studioKind ?? "unknown peer";
  const health = status.data?.health;
  const studioConnected = health?.studioConnected === true;
  const selectedTool = (tools.data ?? []).find((tool) => tool.id === selectedToolId);
  const filteredTools = (tools.data ?? []).filter((tool) => !toolQuery || tool.name.toLowerCase().includes(toolQuery.toLowerCase()) || tool.description.toLowerCase().includes(toolQuery.toLowerCase()));
  const evidence = evidenceNames(orchestrator.data);
  const pending = Array.isArray(confirmations.data?.pending) ? confirmations.data.pending : [];

  const runAction = async (fn: () => Promise<unknown>, success: string) => {
    if (previewOnly) { setActionMessage("Read-only bridge preview: configure Supabase Auth, Postgres, a persistent worker and a stored FOUNDRY integration before performing mutations."); return; }
    setBusy(true); setActionMessage(null);
    try { await fn(); setActionMessage(success); refresh(); }
    catch (error) { setActionMessage(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };

  const bootstrap = () => runAction(() => apiFetch("/api/foundry/bootstrap", { method: "POST", body: "{}" }), "Role presets and personal workspace bootstrapped.");
  const bootstrapIntegration = () => runAction(() => apiFetch("/api/foundry/integrations/bootstrap", { method: "POST", body: "{}" }), "Tunneling integration created from server-side environment values.");
  const testIntegration = () => runAction(() => apiFetch(`/api/foundry/integrations/${integrationId}/test`, { method: "POST", body: "{}" }), "MCP handshake completed; inspect the tool count in Integrations.");
  const discover = () => runAction(() => apiFetch("/api/foundry/tools/refresh", { method: "POST", body: JSON.stringify({ integrationId }) }), "Runtime MCP discovery queued on the persistent worker.");
  const queueTool = () => {
    let args: Record<string, unknown>;
    try { args = JSON.parse(toolArgs) as Record<string, unknown>; } catch (error) { setActionMessage(`Tool arguments must be JSON: ${error instanceof Error ? error.message : String(error)}`); return; }
    return runAction(() => apiFetch("/api/foundry/tunneling/tools/call", { method: "POST", body: JSON.stringify({ toolId: selectedToolId, agentId: selectedAgentId, input: args }) }), "Tool call queued or sent to human approval. Inspect Activity for the durable result.");
  };
  const decide = (requestId: string, action: "approve" | "deny") => runAction(() => apiFetch("/api/foundry/tunneling/confirmations", { method: "POST", body: JSON.stringify({ action, requestId, integrationId, note: `FOUNDRY ${action}`, remember: action === "approve" }) }), `Tunneling ConfirmGate request ${action}d.`);

  if (status.error) {
    return <EmptyState title="Tunneling status unavailable" description={status.error} action={<Button variant="secondary" onClick={() => status.refresh()}><RefreshCw className="h-3.5 w-3.5" />Retry</Button>} />;
  }
  if (!status.loading && !status.data?.configured) {
    return (
      <div className="space-y-4">
        <InlineNotice tone="amber" title="No bridge integration is configured">FOUNDRY will not invent a Studio connection. Create a Tunneling integration with a real bridge URL and a server-side stored token.</InlineNotice>
        <div className="grid gap-4 md:grid-cols-2">
          <Card><CardHeader><CardTitle>Environment bootstrap</CardTitle><Badge tone={config.bridgeBootstrapConfigured ? "green" : "amber"}>{config.bridgeBootstrapConfigured ? "available" : "missing env"}</Badge></CardHeader><CardContent><p className="mb-3 text-xs leading-relaxed text-ink-muted">If the deployment already has FOUNDRY_TUNNELING_URL and FOUNDRY_TUNNELING_TOKEN set server-side, bootstrap the integration without exposing the token to the browser.</p><Button disabled={!config.bridgeBootstrapConfigured || busy || previewOnly} onClick={bootstrapIntegration}><Unplug className="h-3.5 w-3.5" />Bootstrap bridge</Button></CardContent></Card>
          <Card><CardHeader><CardTitle>Roblox role presets</CardTitle><Badge tone="violet">{TEMPLATES.filter((t) => t.isRoblox).length}</Badge></CardHeader><CardContent><p className="mb-3 text-xs leading-relaxed text-ink-muted">Install the nine specialist presets and general-purpose agents. This does not claim a Studio connection.</p><Button variant="secondary" disabled={busy || previewOnly} onClick={bootstrap}><Gamepad2 className="h-3.5 w-3.5" />Install presets</Button></CardContent></Card>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {actionMessage ? <InlineNotice tone={actionMessage.includes("queued") || actionMessage.includes("completed") || actionMessage.includes("bootstrapped") || actionMessage.includes("created") || actionMessage.includes("d") ? "cyan" : "rose"}>{actionMessage}</InlineNotice> : null}
      {previewOnly ? <InlineNotice tone="amber" title="Read-only live protocol preview — not a production Studio connection">
        This is a real HTTP/MCP read against the configured Tunneling bridge at {status.data?.bridgeOrigin ?? "the server-side URL"}. The reported peer is {studioKind} ({status.data?.peerLabel ?? "unknown place"}), not proof of a real Roblox Studio, deployed FOUNDRY database, public tunnel or worker. Live tools/list returned {preview.data?.mcp.toolCount ?? tools.data?.length ?? 0} schemas{preview.data?.mcp.hasAgentTeam ? " including agent_team" : ""}. Mutations, discovery persistence, approvals and durable execution remain disabled without Auth/Postgres.
        {Object.keys(preview.data?.errors ?? {}).length ? ` Partial bridge responses: ${Object.keys(preview.data?.errors ?? {}).join(", ")}.` : ""}
      </InlineNotice> : null}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric label="Studio connection" value={previewOnly ? "Preview peer" : health?.studioConnected ? "Connected" : "Disconnected"} tone={previewOnly ? "cyan" : studioConnected ? "green" : "rose"} icon={<MonitorDot className="h-4 w-4" />} />
        <Metric label="Bridge version" value={health?.version ?? "—"} tone="cyan" icon={<Radio className="h-4 w-4" />} />
        <Metric label="Discovered MCP tools" value={tools.data?.length ?? health?.mcpTools ?? 0} tone="violet" icon={<Wrench className="h-4 w-4" />} />
        <Metric label="Active peers" value={status.data?.peers?.onlineCount ?? health?.peerCount ?? 0} tone="blue" icon={<Boxes className="h-4 w-4" />} />
        <Metric label="Tunnel" value={health?.tunnelState ?? "unknown"} tone={health?.publicUrl ? "green" : "amber"} icon={<Unplug className="h-4 w-4" />} />
      </div>
      {!studioConnected ? <InlineNotice tone="rose" title="Roblox Studio is not connected">Write-capable Studio tools are unavailable. Fleet status, bridge health and read-only discovery remain real; no disconnected Studio will be presented as available.</InlineNotice> : null}
      <Tabs defaultValue="connection">
        <TabsList className="flex-wrap">
          <TabsTrigger value="connection">Connection & fleet</TabsTrigger>
          <TabsTrigger value="tools">Discovered tools</TabsTrigger>
          <TabsTrigger value="review">Change review</TabsTrigger>
          <TabsTrigger value="evidence">Viewport & evidence</TabsTrigger>
          <TabsTrigger value="orchestrator">Orchestrator & Bus</TabsTrigger>
          <TabsTrigger value="presets">Presets</TabsTrigger>
        </TabsList>
        <TabsContent value="connection" className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card><CardHeader><CardTitle>Bridge health</CardTitle><StatusPill status={health?.ok ? "connected" : "disconnected"} /></CardHeader><CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-2 text-xs"><Info label="Auth mode" value={health?.authMode ?? "—"} /><Info label="Public URL" value={health?.publicUrl ?? "not exposed"} /><Info label="ConfirmGate" value={String(asObject(status.data?.confirmations).mode ?? "unknown")} /><Info label="Realtime" value={realtime} /></div>
            <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={!integrationId || busy || previewOnly} onClick={testIntegration}><RefreshCw className="h-3.5 w-3.5" />Test MCP</Button><Button variant="secondary" disabled={!integrationId || busy || previewOnly} onClick={discover}><Database className="h-3.5 w-3.5" />Discover tools</Button><Button variant="secondary" onClick={() => status.refresh()}><Activity className="h-3.5 w-3.5" />Refresh status</Button></div>
            <JsonBlock value={health ?? {}} className="max-h-56" />
          </CardContent></Card>
          <Card><CardHeader><CardTitle>Active Studio / DataModel peers</CardTitle><Badge tone="neutral">routing {status.data?.studios?.routing ?? "unknown"}</Badge></CardHeader><CardContent className="space-y-2">
            {(status.data?.peers?.peers ?? []).map((peer, index) => <div key={String(peer.peerId ?? index)} className="rounded border border-line bg-graphite-950/60 p-2"><div className="flex items-center justify-between gap-2"><span className="font-mono text-2xs text-ink">{String(peer.peerId)}</span><StatusPill status={peer.online ? "online" : "disconnected"} /></div><div className="mt-1 grid grid-cols-2 gap-1 text-2xs text-ink-faint"><span>DM: {String(peer.dm ?? "—")}</span><span>Place: {String(peer.place ?? "—")}</span><span>PlaceId: {String(peer.placeId ?? "—")}</span><span>Age: {formatRelative(typeof peer.firstSeen === "number" ? peer.firstSeen * 1000 : undefined)}</span></div></div>)}
            {!status.data?.peers?.peers?.length ? <EmptyState title="No peer data" description="The bridge has not reported a live Roblox Studio plugin peer." /> : null}
            <JsonBlock value={status.data?.studios ?? {}} className="max-h-48" />
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="tools" className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
          <Card><CardHeader><CardTitle>Runtime MCP tool registry</CardTitle><div className="flex gap-2"><Input className="w-44" placeholder="Filter tools…" value={toolQuery} onChange={(e) => setToolQuery(e.target.value)} /><Button variant="secondary" size="icon" onClick={() => tools.refresh()}><RefreshCw className="h-3.5 w-3.5" /></Button></div></CardHeader><CardContent>
            <p className="mb-3 text-2xs text-ink-faint">Schemas come from the live bridge tools/list response. FOUNDRY does not hardcode an outdated tool inventory.</p>
            <div className="max-h-[520px] space-y-1.5 overflow-auto pr-1">{filteredTools.map((tool) => <button key={tool.id} onClick={() => { setSelectedToolId(tool.id); setToolArgs(safeJson(defaultArgs(tool))); }} className={cn("flex w-full items-start justify-between gap-3 rounded border p-2 text-left transition-colors", selectedToolId === tool.id ? "border-signal-cyan/40 bg-signal-cyan/8" : "border-line bg-graphite-950/50 hover:border-graphite-600")}><div className="min-w-0"><div className="font-mono text-2xs text-ink">{tool.name}</div><div className="mt-0.5 line-clamp-2 text-2xs text-ink-faint">{tool.description}</div><div className="mt-1 flex gap-1"><Badge tone="neutral">{tool.required_permissions.join(",")}</Badge>{asObject(tool.metadata).studioRequired ? <Badge tone="blue">studio</Badge> : null}{asObject(tool.metadata).approvalKind ? <Badge tone="amber">approval</Badge> : null}</div></div><StatusPill status={tool.availability} /></button>)}{!filteredTools.length ? <EmptyState title="No discovered tools" description="Run runtime MCP discovery on a persistent worker or local connector." action={<Button disabled={!integrationId || busy || previewOnly} onClick={discover}>Discover tools</Button>} /> : null}</div>
          </CardContent></Card>
          <Card><CardHeader><CardTitle>Queue a validated tool call</CardTitle><Badge tone="neutral">durable job</Badge></CardHeader><CardContent className="space-y-3">
            <div><Label>Tool</Label><Select value={selectedToolId} onChange={(e) => { setSelectedToolId(e.target.value); const next = (tools.data ?? []).find((tool) => tool.id === e.target.value); if (next) setToolArgs(safeJson(defaultArgs(next))); }}><option value="">Select a discovered tool</option>{(tools.data ?? []).map((tool) => <option key={tool.id} value={tool.id}>{tool.name}</option>)}</Select></div>
            <div><Label>Agent principal</Label><Select value={selectedAgentId} onChange={(e) => setSelectedAgentId(e.target.value)}><option value="">Select an agent</option>{(agents.data ?? []).filter((agent) => agent.enabled).map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</Select></div>
            {selectedTool ? <JsonBlock value={selectedTool.input_schema} className="max-h-40" /> : null}
            <div><Label>Arguments (validated against live schema)</Label><Textarea rows={9} value={toolArgs} onChange={(e) => setToolArgs(e.target.value)} /></div>
            <Button className="w-full" disabled={previewOnly || !selectedToolId || !selectedAgentId || busy || (!studioConnected && asObject(selectedTool?.metadata).studioRequired === true)} onClick={queueTool}><Play className="h-3.5 w-3.5" />Queue / request approval</Button>
            <p className="text-2xs leading-relaxed text-ink-faint">Consequential calls create a FOUNDRY approval; Tunneling ConfirmGate remains the bridge's own approval authority. The bearer token never reaches this browser.</p>
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="review" className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card><CardHeader><CardTitle>Confirmation Gate</CardTitle><Badge tone={pending.length ? "amber" : "green"}>{pending.length} pending</Badge></CardHeader><CardContent className="space-y-2">{pending.map((row, index) => { const item = asObject(row); const requestId = String(item.requestId ?? item.id ?? ""); return <div key={requestId || index} className="rounded border border-line bg-graphite-950/60 p-2"><div className="flex items-center justify-between gap-2"><span className="font-mono text-2xs text-ink">{shortId(requestId, 18)}</span><StatusPill status="pending" /></div><JsonBlock value={item.argumentsPreview ?? item} className="mt-2 max-h-36" /><div className="mt-2 flex gap-2"><Button size="sm" disabled={previewOnly || !requestId || busy} onClick={() => decide(requestId, "approve")}><CheckCircle2 className="h-3 w-3" />Approve</Button><Button size="sm" variant="danger" disabled={previewOnly || !requestId || busy} onClick={() => decide(requestId, "deny")}><CircleOff className="h-3 w-3" />Deny</Button></div></div>; })}{!pending.length ? <EmptyState title="No pending bridge confirmations" description="Destructive and write-scoped Studio operations will appear here when Tunneling ConfirmGate requests a human decision." /> : null}</CardContent></Card>
          <Card><CardHeader><CardTitle>Event / audit review</CardTitle><Badge tone="neutral">SHA-256 chain</Badge></CardHeader><CardContent className="space-y-3"><div><div className="mb-1 text-2xs uppercase tracking-wider text-ink-faint">Bridge events</div><div className="max-h-52 space-y-1 overflow-auto">{(events.data?.events ?? []).slice(0, 20).map((event, index) => { const item = asObject(event); return <div key={String(item.seq ?? index)} className="flex items-center justify-between rounded border border-line bg-graphite-950/50 px-2 py-1 font-mono text-2xs"><span className="truncate text-ink-muted">{String(item.kind)}</span><span className="text-ink-faint">{String(item.seq ?? index)}</span></div>; })}</div></div><div><div className="mb-1 text-2xs uppercase tracking-wider text-ink-faint">Audit entries</div><JsonBlock value={(audit.data?.entries ?? []).slice(0, 6)} className="max-h-44" /></div><p className="text-2xs text-ink-faint">Script diffs, viewport captures and playtest results are stored as FOUNDRY artifacts when discovered MCP tools are executed. No simulated diff is shown.</p></CardContent></Card>
        </TabsContent>
        <TabsContent value="evidence" className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card><CardHeader><CardTitle>Authenticated evidence proxy</CardTitle><Badge tone="violet">{evidence.length} files</Badge></CardHeader><CardContent className="space-y-3">{evidence.map((filename) => <EvidencePreview key={filename} filename={filename} integrationId={integrationId} previewMode={previewOnly} />)}{!evidence.length ? <EmptyState title="No evidence files reported" description="Run the existing agent_team orchestrator and its report will expose evidence filenames. FOUNDRY proxies PNGs server-side with the bridge credential." /> : null}</CardContent></Card>
          <Card><CardHeader><CardTitle>Capture / playtest tools</CardTitle><Images className="h-4 w-4 text-ink-faint" /></CardHeader><CardContent><p className="mb-3 text-xs leading-relaxed text-ink-muted">Select any discovered viewport, playtest, console or project-tree tool in the tools tab. FOUNDRY validates its live schema and queues the call; results become durable artifacts.</p><div className="grid grid-cols-2 gap-2">{(tools.data ?? []).filter((tool) => /(viewport|playtest|console|tree|script|diff|capture)/i.test(tool.name)).slice(0, 12).map((tool) => <button key={tool.id} onClick={() => setSelectedToolId(tool.id)} className="rounded border border-line bg-graphite-950/60 px-2 py-1.5 text-left font-mono text-2xs text-ink-muted hover:border-signal-cyan/30 hover:text-ink">{tool.name}</button>)}{!tools.data?.length ? <div className="col-span-2 text-2xs text-ink-faint">Discover tools to populate this list.</div> : null}</div><JsonBlock value={orchestrator.data ?? {}} className="mt-3 max-h-52" /></CardContent></Card>
        </TabsContent>
        <TabsContent value="orchestrator" className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card><CardHeader><CardTitle>Existing arena_team layer</CardTitle><Badge tone="cyan">delegated</Badge></CardHeader><CardContent className="space-y-3"><p className="text-xs leading-relaxed text-ink-muted">FOUNDRY delegates Roblox project orchestration to Tunneling's existing agent_team layer rather than replacing it. Read status, plan, roster and report through the bridge.</p><JsonBlock value={orchestrator.data ?? {}} className="max-h-80" /></CardContent></Card>
          <Card><CardHeader><CardTitle>Agent Bus normalization</CardTitle><Badge tone="violet">REST adapter</Badge></CardHeader><CardContent className="space-y-3"><div className="grid grid-cols-2 gap-2"><Info label="Agents" value={String(asObject(bus.data).agents ?? "—")} /><Info label="Tasks" value={String(asObject(bus.data).tasks ?? "—")} /><Info label="Messages" value={String(asObject(bus.data).messages ?? "—")} /><Info label="Events" value={String(asObject(bus.data).events ?? "—")} /></div><JsonBlock value={bus.data ?? {}} className="max-h-72" /></CardContent></Card>
        </TabsContent>
        <TabsContent value="presets" className="mt-4"><Card><CardHeader><CardTitle>Roblox specialist presets</CardTitle><Button variant="secondary" disabled={busy || previewOnly} onClick={bootstrap}><Gamepad2 className="h-3.5 w-3.5" />Install presets</Button></CardHeader><CardContent><div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">{TEMPLATES.filter((template) => template.isRoblox).map((template) => <div key={template.slug} className="rounded-lg border border-line bg-graphite-950/50 p-3"><div className="flex items-center gap-2 text-xs font-medium text-ink"><Gamepad2 className="h-3.5 w-3.5 text-signal-violet" />{template.name}</div><p className="mt-1 text-2xs leading-relaxed text-ink-faint">{template.description}</p><div className="mt-2 flex gap-1"><Badge tone="violet">roblox</Badge><Badge tone="neutral">memory scoped</Badge></div></div>)}</div></CardContent></Card></TabsContent>
      </Tabs>
    </div>
  );
}

function Metric({ label, value, tone, icon }: { label: string; value: React.ReactNode; tone: "cyan" | "green" | "amber" | "rose" | "violet" | "blue"; icon: React.ReactNode }) {
  const tones = { cyan: "text-signal-cyan bg-signal-cyan/10", green: "text-signal-green bg-signal-green/10", amber: "text-signal-amber bg-signal-amber/10", rose: "text-signal-rose bg-signal-rose/10", violet: "text-signal-violet bg-signal-violet/10", blue: "text-signal-blue bg-signal-blue/10" };
  return <Card className="p-3"><div className="flex items-center justify-between gap-2"><div className="min-w-0"><div className="text-2xs uppercase tracking-wider text-ink-faint">{label}</div><div className="mt-1 truncate text-sm font-semibold text-ink">{value}</div></div><div className={cn("flex h-8 w-8 items-center justify-center rounded-lg", tones[tone])}>{icon}</div></div></Card>;
}

function Info({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="rounded border border-line bg-graphite-950/50 px-2 py-1.5"><div className="text-2xs text-ink-faint">{label}</div><div className="truncate font-mono text-2xs text-ink-muted">{value}</div></div>;
}

function defaultArgs(tool: ToolRow): Record<string, unknown> {
  const schema = asObject(tool.input_schema);
  const properties = asObject(schema.properties);
  const required = Array.isArray(schema.required) ? schema.required.map(String) : [];
  const out: Record<string, unknown> = {};
  if (properties.action) out.action = asObject(properties.action).default ?? "status";
  for (const key of required) if (!(key in out)) out[key] = asObject(properties[key]).default ?? "";
  return Object.keys(out).length ? out : { action: "status" };
}
