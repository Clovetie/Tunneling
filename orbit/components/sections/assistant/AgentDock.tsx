"use client";

import React, { useState } from "react";
import { Bot, ChevronRight, CircleDot, Network, Play, Shield, X } from "lucide-react";
import { useOrbit } from "@/lib/store";
import { agents } from "@/lib/seed";
import { cn, relative } from "@/lib/util";
import { Badge, Button, HoverTip } from "@/components/ui/kit";
import { AgentTrace } from "./AgentTrace";

export function AgentDock() {
  const { state, setSection } = useOrbit();
  const [open, setOpen] = useState(false);
  const active = state.runtimes.filter((r) => r.status !== "complete" || true).slice(0, 6);

  return (
    <>
      <div className="hidden w-[212px] shrink-0 flex-col border-l border-line bg-abyss/40 xl:flex">
        <div className="flex h-[42px] items-center justify-between border-b border-line px-3">
          <span className="text-[11px] font-medium tracking-[0.1em] text-muted uppercase">
            Agents
          </span>
          <HoverTip content="Delegation rules">
            <button
              onClick={() => setOpen(true)}
              className="rounded p-1 text-faint transition hover:bg-raised hover:text-ink"
            >
              <Shield className="h-3 w-3" />
            </button>
          </HoverTip>
        </div>

        <div className="border-b border-line px-2.5 py-2">
          <div className="flex items-center gap-1.5 rounded-[6px] border border-accent/25 bg-accent/[0.06] px-2 py-1.5">
            <Network className="h-3 w-3 shrink-0 text-accent" />
            <div className="min-w-0">
              <div className="text-[10.5px] font-medium text-ink">Supervisor</div>
              <div className="truncate text-[9px] text-faint">
                {state.runtimes.filter((r) => r.status === "running").length} running · delegates on
                merit
              </div>
            </div>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          <div className="px-1.5 pb-1 text-[9.5px] font-medium tracking-[0.1em] text-faint uppercase">
            Specialists
          </div>
          {agents
            .filter((a) => a.id !== "supervisor")
            .map((a) => {
              const busy = state.runtimes.some(
                (r) =>
                  r.steps.some((s) => s.agent === a.id && s.status === "working") &&
                  r.status === "running",
              );
              return (
                <button
                  key={a.id}
                  onClick={() => setSection("settings")}
                  className="mb-0.5 flex w-full items-center gap-1.5 rounded-[5px] px-1.5 py-1 text-left transition hover:bg-raised/60"
                >
                  <span
                    className={cn("h-1.5 w-1.5 shrink-0 rounded-full", busy && "animate-pulse")}
                    style={{ background: busy ? a.accent : "#2b333e" }}
                  />
                  <span className="min-w-0 flex-1 truncate text-[11px] text-muted">{a.name}</span>
                  <span className="shrink-0 text-[9px] text-faint">{a.tools.length}</span>
                </button>
              );
            })}

          <div className="mt-3 px-1.5 pb-1 text-[9.5px] font-medium tracking-[0.1em] text-faint uppercase">
            Runs
          </div>
          {active.length ? (
            active.map((r) => (
              <div
                key={r.id}
                className="mb-1 rounded-[6px] border border-line bg-surface/60 px-2 py-1.5"
              >
                <div className="flex items-center gap-1.5">
                  <CircleDot
                    className={cn(
                      "h-2.5 w-2.5 shrink-0",
                      r.status === "running"
                        ? "text-accent"
                        : r.status === "review"
                          ? "text-warn"
                          : "text-signal",
                    )}
                  />
                  <span className="min-w-0 flex-1 truncate text-[10.5px] text-ink">
                    {r.objective}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-1.5 pl-4">
                  <Badge tone="neutral">{r.mode}</Badge>
                  <span className="text-[9px] text-faint">{relative(r.startedAt)}</span>
                </div>
              </div>
            ))
          ) : (
            <p className="px-1.5 py-2 text-[10px] leading-relaxed text-faint">
              No delegation yet. Send a multi-step objective in Agent mode.
            </p>
          )}
        </div>

        <div className="border-t border-line p-2">
          <Button
            size="sm"
            variant="secondary"
            className="w-full justify-center"
            onClick={() => {
              setSection("assistant");
              setTimeout(
                () =>
                  window.dispatchEvent(
                    new CustomEvent("orbit:prompt", {
                      detail: { prompt: "Organize everything I need to work on this week" },
                    }),
                  ),
                80,
              );
            }}
          >
            <Play className="h-3 w-3" /> Delegate an objective
          </Button>
        </div>
      </div>

      {open && (
        <div className="fixed inset-0 z-[88] flex items-center justify-center p-4">
          <div className="anim-fade absolute inset-0 bg-void/75 backdrop-blur-[2px]" onClick={() => setOpen(false)} />
          <div className="anim-fade-up relative w-full max-w-2xl overflow-hidden rounded-xl border border-line-bright bg-surface shadow-2xl">
            <div className="flex items-start justify-between border-b border-line px-4 py-3">
              <div>
                <h2 className="text-[14px] font-semibold text-ink">Delegation rules</h2>
                <p className="mt-0.5 text-[11.5px] text-muted">
                  The Supervisor delegates only when a specialist's tools or context genuinely
                  improve the result.
                </p>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="rounded p-1 text-faint hover:bg-raised hover:text-ink"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="max-h-[60vh] overflow-y-auto p-4">
              <div className="mb-3 rounded-[7px] border border-line bg-abyss/50 p-3">
                <div className="mb-1.5 text-[10px] font-medium tracking-[0.1em] text-faint uppercase">
                  Delegation test
                </div>
                <ol className="space-y-1">
                  {[
                    "Does a specialist hold a tool the Supervisor does not?",
                    "Would two agents duplicate the same work? If yes, do not split.",
                    "Is the objective bounded enough to review? If not, narrow it first.",
                    "Can the result be verified by a tool, not by prose? If not, do not claim it.",
                  ].map((q, i) => (
                    <li key={q} className="flex items-start gap-2 text-[11px] text-muted">
                      <span className="num mt-[1px] shrink-0 text-faint">{i + 1}.</span>
                      {q}
                    </li>
                  ))}
                </ol>
              </div>

              {state.runtimes.slice(0, 3).map((r) => (
                <div key={r.id} className="mb-2 rounded-[7px] border border-line p-3">
                  <div className="flex items-center gap-2">
                    <Bot className="h-3 w-3 text-accent" />
                    <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink">
                      {r.objective}
                    </span>
                    <Badge tone={r.status === "complete" ? "success" : "accent"}>{r.status}</Badge>
                  </div>
                  <p className="mt-1 text-[10.5px] text-faint">{r.supervisorNote}</p>
                  <div className="mt-1.5">
                    <AgentTrace steps={r.steps} />
                  </div>
                </div>
              ))}
              {!state.runtimes.length && (
                <p className="py-6 text-center text-[11.5px] text-faint">
                  No runs recorded yet. Agent mode produces one.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
