"use client";

import * as React from "react";
import { Activity, CheckCircle2, CircleOff, Clock3, MessageSquare, RefreshCw, ShieldCheck, Terminal } from "lucide-react";
import { useApiResource, useFoundry } from "@/components/auth-gate";
import { useRealtimeRefresh } from "@/components/realtime";
import { ApiErrorNotice, EmptyOrError } from "@/components/page-helpers";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, InlineNotice, Input, JsonBlock, Label, SectionHeader, StatusPill, Tabs, TabsContent, TabsList, TabsTrigger, Textarea } from "@/components/ui";
import { asObject, formatRelative, shortId } from "@/lib/utils";

type ActivityData = {
  events?: Array<Record<string, unknown>>;
  messages?: Array<Record<string, unknown>>;
  approvals?: Array<Record<string, unknown>>;
  audit?: Array<Record<string, unknown>>;
  jobs?: Array<Record<string, unknown>>;
  workers?: Array<Record<string, unknown>>;
  chain?: { valid: boolean; count: number; firstMismatchId?: number };
};

export default function ActivityPage() {
  const { apiFetch } = useFoundry();
  const activity = useApiResource<ActivityData>("/api/foundry/activity?limit=120");
  const [message, setMessage] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [note, setNote] = React.useState("Reviewed in FOUNDRY.");
  const refresh = React.useCallback(() => activity.refresh(), [activity.refresh]);
  const realtime = useRealtimeRefresh(refresh, ["events", "approvals", "messages", "tasks", "workflow_runs"]);
  const decide = async (id: string, decision: "approve" | "deny") => {
    setBusy(true); setMessage(null);
    try { await apiFetch(`/api/foundry/approvals/${id}/decision`, { method: "POST", body: JSON.stringify({ decision, note }) }); setMessage(`Approval ${decision}d; durable continuation queued.`); refresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  const pending = (activity.data?.approvals ?? []).filter((item) => item.status === "pending");
  return (
    <div className="space-y-4">
      <SectionHeader eyebrow="Observability" title="Activity" description="Execution timeline, human approvals, agent messages, durable jobs, worker heartbeats and a hash-chained audit log." actions={<><Badge tone={activity.data?.chain?.valid ? "green" : "rose"}>audit {activity.data?.chain?.valid ? "valid" : "unavailable"}</Badge><Badge tone={realtime === "subscribed" ? "green" : "amber"}>realtime {realtime}</Badge><Button variant="secondary" onClick={refresh}><RefreshCw className="h-3.5 w-3.5" />Refresh</Button></>} />
      {message ? <InlineNotice tone={message.includes("failed") ? "rose" : "cyan"}>{message}</InlineNotice> : null}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_360px]">
        <Card><CardHeader><CardTitle>Searchable execution history</CardTitle><Badge tone="neutral">{activity.data?.events?.length ?? 0} events</Badge></CardHeader><CardContent><Tabs defaultValue="events"><TabsList><TabsTrigger value="events">Timeline</TabsTrigger><TabsTrigger value="messages">Messages</TabsTrigger><TabsTrigger value="jobs">Jobs</TabsTrigger><TabsTrigger value="audit">Audit</TabsTrigger></TabsList>
          <TabsContent value="events" className="mt-3"><EmptyOrError loading={activity.loading} error={activity.error} empty={!activity.data?.events?.length} title="No execution events" description="Events are emitted for task starts, model chunks, tool calls, approvals, retries and final results."><div className="max-h-[560px] space-y-1 overflow-auto pr-1">{(activity.data?.events ?? []).map((event) => <div key={String(event.id)} className="flex items-start justify-between gap-3 rounded border border-line bg-graphite-950/50 p-2"><div className="min-w-0"><div className="flex items-center gap-2"><span className={`h-1.5 w-1.5 rounded-full ${event.level === "error" ? "bg-signal-rose" : event.level === "warn" ? "bg-signal-amber" : "bg-signal-cyan"}`} /><span className="font-mono text-2xs text-ink">{String(event.kind)}</span></div><div className="mt-1 truncate text-2xs text-ink-faint">{JSON.stringify(event.data ?? {}).slice(0, 220)}</div></div><span className="shrink-0 font-mono text-2xs text-ink-faint">{formatRelative(event.created_at as string | undefined)}</span></div>)}</div></EmptyOrError></TabsContent>
          <TabsContent value="messages" className="mt-3"><div className="space-y-2">{(activity.data?.messages ?? []).map((msg) => <div key={String(msg.id)} className="rounded border border-line bg-graphite-950/50 p-2"><div className="flex items-center justify-between"><span className="font-mono text-2xs text-signal-violet">{String(msg.channel)} · {String(msg.transport)}</span><span className="font-mono text-2xs text-ink-faint">{formatRelative(msg.created_at as string | undefined)}</span></div><p className="mt-1 whitespace-pre-wrap text-2xs text-ink-muted">{String(msg.content)}</p></div>)}{!activity.data?.messages?.length ? <EmptyState title="No messages" description="Direct, broadcast, task, artifact and handoff messages appear here." /> : null}</div></TabsContent>
          <TabsContent value="jobs" className="mt-3"><div className="space-y-1.5">{(activity.data?.jobs ?? []).map((job) => <div key={String(job.id)} className="flex items-center justify-between rounded border border-line bg-graphite-950/50 p-2"><div className="min-w-0"><div className="font-mono text-2xs text-ink">{String(job.type)}</div><div className="truncate font-mono text-2xs text-ink-faint">{shortId(String(job.id))} · attempts {String(job.attempts)}/{String(job.max_attempts)}</div></div><StatusPill status={String(job.status)} /></div>)}{!activity.data?.jobs?.length ? <EmptyState title="No durable jobs" description="Vercel enqueues jobs here; the external worker claims them." /> : null}</div></TabsContent>
          <TabsContent value="audit" className="mt-3"><JsonBlock value={(activity.data?.audit ?? []).slice(0, 30)} className="max-h-[480px]" /></TabsContent>
        </Tabs></CardContent></Card>
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Human approval gates</CardTitle><Badge tone={pending.length ? "amber" : "green"}>{pending.length} pending</Badge></CardHeader><CardContent className="space-y-2"><Label>Decision note</Label><Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />{pending.map((approval) => <div key={String(approval.id)} className="rounded border border-line bg-graphite-950/50 p-2"><div className="flex items-center justify-between"><span className="font-mono text-2xs text-ink">{String(approval.kind)}</span><StatusPill status="pending" /></div><p className="mt-1 text-2xs leading-relaxed text-ink-muted">{String(approval.reason)}</p><JsonBlock value={asObject(approval.payload)} className="mt-2 max-h-32" /><div className="mt-2 flex gap-2"><Button size="sm" disabled={busy} onClick={() => decide(String(approval.id), "approve")}><CheckCircle2 className="h-3 w-3" />Approve</Button><Button size="sm" variant="danger" disabled={busy} onClick={() => decide(String(approval.id), "deny")}><CircleOff className="h-3 w-3" />Deny</Button></div></div>)}{!pending.length ? <EmptyState title="No pending approvals" description="Destructive tools, publishing, external communication, credential changes and human-approval nodes create gates." /> : null}</CardContent></Card>
          <Card><CardHeader><CardTitle>Workers & audit chain</CardTitle></CardHeader><CardContent className="space-y-2"><div className="grid grid-cols-2 gap-2"><div className="rounded border border-line bg-graphite-950/50 p-2"><div className="text-2xs text-ink-faint">Audit entries</div><div className="mt-1 font-mono text-sm text-ink">{activity.data?.chain?.count ?? 0}</div></div><div className="rounded border border-line bg-graphite-950/50 p-2"><div className="text-2xs text-ink-faint">Chain</div><div className="mt-1"><StatusPill status={activity.data?.chain?.valid ? "ok" : "failed"} /></div></div></div><div className="space-y-1.5">{(activity.data?.workers ?? []).map((worker) => <div key={String(worker.id)} className="flex items-center justify-between rounded border border-line bg-graphite-950/50 p-2"><span className="font-mono text-2xs text-ink-muted">{String(worker.id)}</span><StatusPill status={String(worker.status)} /></div>)}{!activity.data?.workers?.length ? <EmptyState title="No worker heartbeat" description="Run npm run worker on a persistent host." /> : null}</div></CardContent></Card>
          <Card><CardHeader><CardTitle>Monitoring dimensions</CardTitle></CardHeader><CardContent><div className="grid grid-cols-2 gap-2 text-2xs text-ink-muted">{["duration", "token usage", "model / provider", "tool calls", "intermediate outputs", "errors / retries", "approvals", "final results"].map((item) => <div key={item} className="rounded border border-line bg-graphite-950/50 px-2 py-1.5">{item}</div>)}</div></CardContent></Card>
        </div>
      </div>
      <ApiErrorNotice error={activity.error} />
    </div>
  );
}
