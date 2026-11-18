"use client";

import React, { useMemo, useState } from "react";
import {
  Activity,
  Bot,
  Check,
  CircleAlert,
  Clock3,
  Download,
  Filter,
  Search,
  ShieldCheck,
  Terminal,
  Workflow,
  Plug,
  UserCheck,
} from "lucide-react";
import { useOrbit } from "@/lib/store";
import { cn, fmtDate, fmtTime, groupBy, relative } from "@/lib/util";
import { Badge } from "@/components/ui/kit";
import type { ActivityEntry } from "@/lib/types";

const KIND_META: Record<ActivityEntry["kind"], { icon: React.ElementType; label: string }> = {
  tool: { icon: Terminal, label: "Tool" },
  agent: { icon: Bot, label: "Agent" },
  automation: { icon: Workflow, label: "Automation" },
  system: { icon: Clock3, label: "System" },
  approval: { icon: UserCheck, label: "Approval" },
  integration: { icon: Plug, label: "Integration" },
};

export function ActivitySection() {
  const { state } = useOrbit();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<string>("all");

  const entries = useMemo(
    () =>
      state.activity
        .filter((a) => (kind === "all" ? true : a.kind === kind))
        .filter((a) =>
          query.trim()
            ? `${a.title} ${a.detail} ${a.actor}`.toLowerCase().includes(query.toLowerCase())
            : true,
        ),
    [state.activity, kind, query],
  );

  const grouped = useMemo(() => groupBy(entries, (a) => fmtDate(a.at)), [entries]);

  const stats = useMemo(() => {
    const ok = state.activity.filter((a) => a.status === "ok").length;
    return {
      total: state.activity.length,
      ok,
      errors: state.activity.filter((a) => a.status === "error").length,
      tools: state.activity.filter((a) => a.kind === "tool").length,
    };
  }, [state.activity]);

  function exportJson() {
    const blob = new Blob([JSON.stringify(state.activity, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `orbit-activity-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="border-b border-line px-6 py-4 md:px-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="mb-1 flex items-center gap-1.5">
              <Activity className="h-3.5 w-3.5 text-signal" />
              <span className="text-[10.5px] font-medium tracking-[0.14em] text-faint uppercase">
                Activity
              </span>
            </div>
            <h1 className="text-[19px] font-semibold tracking-[-0.02em] text-ink">
              Everything ORBIT did
            </h1>
            <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-muted">
              A complete, ordered record of tool calls, agent handoffs, approvals and failures.
              Nothing runs without an entry appearing here.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-3 rounded-[8px] border border-line bg-surface px-3 py-2 text-[10px]">
              <span className="text-muted">{stats.total} events</span>
              <span className="h-2.5 w-px bg-line" />
              <span className="text-signal">{stats.ok} ok</span>
              <span className="h-2.5 w-px bg-line" />
              <span className="text-danger">{stats.errors} error</span>
              <span className="h-2.5 w-px bg-line" />
              <span className="text-faint">{stats.tools} tools</span>
            </div>
            <button
              onClick={exportJson}
              className="flex h-8 items-center gap-1.5 rounded-[7px] border border-line bg-raised px-2.5 text-[11.5px] text-muted transition hover:border-line-bright hover:text-ink"
            >
              <Download className="h-3 w-3" /> Export
            </button>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute top-1/2 left-2 h-3 w-3 -translate-y-1/2 text-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter log"
              className="w-[180px] rounded-[6px] border border-line bg-surface py-1 pr-2 pl-6.5 text-[11.5px] text-ink placeholder:text-faint focus:border-accent/40 focus:outline-none"
            />
          </div>
          <div className="flex flex-wrap gap-1">
            <button
              onClick={() => setKind("all")}
              className={cn(
                "rounded-full px-2 py-0.5 text-[10.5px] transition",
                kind === "all" ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
              )}
            >
              All
            </button>
            {(Object.keys(KIND_META) as ActivityEntry["kind"][]).map((k) => (
              <button
                key={k}
                onClick={() => setKind(k)}
                className={cn(
                  "rounded-full px-2 py-0.5 text-[10.5px] transition",
                  kind === k ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
                )}
              >
                {KIND_META[k].label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-4xl px-6 py-4 md:px-8">
        {Object.entries(grouped).map(([day, items]) => (
          <div key={day} className="mb-5">
            <div className="sticky top-0 z-10 mb-2 flex items-center gap-2 bg-void/90 py-1 backdrop-blur">
              <span className="text-[10.5px] font-medium tracking-[0.12em] text-faint uppercase">
                {day}
              </span>
              <span className="h-px flex-1 bg-line" />
              <span className="num text-[10px] text-faint">{items.length}</span>
            </div>
            <div className="relative">
              <div className="absolute top-2 bottom-2 left-[13px] w-px bg-line" />
              {items.map((a) => {
                const meta = KIND_META[a.kind];
                const Icon = meta.icon;
                return (
                  <div key={a.id} className="relative flex gap-2.5 pb-2.5">
                    <div
                      className={cn(
                        "relative z-10 mt-0.5 flex h-[27px] w-[27px] shrink-0 items-center justify-center rounded-full border bg-abyss",
                        a.status === "ok"
                          ? "border-signal/30 text-signal"
                          : a.status === "error"
                            ? "border-danger/30 text-danger"
                            : a.status === "warn"
                              ? "border-warn/30 text-warn"
                              : "border-line text-faint",
                      )}
                    >
                      <Icon className="h-3 w-3" />
                    </div>
                    <div className="min-w-0 flex-1 rounded-[7px] border border-line bg-surface/50 px-3 py-2 transition-colors hover:bg-raised/50">
                      <div className="flex flex-wrap items-baseline gap-2">
                        <span className="font-mono text-[11.5px] text-ink">{a.title}</span>
                        <Badge tone="neutral">{a.actor}</Badge>
                        <span className="num ml-auto text-[9.5px] text-faint">
                          {fmtTime(a.at)}
                        </span>
                      </div>
                      <p className="mt-0.5 text-[11px] leading-relaxed text-faint">{a.detail}</p>
                      {a.meta && (
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          {Object.entries(a.meta).map(([k, v]) => (
                            <span
                              key={k}
                              className="rounded border border-line bg-abyss px-1 py-[1px] font-mono text-[9px] text-faint"
                            >
                              {k}={v}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        {!entries.length && (
          <div className="px-4 py-16 text-center text-[12px] text-faint">
            No activity matches this filter.
          </div>
        )}

        <div className="mt-4 flex items-start gap-2 rounded-[8px] border border-line bg-abyss/50 px-3 py-2">
          <ShieldCheck className="mt-[1px] h-3 w-3 shrink-0 text-signal" />
          <p className="text-[10.5px] leading-relaxed text-faint">
            Entries are append-only in this session and exported as JSON. In production the same
            record is hash-chained, so tampering after the fact is detectable.
          </p>
        </div>
      </div>
    </div>
  );
}
