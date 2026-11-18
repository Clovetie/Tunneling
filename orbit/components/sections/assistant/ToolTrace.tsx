"use client";

import React, { useState } from "react";
import {
  Check,
  ChevronRight,
  CircleAlert,
  Clock3,
  Loader2,
  ShieldCheck,
  ShieldQuestion,
  Terminal,
  X,
} from "lucide-react";
import { cn, relative } from "@/lib/util";
import type { ToolCall } from "@/lib/types";
import { Button } from "@/components/ui/kit";

const STATUS = {
  pending: { icon: Clock3, tone: "text-warn", label: "awaiting approval" },
  approved: { icon: ShieldCheck, tone: "text-signal", label: "approved" },
  denied: { icon: X, tone: "text-danger", label: "denied" },
  success: { icon: Check, tone: "text-signal", label: "verified" },
  error: { icon: CircleAlert, tone: "text-danger", label: "failed" },
} as const;

export function ToolTrace({
  calls,
  onApprove,
  onDeny,
}: {
  calls: ToolCall[];
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(
    calls.length === 1 ? calls[0].id : null,
  );

  return (
    <div className="mb-2 space-y-1">
      {calls.map((call) => {
        const s = STATUS[call.status] ?? STATUS.pending;
        const Icon = s.icon;
        const isOpen = expanded === call.id;
        return (
          <div
            key={call.id}
            className={cn(
              "overflow-hidden rounded-[7px] border transition-colors",
              call.status === "pending"
                ? "border-warn/35 bg-warn/[0.05]"
                : call.status === "denied" || call.status === "error"
                  ? "border-danger/25 bg-danger/[0.04]"
                  : "border-line bg-abyss/60",
            )}
          >
            <button
              onClick={() => setExpanded(isOpen ? null : call.id)}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
            >
              <ChevronRight
                className={cn("h-2.5 w-2.5 shrink-0 text-faint transition-transform", isOpen && "rotate-90")}
              />
              {call.status === "pending" ? (
                <Loader2 className="h-3 w-3 shrink-0 animate-spin text-warn" />
              ) : (
                <Icon className={cn("h-3 w-3 shrink-0", s.tone)} />
              )}
              <span className="shrink-0 font-mono text-[10.5px] text-muted">{call.tool}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-faint">{call.label}</span>
              {call.requiresApproval && (
                <span className="shrink-0 rounded border border-warn/30 px-1 text-[9px] text-warn uppercase">
                  write
                </span>
              )}
              <span className={cn("shrink-0 text-[9.5px] uppercase", s.tone)}>{s.label}</span>
              {call.durationMs !== undefined && (
                <span className="num shrink-0 text-[9.5px] text-faint">{call.durationMs}ms</span>
              )}
            </button>

            {isOpen && (
              <div className="border-t border-line/60 px-2.5 py-2">
                <div className="mb-1.5 flex items-center gap-1.5">
                  <Terminal className="h-2.5 w-2.5 text-faint" />
                  <span className="text-[9.5px] tracking-wide text-faint uppercase">Input</span>
                </div>
                <pre className="overflow-x-auto rounded border border-line bg-void px-2 py-1.5 font-mono text-[10px] leading-relaxed text-muted">
                  {JSON.stringify(call.args, null, 2)}
                </pre>
                {call.result && (
                  <>
                    <div className="mt-2 mb-1 flex items-center gap-1.5">
                      <ShieldCheck className="h-2.5 w-2.5 text-faint" />
                      <span className="text-[9.5px] tracking-wide text-faint uppercase">Result</span>
                    </div>
                    <div
                      className={cn(
                        "rounded border px-2 py-1.5 font-mono text-[10px] leading-relaxed",
                        call.status === "success"
                          ? "border-signal/25 bg-signal/[0.05] text-signal"
                          : call.status === "error" || call.status === "denied"
                            ? "border-danger/25 bg-danger/[0.05] text-danger"
                            : "border-line bg-void text-muted",
                      )}
                    >
                      {call.result}
                    </div>
                  </>
                )}
                {call.auditAt && (
                  <div className="mt-1.5 flex items-center gap-1 text-[9.5px] text-faint">
                    <ShieldCheck className="h-2.5 w-2.5" />
                    audited {relative(call.auditAt)} · permission-gated · result validated
                  </div>
                )}
                {call.status === "pending" && (
                  <div className="mt-2 flex items-center gap-2">
                    <ShieldQuestion className="h-3 w-3 text-warn" />
                    <span className="text-[10.5px] text-warn">
                      This write is parked. Approve to apply it.
                    </span>
                    <div className="ml-auto flex gap-1.5">
                      <Button size="sm" variant="primary" onClick={() => onApprove(call.id)}>
                        Approve
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => onDeny(call.id)}>
                        Deny
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
