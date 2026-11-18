"use client";

import * as React from "react";
import { FolderKanban, ListTodo, NotebookPen, Plus, RefreshCw, Save } from "lucide-react";
import { useApiResource, useFoundry } from "@/components/auth-gate";
import { useRealtimeRefresh } from "@/components/realtime";
import { ApiErrorNotice, EmptyOrError } from "@/components/page-helpers";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, InlineNotice, Input, Label, SectionHeader, Select, StatusPill, Textarea } from "@/components/ui";
import { formatRelative, shortId } from "@/lib/utils";

type ProjectRow = { id: string; name: string; slug: string; description: string; created_at: string };
type ItemRow = Record<string, unknown> & { id: string; project_id: string; kind: string; title: string; content: string; status: string; created_at: string };

export default function ProjectsPage() {
  const { apiFetch, config } = useFoundry();
  const projects = useApiResource<ProjectRow[]>("/api/foundry/projects");
  const [selectedId, setSelectedId] = React.useState("");
  const selected = (projects.data ?? []).find((project) => project.id === selectedId) ?? (projects.data ?? [])[0] ?? null;
  const items = useApiResource<ItemRow[]>(selected ? `/api/foundry/projects/${selected.id}/items` : null);
  const [projectName, setProjectName] = React.useState("Client automation");
  const [itemKind, setItemKind] = React.useState("task");
  const [title, setTitle] = React.useState("");

  const [content, setContent] = React.useState("");
  const [message, setMessage] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const refresh = React.useCallback(() => { projects.refresh(); items.refresh(); }, [projects.refresh, items.refresh]);
  useRealtimeRefresh(refresh, ["workspace_items", "events"]);
  const mutate = async (fn: () => Promise<unknown>, success: string) => { setBusy(true); setMessage(null); try { await fn(); setMessage(success); refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); } finally { setBusy(false); } };
  const createProject = () => mutate(() => apiFetch("/api/foundry/projects", { method: "POST", body: JSON.stringify({ name: projectName, description: "Persistent project workspace." }) }), "Project created.");
  const createItem = () => selected ? mutate(() => apiFetch(`/api/foundry/projects/${selected.id}/items`, { method: "POST", body: JSON.stringify({ kind: itemKind, title, content, status: itemKind === "task" ? "open" : "active" }) }), "Workspace item saved.") : undefined;
  const complete = (id: string) => mutate(() => apiFetch(`/api/foundry/workspace-items/${id}`, { method: "PATCH", body: JSON.stringify({ status: "done" }) }), "Item marked done.");

  return (
    <div className="space-y-4">
      <SectionHeader eyebrow="Personal workspace" title="Projects" description="Useful without Roblox Studio: notes, tasks, conversations, prompt library, saved outputs and activity history in real persistent project records." actions={<Button variant="secondary" onClick={refresh}><RefreshCw className="h-3.5 w-3.5" />Refresh</Button>} />
      {message ? <InlineNotice tone={message.includes("not configured") ? "amber" : "cyan"}>{message}</InlineNotice> : null}
      <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)_340px]">
        <Card><CardHeader><CardTitle>Projects</CardTitle><Badge tone="neutral">{projects.data?.length ?? 0}</Badge></CardHeader><CardContent><EmptyOrError loading={projects.loading} error={projects.error} empty={!projects.data?.length} title="No projects" description="Create a project to isolate tasks, knowledge and memories."><div className="space-y-1.5">{(projects.data ?? []).map((project) => <button key={project.id} onClick={() => setSelectedId(project.id)} className={`w-full rounded border p-2 text-left ${selected?.id === project.id ? "border-signal-cyan/40 bg-signal-cyan/8" : "border-line bg-graphite-950/50 hover:border-graphite-600"}`}><div className="truncate text-2xs font-medium text-ink">{project.name}</div><div className="mt-0.5 truncate font-mono text-2xs text-ink-faint">{project.slug} · {shortId(project.id)}</div></button>)}</div></EmptyOrError></CardContent></Card>
        <Card><CardHeader><CardTitle>{selected?.name ?? "Workspace items"}</CardTitle>{selected ? <Badge tone="cyan">{formatRelative(selected.created_at)}</Badge> : null}</CardHeader><CardContent><EmptyOrError loading={items.loading} error={items.error} empty={!selected || !items.data?.length} title="No workspace items" description="Add a note, task, prompt, conversation or saved output.">{<div className="grid gap-2 md:grid-cols-2">{(items.data ?? []).map((item) => <div key={item.id} className="rounded-lg border border-line bg-graphite-950/50 p-2.5"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><Badge tone="neutral">{item.kind}</Badge><div className="mt-1.5 truncate text-xs font-medium text-ink">{item.title}</div></div><StatusPill status={item.status} /></div><p className="mt-2 line-clamp-3 whitespace-pre-wrap text-2xs leading-relaxed text-ink-faint">{item.content}</p><div className="mt-2 flex items-center justify-between"><span className="font-mono text-2xs text-ink-faint">{formatRelative(item.created_at)}</span>{item.status !== "done" ? <Button size="sm" variant="secondary" disabled={busy} onClick={() => complete(item.id)}>Done</Button> : null}</div></div>)}</div>}</EmptyOrError></CardContent></Card>
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Create project</CardTitle><FolderKanban className="h-4 w-4 text-ink-faint" /></CardHeader><CardContent className="space-y-2"><Label>Name</Label><Input value={projectName} onChange={(e) => setProjectName(e.target.value)} /><Button className="w-full" disabled={!config.databaseConfigured || busy} onClick={createProject}><Plus className="h-3.5 w-3.5" />Create project</Button></CardContent></Card>
          <Card><CardHeader><CardTitle>Add workspace item</CardTitle><NotebookPen className="h-4 w-4 text-ink-faint" /></CardHeader><CardContent className="space-y-2"><Label>Kind</Label><Select value={itemKind} onChange={(e) => setItemKind(e.target.value)}>{["note", "task", "conversation", "prompt", "output", "saved_output", "attachment"].map((kind) => <option key={kind} value={kind}>{kind}</option>)}</Select><Label>Title</Label><Input value={title} onChange={(e) => setTitle(e.target.value)} /><Label>Content</Label><Textarea rows={6} value={content} onChange={(e) => setContent(e.target.value)} /><Button className="w-full" disabled={!selected || !title || busy} onClick={createItem}><Save className="h-3.5 w-3.5" />Save item</Button><p className="text-2xs leading-relaxed text-ink-faint">Attachments are metadata-only records until Supabase Storage is configured; FOUNDRY does not claim file upload works.</p></CardContent></Card>
        </div>
      </div>
      <ApiErrorNotice error={projects.error} />
    </div>
  );
}
