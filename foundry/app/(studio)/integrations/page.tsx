"use client";

import * as React from "react";
import { Database, KeyRound, Link2, Plus, RefreshCw, ShieldCheck, Unplug, Wrench } from "lucide-react";
import { useApiResource, useFoundry } from "@/components/auth-gate";
import { useRealtimeRefresh } from "@/components/realtime";
import { ApiErrorNotice, EmptyOrError } from "@/components/page-helpers";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, InlineNotice, Input, JsonBlock, Label, SectionHeader, Select, StatusPill, Textarea } from "@/components/ui";
import { asObject, formatRelative } from "@/lib/utils";

type IntegrationRow = Record<string, unknown> & { id: string; name: string; kind: string; transport: string; base_url: string; status: string; last_checked_at?: string | null; last_error?: string | null; config: Record<string, unknown> };
type ToolRow = Record<string, unknown> & { id: string; name: string; kind: string; description: string; availability: string; input_schema: Record<string, unknown>; metadata: Record<string, unknown>; last_seen_at: string };

export default function IntegrationsPage() {
  const { apiFetch, config } = useFoundry();
  const integrations = useApiResource<IntegrationRow[]>("/api/foundry/integrations");
  const tools = useApiResource<ToolRow[]>("/api/foundry/tools?limit=500");
  const [selectedId, setSelectedId] = React.useState("");
  const [name, setName] = React.useState("Primary Roblox bridge");
  const [baseUrl, setBaseUrl] = React.useState("http://127.0.0.1:8077");
  const [transport, setTransport] = React.useState("streamable_http");
  const [token, setToken] = React.useState("");
  const [configText, setConfigText] = React.useState('{\n  "protocolVersion": "2026-07-28",\n  "timeoutMs": 30000,\n  "httpAllowlist": []\n}');
  const [message, setMessage] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const selected = (integrations.data ?? []).find((integration) => integration.id === selectedId) ?? (integrations.data ?? [])[0] ?? null;
  const refresh = React.useCallback(() => { integrations.refresh(); tools.refresh(); }, [integrations.refresh, tools.refresh]);
  useRealtimeRefresh(refresh, ["tools", "events"]);
  const mutate = async (fn: () => Promise<unknown>, success: string) => { setBusy(true); setMessage(null); try { await fn(); setMessage(success); refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); } finally { setBusy(false); } };
  const create = () => mutate(() => apiFetch("/api/foundry/integrations", { method: "POST", body: JSON.stringify({ name, baseUrl, transport, token, kind: "tunneling", config: JSON.parse(configText) }) }), "Integration created; credential stored encrypted server-side.");
  const test = () => selected ? mutate(() => apiFetch(`/api/foundry/integrations/${selected.id}/test`, { method: "POST", body: "{}" }), "MCP handshake tested. Tool count is returned by the live bridge.") : undefined;
  const discover = () => selected ? mutate(() => apiFetch(`/api/foundry/integrations/${selected.id}/discover`, { method: "POST", body: "{}" }), "Runtime tools/list discovery queued on the persistent worker.") : undefined;
  const bootstrap = () => mutate(() => apiFetch("/api/foundry/integrations/bootstrap", { method: "POST", body: "{}" }), "Tunneling integration bootstrapped from environment values.");

  return (
    <div className="space-y-4">
      <SectionHeader eyebrow="Tool boundary" title="Integrations" description="Register MCP servers, HTTP APIs and the existing Tunneling bridge. Tool schemas are discovered at runtime and credentials never reach the browser." actions={<><Button variant="secondary" disabled={!config.bridgeBootstrapConfigured || busy} onClick={bootstrap}><Unplug className="h-3.5 w-3.5" />Bootstrap env bridge</Button><Button variant="secondary" onClick={refresh}><RefreshCw className="h-3.5 w-3.5" /></Button></>} />
      {message ? <InlineNotice tone={message.includes("not configured") || message.includes("failed") ? "amber" : "cyan"}>{message}</InlineNotice> : null}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Configured integrations</CardTitle><Badge tone="neutral">{integrations.data?.length ?? 0}</Badge></CardHeader><CardContent><EmptyOrError loading={integrations.loading} error={integrations.error} empty={!integrations.data?.length} title="No integrations" description="Create a Tunneling bridge integration with a real URL. No mock integration is presented as connected."><div className="space-y-2">{(integrations.data ?? []).map((integration) => <button key={integration.id} onClick={() => setSelectedId(integration.id)} className={`w-full rounded-lg border p-3 text-left ${selected?.id === integration.id ? "border-signal-cyan/40 bg-signal-cyan/8" : "border-line bg-graphite-950/50 hover:border-graphite-600"}`}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex items-center gap-2 text-xs font-medium text-ink"><Link2 className="h-3.5 w-3.5 text-signal-cyan" />{integration.name}</div><div className="mt-1 truncate font-mono text-2xs text-ink-faint">{integration.base_url}</div></div><StatusPill status={integration.status} /></div><div className="mt-2 flex flex-wrap gap-1"><Badge tone="violet">{integration.transport}</Badge><Badge tone="neutral">{integration.kind}</Badge>{integration.last_checked_at ? <Badge tone="neutral">checked {formatRelative(integration.last_checked_at)}</Badge> : null}</div>{integration.last_error ? <p className="mt-2 text-2xs text-signal-rose">{integration.last_error}</p> : null}</button>)}</div></EmptyOrError></CardContent></Card>
          <Card><CardHeader><CardTitle>Discovered tool schemas</CardTitle><Badge tone="cyan">{tools.data?.length ?? 0}</Badge></CardHeader><CardContent><EmptyOrError loading={tools.loading} error={tools.error} empty={!tools.data?.length} title="No schemas discovered" description="Test an integration, then run runtime MCP discovery. FOUNDRY never freezes a tool inventory."><div className="max-h-[480px] space-y-1.5 overflow-auto pr-1">{selected && <div className="mb-2 flex gap-2"><Button size="sm" variant="secondary" disabled={busy} onClick={test}><ShieldCheck className="h-3 w-3" />Test MCP</Button><Button size="sm" disabled={busy} onClick={discover}><Wrench className="h-3 w-3" />Discover tools</Button></div>}{(tools.data ?? []).map((tool) => <details key={tool.id} className="rounded border border-line bg-graphite-950/50 p-2"><summary className="flex cursor-pointer items-center justify-between gap-2"><span className="truncate font-mono text-2xs text-ink">{tool.name}</span><StatusPill status={tool.availability} /></summary><p className="mt-2 text-2xs leading-relaxed text-ink-faint">{tool.description}</p><JsonBlock value={tool.input_schema} className="mt-2 max-h-40" /><JsonBlock value={tool.metadata} className="mt-2 max-h-32" /></details>)}</div></EmptyOrError></CardContent></Card>
        </div>
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Add integration</CardTitle><KeyRound className="h-4 w-4 text-ink-faint" /></CardHeader><CardContent className="space-y-2.5"><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} /><Label>Bridge URL</Label><Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /><Label>Transport</Label><Select value={transport} onChange={(e) => setTransport(e.target.value)}><option value="streamable_http">Streamable HTTP</option><option value="stdio">stdio local connector</option><option value="http_api">HTTP API</option></Select><Label>Bridge token (sent once, encrypted at rest)</Label><Input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="server-side only" /><Label>Server config (JSON)</Label><Textarea rows={7} value={configText} onChange={(e) => setConfigText(e.target.value)} /><Button className="w-full" disabled={!config.encryptionConfigured || busy} onClick={create}><Plus className="h-3.5 w-3.5" />Create integration</Button>{!config.encryptionConfigured ? <p className="text-2xs text-signal-amber">FOUNDRY_SECRET_ENCRYPTION_KEY must be configured before secrets can be stored.</p> : null}</CardContent></Card>
          <Card><CardHeader><CardTitle>Security boundary</CardTitle><ShieldCheck className="h-4 w-4 text-signal-green" /></CardHeader><CardContent className="space-y-2 text-2xs leading-relaxed text-ink-muted"><div className="rounded border border-line bg-graphite-950/50 p-2">Bearer tokens, provider keys, owner credentials and tunnel credentials stay server-side and are redacted from API responses, events and audit details.</div><div className="rounded border border-line bg-graphite-950/50 p-2">stdio commands are allowlisted and spawned with shell:false. Untrusted agents cannot choose arbitrary host commands.</div><div className="rounded border border-line bg-graphite-950/50 p-2">Code, file and database tool kinds are registered as unavailable until an isolated executor is configured.</div></CardContent></Card>
          {selected ? <Card><CardHeader><CardTitle>Safe integration view</CardTitle></CardHeader><CardContent><JsonBlock value={asObject(selected)} className="max-h-64" /></CardContent></Card> : null}
        </div>
      </div>
      <ApiErrorNotice error={tools.error} />
    </div>
  );
}
