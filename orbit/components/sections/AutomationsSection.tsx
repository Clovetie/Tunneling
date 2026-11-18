"use client";

import React, { useMemo, useState } from "react";
import {
  ArrowRight,
  Bell,
  Bot,
  CalendarClock,
  Check,
  ChevronRight,
  CircleAlert,
  GitBranch,
  Play,
  Plus,
  Power,
  ShieldQuestion,
  Terminal,
  Workflow,
  Zap,
} from "lucide-react";
import { useOrbit, useActions } from "@/lib/store";
import { cn, relative, uid } from "@/lib/util";
import {
  Badge,
  Button,
  Field,
  Input,
  Modal,
  Progress,
  Select,
  StatusPill,
  Textarea,
} from "@/components/ui/kit";
import type { Automation, AutomationRun, TriggerKind } from "@/lib/types";

const STEP_ICON = {
  trigger: Zap,
  ai: Bot,
  action: Terminal,
  branch: GitBranch,
  notify: Bell,
} as const;

const TRIGGER_TONE: Record<TriggerKind, string> = {
  schedule: "text-accent",
  event: "text-violet",
  condition: "text-warn",
  manual: "text-muted",
};

export function AutomationsSection() {
  const { state, selection, select, setSection, dispatch } = useOrbit();
  const actions = useActions();
  const [createOpen, setCreateOpen] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    description: "",
    trigger: "manual" as TriggerKind,
    detail: "",
  });

  const selected =
    state.automations.find((a) => a.id === selection.automationId) ?? state.automations[0] ?? null;

  const totals = useMemo(() => {
    const runs = state.automations.flatMap((a) => a.runs);
    const success = runs.filter((r) => r.status === "success").length;
    return {
      enabled: state.automations.filter((a) => a.enabled).length,
      runs: runs.length,
      successRate: runs.length ? Math.round((success / runs.length) * 100) : 0,
      failed: runs.filter((r) => r.status === "failed").length,
    };
  }, [state.automations]);

  async function runAutomation(auto: Automation) {
    setRunningId(auto.id);
    await new Promise((r) => setTimeout(r, 1300));
    const run: AutomationRun = {
      id: uid("run"),
      at: new Date().toISOString(),
      status: "success",
      durationMs: 1800 + Math.round(Math.random() * 2400),
      log: [
        "trigger fired manually",
        ...auto.steps.slice(1).map((s) => `${s.actor}: ${s.title}`),
        "exit 0",
      ],
    };
    actions.dispatchRun(auto.id, run);
    setRunningId(null);
    setRunId(run.id);
  }

  return (
    <div className="flex h-full min-h-0">
      <div
        className={cn(
          "w-full min-w-0 flex-col border-r border-line lg:flex lg:w-[340px] lg:shrink-0",
          selection.automationId ? "hidden" : "flex",
        )}
      >
        <div className="flex h-[42px] items-center justify-between border-b border-line px-3.5">
          <div className="flex items-center gap-1.5">
            <Workflow className="h-3.5 w-3.5 text-accent" />
            <span className="text-[12px] font-medium text-ink">Automations</span>
            <span className="rounded-full bg-overlay px-1.5 py-[1px] text-[10px] text-faint">
              {state.automations.length}
            </span>
          </div>
          <Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3 w-3" /> New
          </Button>
        </div>

        <div className="grid grid-cols-3 gap-px border-b border-line bg-line">
          {[
            { k: "Enabled", v: `${totals.enabled}/${state.automations.length}` },
            { k: "Success", v: `${totals.successRate}%` },
            { k: "Failed runs", v: String(totals.failed) },
          ].map((s) => (
            <div key={s.k} className="bg-abyss px-2.5 py-2">
              <div className="num text-[14px] leading-none font-semibold text-ink">{s.v}</div>
              <div className="mt-0.5 text-[9px] tracking-wide text-faint uppercase">{s.k}</div>
            </div>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {state.automations.map((a) => (
            <button
              key={a.id}
              onClick={() => select({ automationId: a.id })}
              className={cn(
                "mb-1 block w-full rounded-[8px] border px-2.5 py-2 text-left transition",
                selected?.id === a.id
                  ? "border-accent/35 bg-accent/[0.07]"
                  : "border-transparent hover:border-line hover:bg-raised/50",
              )}
            >
              <div className="flex items-center gap-1.5">
                <Zap className={cn("h-3 w-3 shrink-0", TRIGGER_TONE[a.trigger])} />
                <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
                  {a.name}
                </span>
                {runningId === a.id ? (
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                ) : (
                  <span
                    className={cn(
                      "h-1.5 w-1.5 rounded-full",
                      a.enabled ? "bg-signal" : "bg-faint",
                    )}
                  />
                )}
              </div>
              <p className="mt-0.5 line-clamp-1 pl-4 text-[10px] text-faint">{a.triggerDetail}</p>
              <div className="mt-1 flex items-center gap-1.5 pl-4 text-[9.5px] text-faint">
                <span>
                  {a.runs.length} {a.runs.length === 1 ? "run" : "runs"}
                </span>
                <span className="h-1 w-1 rounded-full bg-line-bright" />
                <span>{Math.round(a.successRate * 100)}% ok</span>
                {a.requiresApproval && (
                  <span className="flex items-center gap-0.5 text-warn">
                    <ShieldQuestion className="h-2 w-2" /> gated
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div
        className={cn(
          "min-w-0 flex-1 overflow-y-auto lg:block",
          selection.automationId ? "block" : "hidden",
        )}
      >
        {selected ? (
          <div className="mx-auto max-w-3xl px-6 py-5">
            <button
              onClick={() => {
                select({ automationId: null });
                dispatch({ type: "navigate", section: "automations" });
              }}
              className="mb-2 flex items-center gap-1 text-[11px] text-faint transition hover:text-ink lg:hidden"
            >
              <ChevronRight className="h-3 w-3 rotate-180" /> All automations
            </button>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h1 className="text-[19px] font-semibold tracking-[-0.02em] text-ink">
                    {selected.name}
                  </h1>
                  <StatusPill status={selected.enabled ? "active" : "paused"} />
                  {selected.requiresApproval && <Badge tone="warn">approval required</Badge>}
                </div>
                <p className="mt-1 max-w-xl text-[12px] leading-relaxed text-muted">
                  {selected.description}
                </p>
                <div className="mt-1.5 flex items-center gap-2 text-[10.5px] text-faint">
                  <CalendarClock className="h-2.5 w-2.5" />
                  {selected.triggerDetail}
                  {selected.lastRunAt && <span>· last run {relative(selected.lastRunAt)}</span>}
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    actions.patchAutomation(selected.id, { enabled: !selected.enabled })
                  }
                >
                  <Power className="h-3 w-3" /> {selected.enabled ? "Disable" : "Enable"}
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  loading={runningId === selected.id}
                  onClick={() => runAutomation(selected)}
                >
                  <Play className="h-3 w-3" /> Run now
                </Button>
              </div>
            </div>

            <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
              <section className="panel overflow-hidden">
                <header className="flex items-center gap-1.5 border-b border-line px-3.5 py-2">
                  <Workflow className="h-3.5 w-3.5 text-accent" />
                  <span className="text-[11.5px] font-medium text-ink">Steps</span>
                  <span className="ml-auto text-[10px] text-faint">{selected.steps.length} nodes</span>
                </header>
                <div className="p-3">
                  {selected.steps.map((s, i) => {
                    const Icon = STEP_ICON[s.kind];
                    return (
                      <div key={s.id}>
                        <div className="flex items-start gap-2.5">
                          <div
                            className={cn(
                              "flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] border",
                              s.kind === "trigger"
                                ? "border-accent/30 bg-accent/10 text-accent"
                                : s.kind === "ai"
                                  ? "border-violet/30 bg-violet/10 text-violet"
                                  : s.kind === "branch"
                                    ? "border-warn/30 bg-warn/10 text-warn"
                                    : s.kind === "notify"
                                      ? "border-signal/30 bg-signal/10 text-signal"
                                      : "border-line bg-raised text-muted",
                            )}
                          >
                            <Icon className="h-3 w-3" />
                          </div>
                          <div className="min-w-0 flex-1 pb-0.5">
                            <div className="text-[11.5px] text-ink">{s.title}</div>
                            <div className="text-[10px] text-faint">{s.detail}</div>
                            <div className="mt-0.5 flex items-center gap-1.5">
                              <span className="rounded border border-line px-1 text-[9px] text-faint">
                                {s.actor}
                              </span>
                              {s.tool && (
                                <span className="font-mono text-[9px] text-cyan">{s.tool}</span>
                              )}
                            </div>
                          </div>
                        </div>
                        {i < selected.steps.length - 1 && (
                          <div className="ml-3 h-3 w-px bg-line" />
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className="panel overflow-hidden">
                <header className="flex items-center gap-1.5 border-b border-line px-3.5 py-2">
                  <Terminal className="h-3.5 w-3.5 text-muted" />
                  <span className="text-[11.5px] font-medium text-ink">Recent runs</span>
                </header>
                <div className="divide-y divide-line/60">
                  {selected.runs.map((r) => (
                    <button
                      key={r.id}
                      onClick={() => setRunId(r.id)}
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left transition hover:bg-raised/50"
                    >
                      {r.status === "success" ? (
                        <Check className="h-3 w-3 shrink-0 text-signal" />
                      ) : r.status === "failed" ? (
                        <CircleAlert className="h-3 w-3 shrink-0 text-danger" />
                      ) : (
                        <ArrowRight className="h-3 w-3 shrink-0 text-faint" />
                      )}
                      <span className="min-w-0 flex-1 truncate text-[10.5px] text-muted">
                        {r.log.slice(-1)[0] ?? r.status}
                      </span>
                      <span className="num shrink-0 text-[9.5px] text-faint">
                        {relative(r.at)}
                      </span>
                    </button>
                  ))}
                  {!selected.runs.length && (
                    <p className="px-3 py-4 text-[11px] text-faint">No runs recorded yet.</p>
                  )}
                </div>
                <div className="border-t border-line px-3 py-2">
                  <div className="mt-0.5 flex items-center gap-2">
                    <Progress
                      value={selected.successRate * 100}
                      tone={selected.successRate > 0.9 ? "success" : "warn"}
                    />
                    <span className="num shrink-0 text-[9.5px] text-faint">
                      {Math.round(selected.successRate * 100)}%
                    </span>
                  </div>
                </div>
              </section>
            </div>

            <div className="mt-3 rounded-[8px] border border-line bg-abyss/50 px-3 py-2 text-[10.5px] leading-relaxed text-faint">
              Failure policy: three consecutive failures disable the workflow instead of retrying
              forever. Irreversible steps stop at the approval gate.
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-[12.5px] text-faint">
            No automations yet.
          </div>
        )}
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="Create automation" width="max-w-xl">
        <div className="space-y-3">
          <Field label="Name">
            <Input
              autoFocus
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Nightly licence verification"
            />
          </Field>
          <Field label="Description">
            <Textarea
              rows={2}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="What it does, and what it must never do."
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Trigger">
              <Select
                value={form.trigger}
                onChange={(e) => setForm({ ...form, trigger: e.target.value as TriggerKind })}
              >
                <option value="schedule">Schedule</option>
                <option value="event">Event</option>
                <option value="condition">Condition</option>
                <option value="manual">Manual</option>
              </Select>
            </Field>
            <Field label="Trigger detail">
              <Input
                value={form.detail}
                onChange={(e) => setForm({ ...form, detail: e.target.value })}
                placeholder="Daily · 02:00"
              />
            </Field>
          </div>
        </div>
        <div className="mt-5 flex items-center justify-between">
          <span className="text-[10.5px] text-faint">
            Created disabled-safe: nothing runs until you enable it.
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!form.name.trim()}
              onClick={() => {
                const a = actions.addAutomation({
                  name: form.name.trim(),
                  description: form.description.trim(),
                  trigger: form.trigger,
                  triggerDetail: form.detail || form.trigger,
                  enabled: false,
                });
                select({ automationId: a.id });
                setForm({ name: "", description: "", trigger: "manual", detail: "" });
                setCreateOpen(false);
              }}
            >
              Create
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={Boolean(runId)}
        onClose={() => setRunId(null)}
        title="Run log"
        width="max-w-lg"
      >
        {(() => {
          const run = state.automations.flatMap((a) => a.runs).find((r) => r.id === runId);
          if (!run) return null;
          return (
            <div>
              <div className="mb-2 flex items-center gap-2">
                <StatusPill status={run.status} />
                <span className="num text-[10.5px] text-faint">
                  {run.durationMs}ms · {relative(run.at)}
                </span>
              </div>
              <pre className="max-h-[40vh] overflow-auto rounded-[7px] border border-line bg-void p-3 font-mono text-[11px] leading-relaxed text-muted">
                {run.log.join("\n")}
              </pre>
            </div>
          );
        })()}
      </Modal>
    </div>
  );
}
