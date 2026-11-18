"use client";

import * as React from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { EmptyState, InlineNotice } from "@/components/ui";

export function ApiErrorNotice({ error }: { error: string | null }) {
  if (!error) return null;
  return <InlineNotice tone={error.includes("DATABASE_URL") || error.includes("Supabase Auth") ? "amber" : "rose"} title="Backend unavailable">{error}</InlineNotice>;
}

export function LoadingCard({ label = "Loading persistent records…" }: { label?: string }) {
  return <div className="flex min-h-32 items-center justify-center gap-2 rounded-lg border border-line bg-graphite-950/40 text-xs text-ink-faint"><Loader2 className="h-3.5 w-3.5 animate-spin" />{label}</div>;
}

export function EmptyOrError({ loading, error, empty, children, title = "No records", description = "Create a record to begin." }: { loading: boolean; error: string | null; empty: boolean; children: React.ReactNode; title?: string; description?: string }) {
  if (loading) return <LoadingCard />;
  if (error) return <ApiErrorNotice error={error} />;
  if (empty) return <EmptyState title={title} description={description} />;
  return <>{children}</>;
}

export function KeyValue({ data }: { data: Record<string, unknown> }) {
  return <div className="grid gap-1.5 sm:grid-cols-2">{Object.entries(data).map(([key, value]) => <div key={key} className="rounded border border-line bg-graphite-950/50 px-2 py-1.5"><div className="text-2xs uppercase tracking-wider text-ink-faint">{key.replaceAll("_", " ")}</div><div className="truncate font-mono text-2xs text-ink-muted">{typeof value === "object" ? JSON.stringify(value) : String(value ?? "—")}</div></div>)}</div>;
}

export function WarningLine({ children }: { children: React.ReactNode }) {
  return <div className="flex items-start gap-1.5 text-2xs leading-relaxed text-ink-faint"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-signal-amber" />{children}</div>;
}
