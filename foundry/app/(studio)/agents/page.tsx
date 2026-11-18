"use client";

import * as React from "react";
import { Bot, BrainCircuit, Copy, MemoryStick, Plus, RefreshCw, ShieldCheck, Sparkles, UserRound } from "lucide-react";
import { useApiResource, useFoundry } from "@/components/auth-gate";
import { useRealtimeRefresh } from "@/components/realtime";
import { ApiErrorNotice, EmptyOrError } from "@/components/page-helpers";
import {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, InlineNotice,
  Input, JsonBlock, Label, Select, SectionHeader, StatusPill, Tabs, TabsContent, TabsList, TabsTrigger, Textarea
} from "@/components/ui";
import { asObject, formatRelative, safeJson, shortId } from "@/lib/utils";

type AgentRow = Record<string, unknown> & { id: string; name: string; slug: string; description: string; status: string; enabled: boolean; system_instructions: string; model: unknown; tool_permissions: unknown; memory_config: unknown; updated_at: string };
type TemplateRow = { slug: string; name: string; description: string; category: string; is_roblox: boolean; config: Record<string, unknown> };
type MemoryRow = Record<string, unknown> & { id: string; title: string; content: string; version: number; updated_at: string };
type VersionRow = Record<string, unknown> & { id: string; version: number; change_note: string; created_at: string };

export default function AgentsPage() {
  const { apiFetch, config } = useFoundry();
  const agents = useApiResource<AgentRow[]>("/api/foundry/agents");
  const templates = useApiResource<TemplateRow[]>("/api/foundry/templates");
  const [selectedId, setSelectedId] = React.useState("");
  const [name, setName] = React.useState("Research Copilot");
  const [description, setDescription] = React.useState("General research, synthesis and scoped note taking.");
  const [instructions, setInstructions] = React.useState("Work only inside assigned tasks. Cite scoped knowledge. Never reveal credentials.");
  const [provider, setProvider] = React.useState("openai");
  const [model, setModel] = React.useState("gpt-5-mini");
  const [role, setRole] = React.useState("write");
  const [message, setMessage] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const selected = (agents.data ?? []).find((agent) => agent.id === selectedId) ?? (agents.data ?? [])[0] ?? null;
  const memories = useApiResource<MemoryRow[]>(selected ? `/api/foundry/memories?scope=agent&agentId=${selected.id}` : null);
  const versions = useApiResource<VersionRow[]>(selected ? null : null);
  const refresh = React.useCallback(() => { agents.refresh(); templates.refresh(); memories.refresh(); }, [agents.refresh, templates.refresh, memories.refresh]);
  useRealtimeRefresh(refresh, ["agents", "events", "approvals"]);
  const [memoryTitle, setMemoryTitle] = React.useState("Working preference");
  const [memoryContent, setMemoryContent] = React.useState("");

  const mutate = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true); setMessage(null);
    try { await fn(); setMessage(success); refresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };

  const createAgent = () => mutate(() => apiFetch("/api/foundry/agents", { method: "POST", body: JSON.stringify({
    name, description, systemInstructions: instructions, model: { provider, model },
    toolPermissions: { allow: ["mcp:*", "knowledge", "memory"], deny: [], maxRole: role, requireApproval: false },
    memoryConfig: { enabled: true, scope: "agent", retrievalLimit: 6, writePolicy: "explicit" },
    communicationPermissions: { direct: true, broadcast: false, task: true, artifact: true, handoff: true }
  }) }), "Agent created with isolated memory configuration.");
  const applyTemplate = (slug: string) => mutate(() => apiFetch(`/api/foundry/templates/${slug}/apply`, { method: "POST", body: "{}" }), `Template ${slug} applied.`);
  const clone = () => selected ? mutate(() => apiFetch(`/api/foundry/agents/${selected.id}/clone`, { method: "POST", body: "{}" }), "Agent cloned.") : undefined;
  const disable = () => selected ? mutate(() => apiFetch(`/api/foundry/agents/${selected.id}/disable`, { method: "POST", body: "{}" }), "Agent disabled.") : undefined;
  const bootstrap = () => mutate(() => apiFetch("/api/foundry/bootstrap", { method: "POST", body: "{}" }), "Templates and personal workspace bootstrapped.");
  const addMemory = () => selected ? mutate(() => apiFetch("/api/foundry/memories", { method: "POST", body: JSON.stringify({ scope: "agent", agentId: selected.id, title: memoryTitle, content: memoryContent, visibility: "private", metadata: { source: "agent-editor" } }) }), "Private agent memory created.") : undefined;

  return (
    <div className="space-y-4">
      <SectionHeader eyebrow="Registry" title="Agents" description="Configure identity, model, generation, tool permissions, communication, execution limits and first-class private memories." actions={<><Button variant="secondary" onClick={bootstrap} disabled={busy}><Sparkles className="h-3.5 w-3.5" />Bootstrap presets</Button><Button variant="secondary" onClick={refresh}><RefreshCw className="h-3.5 w-3.5" /></Button></>} />
      {message ? <InlineNotice tone={message.includes("not configured") || message.includes("failed") || message.includes("Invalid") ? "amber" : "cyan"}>{message}</InlineNotice> : null}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Agent roster</CardTitle><Badge tone="neutral">{agents.data?.length ?? 0}</Badge></CardHeader><CardContent>
            <EmptyOrError loading={agents.loading} error={agents.error} empty={!agents.data?.length} title="No agents registered" description="Create an agent or bootstrap the templates. No in-memory agents are shown as production records.">
              <div className="grid gap-2 md:grid-cols-2">{(agents.data ?? []).map((agent) => <button key={agent.id} onClick={() => setSelectedId(agent.id)} className={`rounded-lg border p-3 text-left transition-colors ${selected?.id === agent.id ? "border-signal-cyan/40 bg-signal-cyan/8" : "border-line bg-graphite-950/50 hover:border-graphite-600"}`}><div className="flex items-start justify-between gap-2"><div className="min-w-0"><div className="flex items-center gap-2 text-xs font-semibold text-ink"><Bot className="h-3.5 w-3.5 text-signal-cyan" /><span className="truncate">{agent.name}</span></div><div className="mt-1 line-clamp-2 text-2xs leading-relaxed text-ink-faint">{agent.description}</div></div><StatusPill status={agent.enabled ? agent.status : "disabled"} /></div><div className="mt-2 flex flex-wrap gap-1"><Badge tone="neutral">{String(asObject(agent.model).provider ?? "model")}</Badge><Badge tone="violet">agent memory</Badge><Badge tone="blue">role {String(asObject(agent.tool_permissions).maxRole ?? "write")}</Badge></div><div className="mt-2 font-mono text-2xs text-ink-faint">updated {formatRelative(agent.updated_at)} · {shortId(agent.id)}</div></button>)}</div>
            </EmptyOrError>
          </CardContent></Card>
          <Card><CardHeader><CardTitle>Agent memories</CardTitle>{selected ? <Badge tone="violet">scope: {selected.slug}</Badge> : <Badge tone="neutral">no agent</Badge>}</CardHeader><CardContent className="space-y-3">
            {!selected ? <EmptyState title="Select an agent" description="Agent memory is isolated by agentId and never mixed with project or workflow memory." /> : <>
              <div className="grid gap-2 sm:grid-cols-[1fr_2fr_auto]"><Input value={memoryTitle} onChange={(e) => setMemoryTitle(e.target.value)} placeholder="Memory title" /><Input value={memoryContent} onChange={(e) => setMemoryContent(e.target.value)} placeholder="Private, versioned memory content" /><Button disabled={!memoryContent || busy} onClick={addMemory}><Plus className="h-3.5 w-3.5" />Add</Button></div>
              <EmptyOrError loading={memories.loading} error={memories.error} empty={!memories.data?.length} title="No private memories" description="Add a durable memory. Updates are versioned by a database trigger.">
                <div className="space-y-2">{(memories.data ?? []).map((memory) => <div key={memory.id} className="rounded border border-line bg-graphite-950/60 p-2"><div className="flex items-center justify-between"><span className="text-2xs font-medium text-ink">{memory.title}</span><Badge tone="neutral">v{memory.version}</Badge></div><p className="mt-1 whitespace-pre-wrap text-2xs leading-relaxed text-ink-muted">{memory.content}</p><div className="mt-1 font-mono text-2xs text-ink-faint">{formatRelative(memory.updated_at)}</div></div>)}</div>
              </EmptyOrError>
              <p className="text-2xs leading-relaxed text-ink-faint">Version history and semantic retrieval are exposed through the memory API and Knowledge workspace. Lexical fallback is labeled when no embedding provider is configured.</p>
            </>}
          </CardContent></Card>
        </div>
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Create agent</CardTitle><UserRound className="h-4 w-4 text-ink-faint" /></CardHeader><CardContent className="space-y-2.5">
            <div><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div><Label>Description</Label><Input value={description} onChange={(e) => setDescription(e.target.value)} /></div>
            <div><Label>System instructions</Label><Textarea rows={5} value={instructions} onChange={(e) => setInstructions(e.target.value)} /></div>
            <div className="grid grid-cols-2 gap-2"><div><Label>Provider</Label><Select value={provider} onChange={(e) => setProvider(e.target.value)}><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="google">Google</option><option value="openai_compatible">Compatible</option></Select></div><div><Label>Model</Label><Input value={model} onChange={(e) => setModel(e.target.value)} /></div></div>
            <div><Label>Max bridge role</Label><Select value={role} onChange={(e) => setRole(e.target.value)}><option value="read">read</option><option value="write">write</option><option value="admin">admin</option></Select></div>
            <Button className="w-full" disabled={!config.databaseConfigured || busy} onClick={createAgent}><Plus className="h-3.5 w-3.5" />Create durable agent</Button>
          </CardContent></Card>
          <Card><CardHeader><CardTitle>Selected agent</CardTitle><div className="flex gap-1"><Button variant="secondary" size="icon" disabled={!selected || busy} onClick={clone}><Copy className="h-3.5 w-3.5" /></Button><Button variant="danger" size="icon" disabled={!selected || busy} onClick={disable}><ShieldCheck className="h-3.5 w-3.5" /></Button></div></CardHeader><CardContent>{selected ? <Tabs defaultValue="config"><TabsList><TabsTrigger value="config">Config</TabsTrigger><TabsTrigger value="memory">Memory</TabsTrigger></TabsList><TabsContent value="config" className="mt-3"><JsonBlock value={{ systemInstructions: selected.system_instructions, model: selected.model, toolPermissions: selected.tool_permissions, memoryConfig: selected.memory_config }} className="max-h-80" /></TabsContent><TabsContent value="memory" className="mt-3"><JsonBlock value={asObject(selected.memory_config)} className="max-h-52" /></TabsContent></Tabs> : <EmptyState title="No selection" description="Choose an agent to inspect its normalized configuration." />}</CardContent></Card>
          <Card><CardHeader><CardTitle>Templates & role presets</CardTitle><Badge tone="violet">{templates.data?.length ?? 0}</Badge></CardHeader><CardContent><div className="max-h-72 space-y-1.5 overflow-auto pr-1">{(templates.data ?? []).map((template) => <button key={template.slug} onClick={() => applyTemplate(template.slug)} className="flex w-full items-center justify-between gap-2 rounded border border-line bg-graphite-950/50 p-2 text-left hover:border-signal-cyan/30"><div className="min-w-0"><div className="truncate text-2xs font-medium text-ink">{template.name}</div><div className="truncate text-2xs text-ink-faint">{template.description}</div></div><Badge tone={template.is_roblox ? "violet" : "neutral"}>{template.category}</Badge></button>)}{!templates.data?.length ? <EmptyState title="No templates" description="Bootstrap the built-in general and Roblox presets." action={<Button variant="secondary" onClick={bootstrap}>Bootstrap</Button>} /> : null}</div></CardContent></Card>
        </div>
      </div>
      <ApiErrorNotice error={templates.error} />
    </div>
  );
}
