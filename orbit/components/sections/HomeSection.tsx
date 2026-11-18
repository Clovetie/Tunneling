"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BellPlus,
  Boxes,
  CalendarCheck,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FolderKanban,
  ListChecks,
  Sparkles,
  Upload,
  Workflow,
  AlertTriangle,
  CircleDot,
  Radio,
  TrendingUp,
} from "lucide-react";
import { useOrbit, useActions } from "@/lib/store";
import { buildBriefing, deriveFeed } from "@/lib/briefing";
import {
  cn,
  fmtClock,
  fmtLongDate,
  fmtTime,
  greeting,
  minutesUntil,
  relative,
  sortByPriority,
} from "@/lib/util";
import { Badge, Button, Progress, Ring, StatusPill } from "@/components/ui/kit";

const QUICK = [
  { id: "ask", label: "Ask AI", icon: Sparkles, hint: "⌘K" },
  { id: "task", label: "Create task", icon: ListChecks },
  { id: "project", label: "Create project", icon: FolderKanban },
  { id: "upload", label: "Upload file", icon: Upload },
  { id: "research", label: "Start research", icon: TrendingUp },
  { id: "automation", label: "Create automation", icon: Workflow },
  { id: "reminder", label: "Add reminder", icon: BellPlus },
];

export function HomeSection() {
  const { state, setSection, select, setCommandOpen } = useOrbit();
  const actions = useActions();
  const [now, setNow] = useState(new Date());
  const [briefingOpen, setBriefingOpen] = useState(true);

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const briefing = useMemo(() => buildBriefing(state), [state]);
  const feed = useMemo(() => deriveFeed(state), [state]);
  const todayEvents = useMemo(
    () =>
      state.events
        .filter((e) => {
          const d = new Date(e.start);
          const n = new Date();
          return (
            d.getFullYear() === n.getFullYear() &&
            d.getMonth() === n.getMonth() &&
            d.getDate() === n.getDate()
          );
        })
        .sort((a, b) => a.start.localeCompare(b.start)),
    [state.events],
  );
  const activeProjects = state.projects.filter((p) => p.status === "active");
  const outstanding = useMemo(
    () => sortByPriority(state.tasks.filter((t) => t.status !== "done")),
    [state.tasks],
  );
  const completedToday = state.tasks.filter(
    (t) => t.completedAt && new Date(t.completedAt).toDateString() === new Date().toDateString(),
  );

  function runQuick(id: string) {
    switch (id) {
      case "ask":
        setCommandOpen(true);
        break;
      case "task":
        window.dispatchEvent(new CustomEvent("orbit:quick", { detail: { kind: "task" } }));
        break;
      case "project":
        window.dispatchEvent(new CustomEvent("orbit:quick", { detail: { kind: "project" } }));
        break;
      case "upload":
        window.dispatchEvent(new CustomEvent("orbit:quick", { detail: { kind: "file" } }));
        break;
      case "research":
        setSection("assistant");
        setTimeout(
          () =>
            window.dispatchEvent(
              new CustomEvent("orbit:prompt", {
                detail: { prompt: "Create a research report about local AI models" },
              }),
            ),
          80,
        );
        break;
      case "automation":
        setSection("automations");
        break;
      case "reminder":
        window.dispatchEvent(new CustomEvent("orbit:quick", { detail: { kind: "reminder" } }));
        break;
      default:
        break;
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="relative border-b border-line px-6 py-5 md:px-8">
        <div className="grid-bg pointer-events-none absolute inset-0 opacity-60" />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-[10.5px] font-medium tracking-[0.14em] text-faint uppercase">
                {fmtLongDate(now, state.identity.timezone)}
              </span>
              <span className="h-1 w-1 rounded-full bg-line-bright" />
              <span className="num text-[11px] text-muted">
                {fmtClock(now, state.identity.timezone)}
              </span>
              <span className="h-1 w-1 rounded-full bg-line-bright" />
              <span className="text-[11px] text-faint">
                {state.identity.timezone}
              </span>
            </div>
            <h1 className="mt-1.5 text-[24px] leading-tight font-semibold tracking-[-0.025em] text-ink">
              {greeting(state.identity.timezone)}, {state.identity.name.split(" ")[0]}.
            </h1>
            <p className="mt-1 max-w-xl text-[13px] leading-relaxed text-muted text-balance-pretty">
              {briefing.headline}
            </p>
          </div>

          <div className="flex items-center gap-4 rounded-[10px] border border-line bg-surface/70 px-4 py-3 backdrop-blur">
            <Ring value={Math.round((completedToday.length / Math.max(1, completedToday.length + outstanding.length)) * 100)}>
              {completedToday.length}
            </Ring>
            <div className="text-[11px] leading-tight">
              <div className="font-medium text-ink">Today</div>
              <div className="text-faint">
                {completedToday.length} done · {outstanding.length} open
              </div>
              <div className="mt-0.5 text-faint">{briefing.counts.focusMinutes}m focus</div>
            </div>
          </div>
        </div>

        {/* quick actions */}
        <div className="relative mt-4 flex flex-wrap gap-1.5">
          {QUICK.map((q) => {
            const Icon = q.icon;
            return (
              <button
                key={q.id}
                onClick={() => runQuick(q.id)}
                className="group flex items-center gap-1.5 rounded-[7px] border border-line bg-surface/80 px-2.5 py-1.5 text-[11.5px] text-muted transition-all duration-150 hover:-translate-y-[1px] hover:border-line-bright hover:bg-raised hover:text-ink"
              >
                <Icon className="h-3 w-3 text-faint transition group-hover:text-accent" />
                {q.label}
                {q.hint && (
                  <span className="ml-0.5 rounded border border-line px-1 text-[9px] text-faint">
                    {q.hint}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid gap-4 px-6 py-5 md:px-8 xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        {/* left column */}
        <div className="min-w-0 space-y-4">
          {/* daily briefing */}
          <section className="panel overflow-hidden">
            <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <div className="flex items-center gap-2">
                <Sparkles className="h-3.5 w-3.5 text-accent" />
                <span className="text-[12px] font-medium text-ink">Daily briefing</span>
                <Badge tone={briefing.grounded ? "success" : "warn"} dot>
                  {briefing.grounded ? "grounded" : "partial data"}
                </Badge>
              </div>
              <div className="flex items-center gap-1">
                <span className="text-[10px] text-faint">
                  generated {relative(briefing.generatedAt)}
                </span>
                <button
                  onClick={() => setBriefingOpen((v) => !v)}
                  className="rounded p-1 text-faint transition hover:text-ink"
                >
                  <ChevronRight
                    className={cn("h-3.5 w-3.5 transition-transform", briefingOpen && "rotate-90")}
                  />
                </button>
              </div>
            </header>
            {briefingOpen && (
              <div className="px-4 py-3">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {[
                    { k: "Events", v: briefing.counts.eventsToday },
                    { k: "Open", v: briefing.counts.openTasks },
                    { k: "Overdue", v: briefing.counts.overdue },
                    { k: "Blocked", v: briefing.counts.blocked },
                  ].map((c) => (
                    <div key={c.k} className="rounded-[7px] border border-line bg-abyss/60 px-2.5 py-2">
                      <div className="num text-[17px] leading-none font-semibold tracking-tight text-ink">
                        {c.v}
                      </div>
                      <div className="mt-1 text-[10px] tracking-wide text-faint uppercase">
                        {c.k}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-3 space-y-1.5">
                  {briefing.lines.map((line, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-2 rounded-[6px] px-1.5 py-1 transition-colors hover:bg-raised/40"
                    >
                      <span
                        className={cn(
                          "mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full",
                          line.kind === "overdue" || line.kind === "blocked"
                            ? "bg-danger"
                            : line.kind === "priority"
                              ? "bg-accent"
                              : line.kind === "deadline"
                                ? "bg-warn"
                                : "bg-faint",
                        )}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="text-[12px] leading-snug text-ink">{line.text}</div>
                        {line.detail && (
                          <div className="mt-0.5 truncate text-[10.5px] text-faint">
                            {line.detail}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-3 flex items-start gap-2 rounded-[7px] border border-accent/20 bg-accent/[0.05] px-2.5 py-2">
                  <Sparkles className="mt-[1px] h-3 w-3 shrink-0 text-accent" />
                  <div className="min-w-0 flex-1">
                    <div className="text-[11.5px] leading-relaxed text-ink">
                      {briefing.suggestion}
                    </div>
                    <button
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
                      className="mt-1 flex items-center gap-1 text-[11px] text-accent transition hover:brightness-125"
                    >
                      Build the plan <ArrowRight className="h-3 w-3" />
                    </button>
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* intelligence feed */}
          <section className="panel overflow-hidden">
            <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <div className="flex items-center gap-2">
                <Radio className="h-3.5 w-3.5 text-signal" />
                <span className="text-[12px] font-medium text-ink">Intelligence feed</span>
                <span className="rounded-full bg-overlay px-1.5 py-[1px] text-[10px] text-faint">
                  {feed.length}
                </span>
              </div>
              <button
                onClick={() => setSection("activity")}
                className="text-[10.5px] text-accent transition hover:brightness-125"
              >
                Activity log
              </button>
            </header>
            <div className="divide-y divide-line/70">
              {feed.slice(0, 6).map((item) => (
                <div
                  key={item.id}
                  className="group flex items-start gap-2.5 px-4 py-2.5 transition-colors hover:bg-raised/40"
                >
                  <span
                    className={cn(
                      "mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full",
                      item.priority === "critical" || item.priority === "high"
                        ? "bg-danger"
                        : item.priority === "medium"
                          ? "bg-warn"
                          : "bg-faint",
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="truncate text-[12px] font-medium text-ink">{item.title}</span>
                      <span className="ml-auto shrink-0 text-[10px] text-faint">
                        {relative(item.at)}
                      </span>
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-faint">
                      {item.detail}
                    </p>
                    {item.actionTarget && (
                      <button
                        onClick={() => {
                          if (item.actionTarget?.id) select({ projectId: item.actionTarget.id });
                          setSection(item.actionTarget!.section);
                        }}
                        className="mt-1 flex items-center gap-1 text-[10.5px] text-accent opacity-0 transition group-hover:opacity-100"
                      >
                        {item.actionLabel} <ArrowUpRight className="h-2.5 w-2.5" />
                      </button>
                    )}
                  </div>
                </div>
              ))}
              {!feed.length && (
                <div className="px-4 py-8 text-center text-[12px] text-faint">
                  Nothing needs attention. Feed is derived from live state, so this means none.
                </div>
              )}
            </div>
          </section>

          {/* active projects */}
          <section className="panel overflow-hidden">
            <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <div className="flex items-center gap-2">
                <FolderKanban className="h-3.5 w-3.5 text-accent" />
                <span className="text-[12px] font-medium text-ink">Active projects</span>
                <span className="rounded-full bg-overlay px-1.5 py-[1px] text-[10px] text-faint">
                  {activeProjects.length}
                </span>
              </div>
              <button
                onClick={() => setSection("projects")}
                className="text-[10.5px] text-accent transition hover:brightness-125"
              >
                All projects
              </button>
            </header>
            <div className="grid gap-2 p-3 sm:grid-cols-2">
              {activeProjects.slice(0, 4).map((p) => {
                const projTasks = state.tasks.filter((t) => t.projectId === p.id);
                const done = projTasks.filter((t) => t.status === "done").length;
                return (
                  <button
                    key={p.id}
                    onClick={() => {
                      select({ projectId: p.id });
                      setSection("projects");
                    }}
                    className="group rounded-[8px] border border-line bg-abyss/50 p-3 text-left transition-all duration-150 hover:border-line-bright hover:bg-raised/60"
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ background: p.color }}
                      />
                      <span className="truncate text-[12.5px] font-medium text-ink">{p.name}</span>
                      <StatusPill status={p.status} />
                    </div>
                    <p className="mt-1 line-clamp-1 text-[11px] text-faint">{p.description}</p>
                    <div className="mt-2 flex items-center gap-2">
                      <Progress value={p.progress} />
                      <span className="num shrink-0 text-[10px] text-faint">{p.progress}%</span>
                    </div>
                    <div className="mt-1.5 flex items-center justify-between text-[10px] text-faint">
                      <span>
                        {done}/{projTasks.length} tasks
                      </span>
                      <span>
                        {p.milestones.filter((m) => m.done).length}/{p.milestones.length} milestones
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
        </div>

        {/* right column */}
        <div className="min-w-0 space-y-4">
          {/* today schedule */}
          <section className="panel overflow-hidden">
            <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <div className="flex items-center gap-2">
                <CalendarDays className="h-3.5 w-3.5 text-muted" />
                <span className="text-[12px] font-medium text-ink">Today</span>
              </div>
              <button
                onClick={() => setSection("calendar")}
                className="text-[10.5px] text-accent transition hover:brightness-125"
              >
                Calendar
              </button>
            </header>
            <div className="max-h-[300px] overflow-y-auto p-2.5">
              {todayEvents.map((e) => {
                const past = minutesUntil(e.end) < 0;
                const live = minutesUntil(e.start) <= 0 && minutesUntil(e.end) > 0;
                return (
                  <div
                    key={e.id}
                    className={cn(
                      "flex items-stretch gap-2 rounded-[6px] px-1.5 py-1 transition-colors",
                      live && "bg-accent/[0.07]",
                      past && "opacity-45",
                    )}
                  >
                    <div className="w-[38px] shrink-0 pt-[1px] text-right">
                      <div className="num text-[10.5px] text-muted">{fmtTime(e.start)}</div>
                    </div>
                    <div
                      className={cn(
                        "w-[2px] shrink-0 rounded-full",
                        e.kind === "focus"
                          ? "bg-accent"
                          : e.kind === "deadline"
                            ? "bg-danger"
                            : e.kind === "reminder"
                              ? "bg-warn"
                              : "bg-line-bright",
                      )}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[11.5px] text-ink">{e.title}</div>
                      <div className="text-[10px] text-faint">
                        {e.kind === "focus" ? "Focus block" : e.kind === "routine" ? "Routine" : e.kind}
                        {live && <span className="ml-1 text-accent">· now</span>}
                      </div>
                    </div>
                  </div>
                );
              })}
              {!todayEvents.length && (
                <div className="px-2 py-6 text-center text-[11.5px] text-faint">
                  No events recorded today.
                </div>
              )}
            </div>
          </section>

          {/* outstanding tasks */}
          <section className="panel overflow-hidden">
            <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <div className="flex items-center gap-2">
                <ListChecks className="h-3.5 w-3.5 text-muted" />
                <span className="text-[12px] font-medium text-ink">Outstanding</span>
                <span className="rounded-full bg-overlay px-1.5 py-[1px] text-[10px] text-faint">
                  {outstanding.length}
                </span>
              </div>
              <button
                onClick={() => setSection("tasks")}
                className="text-[10.5px] text-accent transition hover:brightness-125"
              >
                All tasks
              </button>
            </header>
            <div className="divide-y divide-line/60">
              {outstanding.slice(0, 6).map((t) => {
                const project = state.projects.find((p) => p.id === t.projectId);
                return (
                  <div
                    key={t.id}
                    className="group flex items-center gap-2 px-3 py-2 transition-colors hover:bg-raised/40"
                  >
                    <button
                      onClick={() =>
                        actions.patchTask(t.id, {
                          status: t.status === "done" ? "todo" : "done",
                        })
                      }
                      className="h-3.5 w-3.5 shrink-0 rounded-full border border-line-bright transition-colors hover:border-signal hover:bg-signal/20"
                      aria-label="Complete"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[11.5px] text-ink">{t.title}</div>
                      <div className="flex items-center gap-1.5">
                        <span
                          className="h-1 w-1 rounded-full"
                          style={{ background: project?.color ?? "#5b6672" }}
                        />
                        <span className="truncate text-[10px] text-faint">
                          {project?.name ?? "No project"}
                        </span>
                        {t.status === "blocked" && <span className="text-[9.5px] text-danger">blocked</span>}
                      </div>
                    </div>
                    <span className="num shrink-0 text-[10px] text-faint">
                      {t.due ? relative(t.due).replace(" ago", "") : "—"}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>

          {/* reminders */}
          <section className="panel overflow-hidden">
            <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <div className="flex items-center gap-2">
                <BellPlus className="h-3.5 w-3.5 text-warn" />
                <span className="text-[12px] font-medium text-ink">Reminders</span>
              </div>
            </header>
            <div className="divide-y divide-line/60">
              {state.reminders
                .filter((r) => !r.done)
                .slice(0, 4)
                .map((r) => (
                  <div key={r.id} className="flex items-center gap-2 px-3 py-2">
                    <Clock3 className="h-3 w-3 shrink-0 text-faint" />
                    <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink">{r.text}</span>
                    <span className="num shrink-0 text-[10px] text-faint">{relative(r.at)}</span>
                    <button
                      onClick={() => actions.patchTask(r.id, {})}
                      className="hidden"
                      aria-hidden
                    />
                  </div>
                ))}
            </div>
          </section>

          {/* integrations strip */}
          <section className="panel p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
                Connections
              </span>
              <button
                onClick={() => setSection("integrations")}
                className="text-[10.5px] text-accent transition hover:brightness-125"
              >
                Manage
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {state.integrations.map((i) => (
                <div
                  key={i.id}
                  title={`${i.name} — ${i.status}`}
                  className={cn(
                    "flex h-7 w-7 items-center justify-center rounded-[6px] border text-[11px] font-semibold transition-colors",
                    i.status === "connected"
                      ? "border-line bg-raised text-muted"
                      : i.status === "error"
                        ? "border-danger/40 bg-danger/10 text-danger"
                        : i.status === "syncing"
                          ? "border-accent/40 bg-accent/10 text-accent"
                          : "border-line/60 bg-abyss text-faint",
                  )}
                >
                  {i.glyph}
                </div>
              ))}
            </div>
            <div className="mt-2 flex items-center gap-1.5 text-[10px] text-faint">
              <CircleDot className="h-2.5 w-2.5 text-signal" />
              {state.integrations.filter((i) => i.status === "connected").length} live ·{" "}
              {state.integrations.filter((i) => i.status === "error").length} error
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
