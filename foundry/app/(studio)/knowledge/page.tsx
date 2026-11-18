"use client";

import * as React from "react";
import { BookOpen, BrainCircuit, Database, FileText, Search, Sparkles } from "lucide-react";
import { useApiResource, useFoundry } from "@/components/auth-gate";
import { ApiErrorNotice, EmptyOrError } from "@/components/page-helpers";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, InlineNotice, Input, JsonBlock, Label, SectionHeader, Select, StatusPill, Tabs, TabsContent, TabsList, TabsTrigger, Textarea } from "@/components/ui";
import { asObject, formatRelative } from "@/lib/utils";

type ProjectRow = { id: string; name: string };
type AgentRow = { id: string; name: string };
type DocumentRow = { id: string; title: string; project_id: string | null; content_length: number; created_at: string };
type MemoryRow = Record<string, unknown> & { id: string; scope: string; title: string; content: string; version: number; updated_at: string };
type SearchResult = { mode: string; embedding?: Record<string, unknown>; results: Array<Record<string, unknown>> };

export default function KnowledgePage() {
  const { apiFetch, config } = useFoundry();
  const projects = useApiResource<ProjectRow[]>("/api/foundry/projects");
  const agents = useApiResource<AgentRow[]>("/api/foundry/agents");
  const documents = useApiResource<DocumentRow[]>("/api/foundry/knowledge");
  const [scope, setScope] = React.useState("agent");
  const [agentId, setAgentId] = React.useState("");
  const [projectId, setProjectId] = React.useState("");
  const memories = useApiResource<MemoryRow[]>(`/api/foundry/memories?${new URLSearchParams({ scope, ...(agentId ? { agentId } : {}), ...(projectId ? { projectId } : {}) })}`);
  const [docTitle, setDocTitle] = React.useState("Architecture notes");
  const [docContent, setDocContent] = React.useState("FOUNDRY separates agent, project, team and workflow memory.");
  const [query, setQuery] = React.useState("memory isolation");
  const [memoryTitle, setMemoryTitle] = React.useState("Scoped context");
  const [memoryContent, setMemoryContent] = React.useState("Keep project knowledge separate from private agent context.");
  const [searchResult, setSearchResult] = React.useState<SearchResult | null>(null);
  const [message, setMessage] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const mutate = async (fn: () => Promise<unknown>, success: string) => { setBusy(true); setMessage(null); try { const result = await fn(); if (result && typeof result === "object" && "results" in result) setSearchResult(result as SearchResult); setMessage(success); documents.refresh(); memories.refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); } finally { setBusy(false); } };
  const createDoc = () => mutate(() => apiFetch("/api/foundry/knowledge", { method: "POST", body: JSON.stringify({ title: docTitle, content: docContent, projectId: projectId || null, mimeType: "text/plain" }) }), "Knowledge document chunked and stored.");
  const createMemory = () => mutate(() => apiFetch("/api/foundry/memories", { method: "POST", body: JSON.stringify({ scope, title: memoryTitle, content: memoryContent, agentId: scope === "agent" ? agentId || agents.data?.[0]?.id : null, projectId: projectId || null, visibility: scope === "project" ? "project" : "private", metadata: { source: "knowledge-workspace" } }) }), "Scoped memory created.");
  const searchKnowledge = () => mutate(() => apiFetch("/api/foundry/knowledge/search", { method: "POST", body: JSON.stringify({ query, projectId: projectId || null, limit: 8 }) }), "Knowledge search completed.");
  const searchMemories = () => mutate(() => apiFetch("/api/foundry/memories/search", { method: "POST", body: JSON.stringify({ query, scope, agentId: scope === "agent" ? agentId || agents.data?.[0]?.id : null, projectId: projectId || null, limit: 8 }) }), "Scoped memory search completed.");

  return (
    <div className="space-y-4">
      <SectionHeader eyebrow="Scoped context" title="Knowledge" description="First-class agent, project, team and workflow memories with version isolation, pgvector schema and honest lexical fallback labeling when no embedding provider is configured." />
      {message ? <InlineNotice tone={message.includes("not configured") ? "amber" : "cyan"}>{message}</InlineNotice> : null}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_360px]">
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Search & citations</CardTitle>{searchResult ? <Badge tone={searchResult.mode === "vector" ? "green" : "amber"}>{searchResult.mode}</Badge> : <Badge tone="neutral">idle</Badge>}</CardHeader><CardContent className="space-y-3"><div className="flex gap-2"><Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search knowledge or scoped memory…" /><Button variant="secondary" disabled={busy} onClick={searchKnowledge}><Search className="h-3.5 w-3.5" />Knowledge</Button><Button disabled={busy} onClick={searchMemories}><BrainCircuit className="h-3.5 w-3.5" />Memory</Button></div>{searchResult ? <><div className="text-2xs text-ink-faint">Retrieval mode is {searchResult.mode}. {searchResult.mode === "lexical" ? "No semantic embedding was fabricated." : "pgvector cosine similarity was used."}</div><div className="space-y-2">{searchResult.results.map((result, index) => <div key={String(result.id ?? index)} className="rounded border border-line bg-graphite-950/50 p-2"><div className="flex items-center justify-between gap-2"><span className="truncate text-2xs font-medium text-ink">{String(result.title ?? `Result ${index + 1}`)}</span><Badge tone="cyan">sim {Number(result.similarity ?? 0).toFixed(2)}</Badge></div><p className="mt-1 line-clamp-3 whitespace-pre-wrap text-2xs leading-relaxed text-ink-faint">{String(result.content ?? "")}</p><JsonBlock value={result.citation ?? {}} className="mt-2 max-h-24" /></div>)}</div></> : <EmptyState title="No search results yet" description="Run a knowledge or memory search. Results include source citations and retrieval mode." />}</CardContent></Card>
          <Card><CardHeader><CardTitle>Knowledge documents</CardTitle><Badge tone="neutral">{documents.data?.length ?? 0}</Badge></CardHeader><CardContent><EmptyOrError loading={documents.loading} error={documents.error} empty={!documents.data?.length} title="No documents" description="Store persistent project or general notes with chunk records."><div className="space-y-1.5">{(documents.data ?? []).map((doc) => <div key={doc.id} className="flex items-center justify-between gap-3 rounded border border-line bg-graphite-950/50 p-2"><div className="min-w-0"><div className="truncate text-2xs font-medium text-ink"><FileText className="mr-1 inline h-3 w-3 text-signal-green" />{doc.title}</div><div className="font-mono text-2xs text-ink-faint">{doc.content_length} chars · {formatRelative(doc.created_at)}</div></div><Badge tone={doc.project_id ? "violet" : "neutral"}>{doc.project_id ? "project" : "general"}</Badge></div>)}</div></EmptyOrError></CardContent></Card>
        </div>
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Context filters</CardTitle><Database className="h-4 w-4 text-ink-faint" /></CardHeader><CardContent className="space-y-2"><Label>Memory scope</Label><Select value={scope} onChange={(e) => setScope(e.target.value)}>{["agent", "project", "team", "workflow"].map((value) => <option key={value} value={value}>{value}</option>)}</Select><Label>Agent</Label><Select value={agentId} onChange={(e) => setAgentId(e.target.value)}><option value="">First available</option>{(agents.data ?? []).map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</Select><Label>Project</Label><Select value={projectId} onChange={(e) => setProjectId(e.target.value)}><option value="">General / no project</option>{(projects.data ?? []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</Select><p className="text-2xs leading-relaxed text-ink-faint">Private agent memory cannot be retrieved by a project or team query without metadata.explicitShare=true.</p></CardContent></Card>
          <Card><CardHeader><CardTitle>Add knowledge</CardTitle><BookOpen className="h-4 w-4 text-ink-faint" /></CardHeader><CardContent className="space-y-2"><Label>Title</Label><Input value={docTitle} onChange={(e) => setDocTitle(e.target.value)} /><Label>Content</Label><Textarea rows={5} value={docContent} onChange={(e) => setDocContent(e.target.value)} /><Button className="w-full" disabled={!config.databaseConfigured || busy} onClick={createDoc}><Sparkles className="h-3.5 w-3.5" />Chunk & store</Button></CardContent></Card>
          <Card><CardHeader><CardTitle>Add scoped memory</CardTitle><Badge tone="violet">versioned</Badge></CardHeader><CardContent className="space-y-2"><Label>Title</Label><Input value={memoryTitle} onChange={(e) => setMemoryTitle(e.target.value)} /><Label>Content</Label><Textarea rows={4} value={memoryContent} onChange={(e) => setMemoryContent(e.target.value)} /><Button variant="secondary" className="w-full" disabled={busy} onClick={createMemory}>Create memory</Button></CardContent></Card>
          <Card><CardHeader><CardTitle>Memories in current scope</CardTitle><Badge tone="neutral">{memories.data?.length ?? 0}</Badge></CardHeader><CardContent><div className="max-h-52 space-y-1.5 overflow-auto">{(memories.data ?? []).map((memory) => <div key={memory.id} className="rounded border border-line bg-graphite-950/50 p-2"><div className="flex items-center justify-between"><span className="truncate text-2xs text-ink">{memory.title}</span><Badge tone="neutral">v{memory.version}</Badge></div><p className="mt-1 line-clamp-2 text-2xs text-ink-faint">{memory.content}</p></div>)}{!memories.data?.length ? <EmptyState title="No scoped memories" description="Create a memory in the selected scope." /> : null}</div></CardContent></Card>
        </div>
      </div>
      <ApiErrorNotice error={memories.error} />
    </div>
  );
}
