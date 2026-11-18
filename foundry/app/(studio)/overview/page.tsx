"use client";

import * as React from "react";
import Link from "next/link";
import { Activity, Bot, Boxes, CheckCircle2, Clock3, Cpu, Database, GitBranch, Layers3, Play, ShieldCheck } from "lucide-react";
import { useApiResource } from "@/components/auth-gate";
import { useRealtimeRefresh } from "@/components/realtime";
import { ReadinessStrip } from "@/components/config-banner";
import { ApiErrorNotice, EmptyOrError } from "@/components/page-helpers";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, JsonBlock, MetricCard, SectionHeader, StatusPill } from "@/components/ui";
import { compactNumber, formatRelative, shortId } from "@/lib/utils";

type Dashboard = {
  config?: { mode: string };
  database?: { ok: boolean; latencyMs: number; error?: string };
  metrics?: Record<string, unknown>;
  activeRuns?: Array<Record<string, unknown>>;
  agents?: Array<Record<string, unknown>>;
  recentEvents?: Array<Record<string, unknown>>;
  workers?: Array<Record<string, unknown>>;
};

export default function OverviewPage() {
  const dashboard = useApiResource<Dashboard>("/api/foundry/dashboard");
  const health = useApiResource<Record<string, unknown>>("/api/health");
  const refresh = () => { dashboard.refresh(); health.refresh(); };
  const realtime = useRealtimeRefresh(refresh);
  const metrics = dashboard.data?.metrics ?? {};
  const jobs = (metrics.jobs as Record<string, unknown> | undefined) ?? {};
  return (
    <div className="space-y-4">
      <SectionHeader eyebrow="Control center" title="Overview" description="Live posture of the personal agent workspace, durable execution queue and connected Roblox bridge. Unconfigured surfaces are reported honestly." actions={<><ReadinessStrip /><Button variant="secondary" onClick={refresh}><Activity className="h-3.5 w-3.5" />Refresh</Button></>} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Registered agents" value={compactNumber(Number(metrics.agents ?? 0))} hint="CRUD, presets and scoped memories" icon={<Bot className="h-4 w-4" />} />
        <MetricCard label="Active runs" value={compactNumber(Number(metrics.activeRuns ?? 0))} hint={`${compactNumber(Number(metrics.runs ?? 0))} total durable runs`} tone="violet" icon={<Layers3 className="h-4 w-4" />} />
        <MetricCard label="Queued jobs" value={compactNumber(Number(jobs.queued ?? 0))} hint={`${compactNumber(Number(jobs.running ?? 0))} running · ${compactNumber(Number(jobs.failed ?? 0))} failed`} tone="amber" icon={<Clock3 className="h-4 w-4" />} />
        <MetricCard label="Pending approvals" value={compactNumber(Number(metrics.pendingApprovals ?? 0))} hint="Human gates and ConfirmGate" tone="rose" icon={<ShieldCheck className="h-4 w-4" />} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(300px,0.8fr)]">
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Active workflow runs</CardTitle><div className="flex gap-2"><Badge tone="cyan">durable tasks</Badge><Badge tone={realtime === "subscribed" ? "green" : "amber"}>realtime {realtime}</Badge></div></CardHeader><CardContent>
            <EmptyOrError loading={dashboard.loading} error={dashboard.error} empty={!dashboard.data?.activeRuns?.length} title="No active runs" description="Create a workflow or launch an objective in Workflows.">
              <div className="space-y-2">{(dashboard.data?.activeRuns ?? []).map((run) => <Link key={String(run.id)} href={`/workflows?run=${String(run.id)}`} className="flex items-center justify-between gap-3 rounded-lg border border-line bg-graphite-950/50 p-2.5 hover:border-signal-cyan/30"><div className="min-w-0"><div className="truncate text-xs font-medium text-ink">{String(run.objective ?? `Graph run ${shortId(String(run.id))}`)}</div><div className="mt-1 font-mono text-2xs text-ink-faint">{String(run.mode)} · {formatRelative(run.created_at as string | undefined)}</div></div><StatusPill status={String(run.status)} /></Link>)}</div>
            </EmptyOrError>
          </CardContent></Card>
          <Card><CardHeader><CardTitle>Execution event stream</CardTitle><Badge tone="neutral">Supabase Realtime</Badge></CardHeader><CardContent><EmptyOrError loading={dashboard.loading} error={dashboard.error} empty={!dashboard.data?.recentEvents?.length} title="No events yet" description="Events appear when jobs, tasks, tools, approvals and model calls are recorded."><div className="max-h-72 space-y-1 overflow-auto pr-1">{(dashboard.data?.recentEvents ?? []).map((event) => <div key={String(event.id)} className="flex items-center justify-between gap-3 rounded border border-line bg-graphite-950/50 px-2 py-1.5"><div className="min-w-0"><span className="font-mono text-2xs text-signal-cyan/80">{String(event.kind)}</span><span className="ml-2 truncate text-2xs text-ink-faint">{JSON.stringify(event.data ?? {}).slice(0, 140)}</span></div><span className="shrink-0 font-mono text-2xs text-ink-faint">{formatRelative(event.created_at as string | undefined)}</span></div>)}</div></EmptyOrError></CardContent></Card>
        </div>
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>Runtime split</CardTitle><Badge tone="violet">Vercel + worker</Badge></CardHeader><CardContent className="space-y-3">
            <RuntimeRow icon={<Cpu className="h-3.5 w-3.5" />} label="Next.js / Vercel" value={health.data?.app === "FOUNDRY" ? "online" : "unknown"} />
            <RuntimeRow icon={<Database className="h-3.5 w-3.5" />} label="Postgres / Supabase" value={health.data?.database && (health.data.database as Record<string, unknown>).ok ? "connected" : "unconfigured"} />
            <RuntimeRow icon={<Play className="h-3.5 w-3.5" />} label="Persistent worker" value={health.data?.worker ? "heartbeat" : "no heartbeat"} />
            <RuntimeRow icon={<CheckCircle2 className="h-3.5 w-3.5" />} label="Migration" value={health.data?.migration ? "applied" : "missing"} />
            <div className="rounded-lg border border-line bg-graphite-950/50 p-2.5 text-2xs leading-relaxed text-ink-faint">Long-running agent execution is never kept inside a serverless request. Vercel enqueues durable jobs; an external worker executes them.</div>
          </CardContent></Card>
          <Card><CardHeader><CardTitle>Quick actions</CardTitle></CardHeader><CardContent className="grid gap-2"><Link href="/builder"><Button variant="secondary" className="w-full justify-start"><GitBranch className="h-3.5 w-3.5" />Open visual builder</Button></Link><Link href="/roblox"><Button variant="secondary" className="w-full justify-start"><Boxes className="h-3.5 w-3.5" />Inspect Roblox bridge</Button></Link><Link href="/activity"><Button variant="secondary" className="w-full justify-start"><Activity className="h-3.5 w-3.5" />Review approvals & audit</Button></Link></CardContent></Card>
          <Card><CardHeader><CardTitle>Worker heartbeats</CardTitle></CardHeader><CardContent>{dashboard.data?.workers?.length ? <JsonBlock value={dashboard.data.workers} className="max-h-44" /> : <EmptyState title="No worker heartbeat" description="Run npm run worker on a persistent host connected to the same DATABASE_URL." />}</CardContent></Card>
        </div>
      </div>
      <ApiErrorNotice error={health.error} />
    </div>
  );
}

function RuntimeRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="flex items-center justify-between gap-3 rounded border border-line bg-graphite-950/50 px-2 py-1.5"><div className="flex items-center gap-2 text-2xs text-ink-muted">{icon}{label}</div><StatusPill status={value === "connected" || value === "online" || value === "heartbeat" || value === "applied" ? "connected" : "disconnected"} /></div>;
}
