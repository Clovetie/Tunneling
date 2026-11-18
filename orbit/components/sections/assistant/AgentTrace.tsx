"use client";

import React from "react";
import { ArrowRight, Check, CircleAlert, Loader2, Bot } from "lucide-react";
import { cn } from "@/lib/util";
import type { AgentStep } from "@/lib/types";

const AGENT_COLOR: Record<string, string> = {
  supervisor: "#7c9cff",
  research: "#56cfd8",
  planning: "#a78bfa",
  productivity: "#4ade9b",
  knowledge: "#f5b556",
  development: "#7c9cff",
  data: "#56cfd8",
  automation: "#f2626b",
};

export function AgentTrace({ steps }: { steps: AgentStep[] }) {
  return (
    <div className="mt-1.5 mb-1 space-y-0">
      {steps.map((s, i) => {
        const color = AGENT_COLOR[s.agent] ?? "#8b96a5";
        const last = i === steps.length - 1;
        return (
          <div key={s.id} className="relative flex gap-2 pb-2">
            {!last && (
              <span className="absolute top-4 left-[5.5px] h-[calc(100%-12px)] w-px bg-line" />
            )}
            <span
              className={cn(
                "relative z-10 mt-[2px] flex h-3 w-3 shrink-0 items-center justify-center rounded-full ring-2 ring-surface",
                s.status === "done"
                  ? "bg-signal"
                  : s.status === "failed"
                    ? "bg-danger"
                    : s.status === "working"
                      ? "bg-accent"
                      : "bg-line-bright",
              )}
            >
              {s.status === "working" ? (
                <Loader2 className="h-2 w-2 animate-spin text-void" />
              ) : s.status === "failed" ? (
                <CircleAlert className="h-2 w-2 text-void" />
              ) : s.status === "done" ? (
                <Check className="h-2 w-2 text-void" />
              ) : null}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span
                  className="rounded px-1 py-[0.5px] text-[9px] font-medium tracking-wide uppercase"
                  style={{ background: `${color}1a`, color }}
                >
                  {s.agent}
                </span>
                <span className="text-[11px] text-ink">{s.action}</span>
                {s.handoffTo && (
                  <span className="flex items-center gap-0.5 text-[9.5px] text-faint">
                    <ArrowRight className="h-2 w-2" /> {s.handoffTo}
                  </span>
                )}
              </div>
              <div className="text-[10px] leading-relaxed text-faint">{s.detail}</div>
            </div>
          </div>
        );
      })}
      <div className="flex items-center gap-1.5 pl-[1px] text-[9.5px] text-faint">
        <Bot className="h-2.5 w-2.5" />
        Supervisor reviewed results before reporting
      </div>
    </div>
  );
}
