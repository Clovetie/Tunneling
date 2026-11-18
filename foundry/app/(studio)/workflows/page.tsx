"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { Boxes, GitBranch, Play, Plus, RefreshCw, Sparkles } from "lucide-react";
import { useApiResource, useFoundry } from "@/components/auth-gate";
import { useRealtimeRefresh } from "@/components/realtime";
import { ApiErrorNotice, EmptyOrError } from "@/components/page-helpers";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, InlineNotice, Input, JsonBlock, Label, SectionHeader, StatusPill, Textarea } from "@/components/ui";
import { formatRelative, shortId } from "@/lib/utils";

type WorkflowRow = Record<string, unknown> & { id: string; name: string; description: string; graph: unknown; updated_at: string };
type RunRow = Record<string, unknown> & { id: string; workflow_id: string; objective: string | null; mode: string; status: string; output?: unknown; created_at: string; finished_at?: string | null };
type TaskRow = Record<string, unknown> & { id: string; key: string; node_id: string; kind: string; status: string; attempts: number; output?: unknown; last_error?: string | null };

export default function WorkflowsPage() {
  const params = useSearchParams();
  const { apiFetch, config } = useFoundry();
  const workflows = useApiResource<WorkflowRow[]>("/api/foundry/workflows");
  const allRuns = useApiResource<RunRow[]>("/api/foundry/runs?limit=100");
  const initialRun = params.get("run");
  const [selectedWorkflowId, setSelectedWorkflowId] = React.useState("");
  const [selectedRunId, setSelectedRunId] = React.useState(initialRun ?? "");
  const [objective, setObjective] = React.useState("Research a topic, outline a plan, and produce a cited brief.");
  const [name, setName] = React.useState("Research workflow");
  const [message, setMessage] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const selectedWorkflow = (workflows.data ?? []).find((workflow) => workflow.id === selectedWorkflowId) ?? (workflows.data ?? [])[0] ?? null;
  const tasks = useApiResource<TaskRow[]>(selectedRunId ? `/api/foundry/runs/${selectedRunId}/tasks` : null);
  const refresh = React.useCallback(() => { workflows.refresh(); allRuns.refresh(); tasks.refresh(); }, [workflows.refresh, allRuns.refresh, tasks.refresh]);
  useRealtimeRefresh(refresh, ["workflow_runs", "tasks", "events"]);

  const mutate = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true); setMessage(null);
    try { const result = await fn(); setMessage(success); if (result && typeof result === "object" && "runId" in result) setSelectedRunId(String((result as Record<string, unknown>).runId)); refresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  const createWorkflow = () => mutate(() => apiFetch("/api/foundry/workflows", { method: "POST", body: JSON.stringify({
    name, description: "Created from Workflows.", graph: { nodes: [{ id: "task", type: "task", position: { x: 80, y: 80 }, data: { label: "Durable task", config: { title: "Execute objective" } } }, { id: "output", type: "output", position: { x: 340, y: 80 }, data: { label: "Output", config: { format: "json" } } }], edges: [{ id: "e1", source: "task", target: "output" }] }
  }) }), "Workflow created and graph validated.");
  const runGraph = () => selectedWorkflow ? mutate(() => apiFetch(`/api/foundry/workflows/${selectedWorkflow.id}/runs`, { method: "POST", body: JSON.stringify({ mode: "graph", input: { objective } }) }), "Graph run queued as a durable job.") : undefined;
  const runObjective = () => selectedWorkflow ? mutate(() => apiFetch(`/api/foundry/workflows/${selectedWorkflow.id}/runs`, { method: "POST", body: JSON.stringify({ mode: "objective", objective }) }), "Objective planning run queued; a model provider and persistent worker are required.") : undefined;

  return (
    <div className="space-y-4">
      <SectionHeader eyebrow="Durable orchestration" title="Workflows" description="Graph and objective runs produce real task records with dependencies, retries, leases, artifacts and supervisor review. Conversation history is never the sole state." actions={<Button variant="secondary" onClick={refresh}><RefreshCw className="h-3.5 w-3.5" />Refresh</Button>} />
      {message ? <InlineNotice tone={message.includes("not configured") || message.includes("required") ? "amber" : "cyan"}>{message}</InlineNotice> : null}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Saved workflows</CardTitle><Badge tone="neutral">{workflows.data?.length ?? 0}</Badge></CardHeader><CardContent>
            <EmptyOrError loading={workflows.loading} error={workflows.error} empty={!workflows.data?.length} title="No workflows" description="Create a validated graph here or use the visual Agent Builder.">
              <div className="space-y-2">{(workflows.data ?? []).map((workflow) => <button key={workflow.id} onClick={() => setSelectedWorkflowId(workflow.id)} className={`flex w-full items-center justify-between gap-3 rounded-lg border p-2.5 text-left ${selectedWorkflow?.id === workflow.id ? "border-signal-cyan/40 bg-signal-cyan/8" : "border-line bg-graphite-950/50 hover:border-graphite-600"}`}><div className="min-w-0"><div className="truncate text-xs font-medium text-ink">{workflow.name}</div><div className="mt-0.5 truncate text-2xs text-ink-faint">{workflow.description}</div></div><div className="flex items-center gap-2"><Badge tone="neutral">{Array.isArray((workflow.graph as Record<string, unknown> | undefined)?.nodes) ? ((workflow.graph as Record<string, unknown>).nodes as unknown[]).length : 0} nodes</Badge><span className="font-mono text-2xs text-ink-faint">{formatRelative(workflow.updated_at)}</span></div></button>)}</div>
            </EmptyOrError>
          </CardContent></Card>
          <Card><CardHeader><CardTitle>Runs</CardTitle><Badge tone="violet">{allRuns.data?.length ?? 0}</Badge></CardHeader><CardContent><EmptyOrError loading={allRuns.loading} error={allRuns.error} empty={!allRuns.data?.length} title="No durable runs" description="Run a graph or objective workflow to create persistent task records."><div className="max-h-80 space-y-1.5 overflow-auto pr-1">{(allRuns.data ?? []).map((run) => <button key={run.id} onClick={() => setSelectedRunId(run.id)} className={`flex w-full items-center justify-between gap-3 rounded border p-2 text-left ${selectedRunId === run.id ? "border-signal-cyan/40 bg-signal-cyan/8" : "border-line bg-graphite-950/50 hover:border-graphite-600"}`}><div className="min-w-0"><div className="truncate text-2xs text-ink">{String(run.objective ?? `Graph run ${shortId(run.id)}`)}</div><div className="font-mono text-2xs text-ink-faint">{run.mode} · {formatRelative(run.created_at)}</div></div><StatusPill status={run.status} /></button>)}</div></EmptyOrError></CardContent></Card>
          <Card><CardHeader><CardTitle>Execution graph / intermediate results</CardTitle>{selectedRunId ? <Badge tone="cyan">{shortId(selectedRunId)}</Badge> : <Badge tone="neutral">no run</Badge>}</CardHeader><CardContent>{selectedRunId ? <div className="grid gap-2 md:grid-cols-2">{(tasks.data ?? []).map((task) => <div key={task.id} className="rounded border border-line bg-graphite-950/60 p-2"><div className="flex items-center justify-between"><span className="font-mono text-2xs text-ink">{task.key}</span><StatusPill status={task.status} /></div><div className="mt-1 font-mono text-2xs text-ink-faint">{task.kind} · attempts {task.attempts}</div><JsonBlock value={task.output ?? { error: task.last_error }} className="mt-2 max-h-36" /></div>)}</div> : <EmptyState title="Select a run" description="Task statuses and intermediate outputs are read from Postgres and refreshed over Realtime." />}</CardContent></Card>
        </div>
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Create workflow</CardTitle><Boxes className="h-4 w-4 text-ink-faint" /></CardHeader><CardContent className="space-y-2.5"><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} /><Button className="w-full" disabled={!config.databaseConfigured || busy} onClick={createWorkflow}><Plus className="h-3.5 w-3.5" />Create validated graph</Button><p className="text-2xs leading-relaxed text-ink-faint">The full 13-node editor is in Agent Builder. This quick form creates a minimal durable graph.</p></CardContent></Card>
          <Card><CardHeader><CardTitle>Launch run</CardTitle><Sparkles className="h-4 w-4 text-signal-violet" /></CardHeader><CardContent className="space-y-2.5"><Label>Objective / graph input</Label><Textarea rows={5} value={objective} onChange={(e) => setObjective(e.target.value)} /><div className="grid grid-cols-2 gap-2"><Button variant="secondary" disabled={!selectedWorkflow || busy} onClick={runGraph}><GitBranch className="h-3.5 w-3.5" />Run graph</Button><Button disabled={!selectedWorkflow || busy} onClick={runObjective}><Play className="h-3.5 w-3.5" />Supervise objective</Button></div>{selectedWorkflow ? <JsonBlock value={selectedWorkflow.graph} className="max-h-56" /> : <EmptyState title="No workflow selected" description="Create or select a saved workflow." />}</CardContent></Card>
          <Card><CardHeader><CardTitle>Supervisor pattern</CardTitle></CardHeader><CardContent><div className="space-y-1.5 text-2xs text-ink-muted">{["objective", "plan", "select agents", "assign durable tasks", "monitor", "handle failures / blockers", "review", "deliver"].map((step, index) => <div key={step} className="flex items-center gap-2 rounded border border-line bg-graphite-950/50 px-2 py-1.5"><span className="font-mono text-signal-cyan/70">{String(index + 1).padStart(2, "0")}</span>{step}</div>)}</div></CardContent></Card>
        </div>
      </div>
      <ApiErrorNotice error={tasks.error} />
    </div>
  );
}
