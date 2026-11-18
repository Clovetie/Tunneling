"use client";

import React, { useMemo } from "react";
import {
  Activity,
  ArrowUpRight,
  Bot,
  ChevronRight,
  CircleDot,
  Clock3,
  GripVertical,
  Lightbulb,
  ListChecks,
  PanelRightClose,
  Radio,
  Workflow,
} from "lucide-react";
import { useOrbit } from "@/lib/store";
import { cn, minutesUntil, relative, sortByPriority } from "@/lib/util";
import { buildBriefing } from "@/lib/briefing";
import { Badge, Progress, StatusPill } from "@/components/ui/kit";

export function RightPanel() {
  const { state, dispatch, setSection, select } = useOrbit();
  const open = state.preferences.rightPanelOpen;
  const width = state.preferences.rightPanelWidth;

  const briefing = useMemo(() => buildBriefing(state), [state]);
  const focusTasks = useMemo(
    () =>
      sortByPriority(state.tasks.filter((t) => t.status === "doing" || t.status === "blocked")).slice(
        0,
        4,
      ),
    [state.tasks],
  );
  const activeProjects = state.projects.filter((p) => p.status === "active").slice(0, 3);
  const runningAutomations = state.automations.filter((a) => a.enabled).slice(0, 3);
  const recentActivity = state.activity.slice(0, 4);
  const liveRuntime = state.runtimes.find((r) => r.status === "running" || r.status === "review");

  if (!open) {
    return (
      <button
        onClick={() => dispatch({ type: "prefs", patch: { rightPanelOpen: true } })}
        className="relative z-20 flex w-9 shrink-0 items-center justify-center border-l border-line bg-abyss text-faint transition hover:text-ink"
        aria-label="Open intelligence panel"
      >
        <div className="flex flex-col items-center gap-2">
          <Bot className="h-4 w-4" />
          <span className="text-[10px] tracking-[0.16em] uppercase [writing-mode:vertical-rl]">
            Intelligence
          </span>
        </div>
      </button>
    );
  }

  return (
    <aside
      className="relative z-20 hidden h-full shrink-0 flex-col border-l border-line bg-abyss lg:flex"
      style={{ width }}
    >
      <div
        onPointerDown={(e) => {
          e.preventDefault();
          const startX = e.clientX;
          const startW = width;
          const move = (ev: PointerEvent) => {
            const next = Math.min(520, Math.max(280, startW - (ev.clientX - startX)));
            dispatch({ type: "prefs", patch: { rightPanelWidth: next } });
          };
          const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
          };
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", up);
        }}
        className="group absolute top-0 -left-[3px] z-30 h-full w-[6px] cursor-col-resize"
        aria-label="Resize panel"
      >
        <div className="mx-auto h-full w-[2px] bg-transparent transition-colors group-hover:bg-accent/40" />
      </div>

      <div className="flex h-[42px] shrink-0 items-center justify-between border-b border-line px-3.5">
        <div className="flex items-center gap-1.5">
          <Radio className="h-3 w-3 text-signal" />
          <span className="text-[11px] font-medium tracking-[0.1em] text-muted uppercase">
            Intelligence
          </span>
        </div>
        <button
          onClick={() => dispatch({ type: "prefs", patch: { rightPanelOpen: false } })}
          className="rounded p-1 text-faint transition hover:bg-raised hover:text-ink"
          aria-label="Hide panel"
        >
          <PanelRightClose className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* live agent activity */}
        <section className="border-b border-line px-3.5 py-3">
          <header className="mb-2 flex items-center justify-between">
            <span className="text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
              AI activity
            </span>
            <button
              onClick={() => setSection("assistant")}
              className="text-[10px] text-accent transition hover:brightness-125"
            >
              open
            </button>
          </header>
          {liveRuntime ? (
            <div className="rounded-[7px] border border-accent/25 bg-accent/[0.06] p-2.5">
              <div className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                <span className="text-[11.5px] font-medium text-ink">
                  {liveRuntime.status === "review" ? "Reviewing results" : "Working"}
                </span>
                <Badge tone="accent" className="ml-auto">
                  {liveRuntime.mode}
                </Badge>
              </div>
              <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-muted">
                {liveRuntime.objective}
              </p>
              <div className="mt-2 space-y-1">
                {liveRuntime.steps.slice(0, 3).map((s) => (
                  <div key={s.id} className="flex items-center gap-1.5 text-[10.5px]">
                    <span
                      className={cn(
                        "h-1 w-1 rounded-full",
                        s.status === "done"
                          ? "bg-signal"
                          : s.status === "working"
                            ? "bg-accent"
                            : s.status === "failed"
                              ? "bg-danger"
                              : "bg-faint",
                      )}
                    />
                    <span className="truncate text-muted">{s.action}</span>
                    <span className="ml-auto shrink-0 text-faint">{s.agent}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="rounded-[7px] border border-line bg-surface/60 p-2.5">
              <div className="flex items-center gap-1.5">
                <CircleDot className="h-3 w-3 text-faint" />
                <span className="text-[11.5px] text-muted">Idle</span>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-faint">
                No agent is running. {briefing.suggestion}
              </p>
              <button
                onClick={() => setSection("assistant")}
                className="mt-2 flex items-center gap-1 text-[11px] text-accent transition hover:brightness-125"
              >
                Ask ORBIT <ArrowUpRight className="h-3 w-3" />
              </button>
            </div>
          )}
        </section>

        {/* next up */}
        <section className="border-b border-line px-3.5 py-3">
          <header className="mb-2 flex items-center justify-between">
            <span className="text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
              Next up
            </span>
            <span className="num text-[10px] text-faint">{briefing.counts.eventsToday} today</span>
          </header>
          <div className="space-y-1">
            {state.events
              .filter((e) => minutesUntil(e.end) > 0 && minutesUntil(e.start) < 60 * 8)
              .sort((a, b) => a.start.localeCompare(b.start))
              .slice(0, 3)
              .map((e) => (
                <button
                  key={e.id}
                  onClick={() => setSection("calendar")}
                  className="flex w-full items-center gap-2 rounded-[6px] px-1.5 py-1 text-left transition-colors hover:bg-raised/60"
                >
                  <Clock3 className="h-3 w-3 shrink-0 text-faint" />
                  <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink">{e.title}</span>
                  <span className="num shrink-0 text-[10px] text-faint">
                    {minutesUntil(e.start) <= 0 ? "now" : relative(e.start)}
                  </span>
                </button>
              ))}
            {!state.events.some((e) => minutesUntil(e.end) > 0 && minutesUntil(e.start) < 480) && (
              <p className="px-1.5 text-[11px] text-faint">Nothing scheduled in the next 8 hours.</p>
            )}
          </div>
        </section>

        {/* relevant tasks */}
        <section className="border-b border-line px-3.5 py-3">
          <header className="mb-2 flex items-center justify-between">
            <span className="text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
              In flight
            </span>
            <button
              onClick={() => setSection("tasks")}
              className="text-[10px] text-accent transition hover:brightness-125"
            >
              all
            </button>
          </header>
          <div className="space-y-1.5">
            {focusTasks.map((t) => {
              const project = state.projects.find((p) => p.id === t.projectId);
              return (
                <button
                  key={t.id}
                  onClick={() => {
                    select({ taskId: t.id });
                    setSection("tasks");
                  }}
                  className="block w-full rounded-[6px] border border-transparent px-1.5 py-1 text-left transition-colors hover:border-line hover:bg-raised/50"
                >
                  <div className="flex items-start gap-1.5">
                    <ListChecks className="mt-[2px] h-3 w-3 shrink-0 text-faint" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[11.5px] text-ink">{t.title}</div>
                      <div className="mt-0.5 flex items-center gap-1.5">
                        <span
                          className="h-1 w-1 rounded-full"
                          style={{ background: project?.color ?? "#5b6672" }}
                        />
                        <span className="truncate text-[10px] text-faint">
                          {project?.name ?? "No project"}
                        </span>
                        {t.status === "blocked" && <StatusPill status="blocked" />}
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
            {!focusTasks.length && (
              <p className="px-1.5 text-[11px] text-faint">Nothing in flight.</p>
            )}
          </div>
        </section>

        {/* project status */}
        <section className="border-b border-line px-3.5 py-3">
          <header className="mb-2 flex items-center justify-between">
            <span className="text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
              Projects
            </span>
            <button
              onClick={() => setSection("projects")}
              className="text-[10px] text-accent transition hover:brightness-125"
            >
              all
            </button>
          </header>
          <div className="space-y-2">
            {activeProjects.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  select({ projectId: p.id });
                  setSection("projects");
                }}
                className="block w-full text-left"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ background: p.color }}
                    />
                    <span className="truncate text-[11.5px] text-ink">{p.name}</span>
                  </span>
                  <span className="num shrink-0 text-[10px] text-faint">{p.progress}%</span>
                </div>
                <Progress value={p.progress} className="mt-1" />
              </button>
            ))}
          </div>
        </section>

        {/* automations */}
        <section className="border-b border-line px-3.5 py-3">
          <header className="mb-2 flex items-center justify-between">
            <span className="text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
              Background
            </span>
            <button
              onClick={() => setSection("automations")}
              className="text-[10px] text-accent transition hover:brightness-125"
            >
              all
            </button>
          </header>
          <div className="space-y-1">
            {runningAutomations.map((a) => (
              <div key={a.id} className="flex items-center gap-1.5 px-1.5 py-1">
                <Workflow className="h-3 w-3 shrink-0 text-faint" />
                <span className="min-w-0 flex-1 truncate text-[11px] text-muted">{a.name}</span>
                <span className="shrink-0 text-[10px] text-faint">
                  {a.lastRunAt ? relative(a.lastRunAt).replace(" ago", "") : "—"}
                </span>
              </div>
            ))}
          </div>
        </section>

        {/* suggestions */}
        <section className="border-b border-line px-3.5 py-3">
          <header className="mb-2 flex items-center gap-1.5">
            <Lightbulb className="h-3 w-3 text-warn" />
            <span className="text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
              Suggested next
            </span>
          </header>
          <div className="space-y-1">
            {[
              briefing.suggestion,
              `Clear the oldest blocked item: ${state.tasks.find((t) => t.status === "blocked")?.title ?? "none"}`,
              `Ask ORBIT to summarise what you finished today`,
            ]
              .filter(Boolean)
              .slice(0, 3)
              .map((s, i) => (
                <button
                  key={i}
                  onClick={() => setSection("assistant")}
                  className="flex w-full items-start gap-1.5 rounded-[6px] px-1.5 py-1 text-left transition-colors hover:bg-raised/60"
                >
                  <ChevronRight className="mt-[3px] h-3 w-3 shrink-0 text-faint" />
                  <span className="text-[11px] leading-relaxed text-muted">{s}</span>
                </button>
              ))}
          </div>
        </section>

        {/* activity tail */}
        <section className="px-3.5 py-3">
          <header className="mb-2 flex items-center justify-between">
            <span className="text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
              Recent
            </span>
            <button
              onClick={() => setSection("activity")}
              className="text-[10px] text-accent transition hover:brightness-125"
            >
              log
            </button>
          </header>
          <div className="space-y-1.5">
            {recentActivity.map((a) => (
              <div key={a.id} className="flex items-start gap-1.5">
                <Activity
                  className={cn(
                    "mt-[2px] h-2.5 w-2.5 shrink-0",
                    a.status === "ok"
                      ? "text-signal"
                      : a.status === "error"
                        ? "text-danger"
                        : "text-warn",
                  )}
                />
                <div className="min-w-0">
                  <div className="truncate text-[11px] text-muted">{a.title}</div>
                  <div className="text-[10px] text-faint">{relative(a.at)}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="flex h-7 shrink-0 items-center justify-between border-t border-line px-3.5 text-[10px] text-faint">
        <span className="flex items-center gap-1">
          <GripVertical className="h-2.5 w-2.5" /> drag edge to resize
        </span>
        <span>{briefing.grounded ? "grounded" : "partial data"}</span>
      </div>
    </aside>
  );
}
