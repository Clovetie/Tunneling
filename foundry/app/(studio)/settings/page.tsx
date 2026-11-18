"use client";

import * as React from "react";
import { Boxes, CheckCircle2, Database, KeyRound, LockKeyhole, RefreshCw, Server, ShieldCheck, Sparkles } from "lucide-react";
import { useApiResource, useFoundry } from "@/components/auth-gate";
import { ReadinessStrip } from "@/components/config-banner";
import { ApiErrorNotice } from "@/components/page-helpers";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, InlineNotice, JsonBlock, SectionHeader, StatusPill } from "@/components/ui";

export default function SettingsPage() {
  const { apiFetch, config } = useFoundry();
  const health = useApiResource<Record<string, unknown>>("/api/health");
  const appConfig = useApiResource<Record<string, unknown>>("/api/foundry/config");
  const audit = useApiResource<{ valid: boolean; count: number; firstMismatchId?: number }>("/api/foundry/audit/verify");
  const workers = useApiResource<Array<Record<string, unknown>>>("/api/foundry/workers");
  const [message, setMessage] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const refresh = () => { health.refresh(); appConfig.refresh(); audit.refresh(); workers.refresh(); };
  const bootstrap = async () => { setBusy(true); try { await apiFetch("/api/foundry/bootstrap", { method: "POST", body: "{}" }); setMessage("Templates, default project and general agents bootstrapped."); refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); } finally { setBusy(false); } };

  return (
    <div className="space-y-4">
      <SectionHeader eyebrow="Deployment" title="Settings" description="Configuration posture, server-side secret boundary, persistent execution split and deployment responsibilities. Values are booleans and labels only; no credentials are exposed." actions={<><ReadinessStrip /><Button variant="secondary" onClick={refresh}><RefreshCw className="h-3.5 w-3.5" /></Button></>} />
      {message ? <InlineNotice tone={message.includes("not configured") ? "amber" : "cyan"}>{message}</InlineNotice> : null}
      <div className="grid gap-4 xl:grid-cols-2">
        <Card><CardHeader><CardTitle>Environment readiness</CardTitle><Badge tone={config.mode === "ready" ? "green" : config.mode === "partial" ? "amber" : "rose"}>{config.mode}</Badge></CardHeader><CardContent className="space-y-3"><div className="grid gap-2 sm:grid-cols-2"><Setting label="Supabase Auth" ok={config.authConfigured} icon={<LockKeyhole className="h-3.5 w-3.5" />} /><Setting label="Postgres" ok={config.databaseConfigured} icon={<Database className="h-3.5 w-3.5" />} /><Setting label="Realtime" ok={config.realtimeConfigured} icon={<Boxes className="h-3.5 w-3.5" />} /><Setting label="Secret encryption" ok={config.encryptionConfigured} icon={<KeyRound className="h-3.5 w-3.5" />} /><Setting label="Model providers" ok={config.providers.length > 0} icon={<Sparkles className="h-3.5 w-3.5" />} /><Setting label="Worker heartbeat" ok={Boolean(health.data?.worker)} icon={<Server className="h-3.5 w-3.5" />} /></div><JsonBlock value={health.data ?? {}} className="max-h-72" /></CardContent></Card>
        <Card><CardHeader><CardTitle>Runtime ownership</CardTitle><ShieldCheck className="h-4 w-4 text-signal-green" /></CardHeader><CardContent className="space-y-2 text-xs leading-relaxed text-ink-muted"><Runtime title="Vercel / Next.js" detail="Auth, API routes, validation, small database writes, durable job enqueue and proxying short read-only bridge status." /><Runtime title="Persistent worker" detail="Model streaming, tool calls, task dependencies, retries, lease recovery, approvals and supervisor review. Never a permanent worker inside a serverless request." /><Runtime title="Local connector / Studio" detail="stdio MCP or local Streamable HTTP to the existing Tunneling bridge. Cloudflare Tunnel or ngrok is managed by Tunneling, not duplicated." /><Runtime title="Supabase / Postgres" detail="Auth, RLS, pgvector, Realtime publication, durable records and scoped memories." /><div className="rounded border border-line bg-graphite-950/50 p-2.5 text-2xs text-ink-faint">See foundry/docs/DEPLOYMENT.md for environment variables and the live acceptance checklist.</div></CardContent></Card>
        <Card><CardHeader><CardTitle>Security & audit</CardTitle><StatusPill status={audit.data?.valid ? "ok" : "failed"} /></CardHeader><CardContent className="space-y-3"><div className="grid grid-cols-2 gap-2"><div className="rounded border border-line bg-graphite-950/50 p-2"><div className="text-2xs text-ink-faint">Audit entries</div><div className="mt-1 font-mono text-lg text-ink">{audit.data?.count ?? 0}</div></div><div className="rounded border border-line bg-graphite-950/50 p-2"><div className="text-2xs text-ink-faint">Chain integrity</div><div className="mt-1 font-mono text-sm text-signal-green">{audit.data?.valid ? "verified" : "unavailable"}</div></div></div><div className="space-y-1.5 text-2xs leading-relaxed text-ink-muted">{["RLS on owner-scoped tables", "AES-256-GCM encrypted integration secrets", "per-agent tool permissions and role ceilings", "explicit approval for destructive, publish, external and credential actions", "allowlisted HTTP origins and stdio commands", "hash-chained mutation audit"].map((item) => <div key={item} className="flex items-center gap-2 rounded border border-line bg-graphite-950/50 px-2 py-1.5"><CheckCircle2 className="h-3 w-3 text-signal-green" />{item}</div>)}</div></CardContent></Card>
        <Card><CardHeader><CardTitle>Bootstrap & workers</CardTitle></CardHeader><CardContent className="space-y-3"><p className="text-xs leading-relaxed text-ink-muted">Bootstrap installs global role presets, four general agents, the Personal Workspace project and a validated workflow template. It requires Auth, Postgres and the migration.</p><Button disabled={!config.databaseConfigured || busy} onClick={bootstrap}><Sparkles className="h-3.5 w-3.5" />Bootstrap personal workspace</Button><div><div className="mb-2 text-2xs uppercase tracking-wider text-ink-faint">Worker registry</div><JsonBlock value={workers.data ?? []} className="max-h-48" /></div><div><div className="mb-2 text-2xs uppercase tracking-wider text-ink-faint">Non-secret app config</div><JsonBlock value={appConfig.data ?? {}} className="max-h-48" /></div></CardContent></Card>
      </div>
      <ApiErrorNotice error={audit.error || workers.error} />
    </div>
  );
}

function Setting({ label, ok, icon }: { label: string; ok: boolean; icon: React.ReactNode }) {
  return <div className="flex items-center justify-between gap-2 rounded border border-line bg-graphite-950/50 px-2 py-2"><div className="flex items-center gap-2 text-2xs text-ink-muted">{icon}{label}</div><StatusPill status={ok ? "connected" : "disconnected"} /></div>;
}
function Runtime({ title, detail }: { title: string; detail: string }) {
  return <div className="rounded border border-line bg-graphite-950/50 p-2.5"><div className="text-2xs font-semibold text-ink">{title}</div><p className="mt-1 text-2xs leading-relaxed text-ink-faint">{detail}</p></div>;
}
