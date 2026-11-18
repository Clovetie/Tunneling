"use client";

import React, { useMemo, useState } from "react";
import {
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleDashed,
  Flag,
  FolderKanban,
  Pin,
  Plus,
  Search,
  Sparkles,
  Target,
  Trash2,
  TrendingUp,
} from "lucide-react";
import { useOrbit, useActions } from "@/lib/store";
import { cn, fmtDate, relative, sortByPriority } from "@/lib/util";
import {
  Badge,
  Button,
  Field,
  Input,
  Modal,
  PriorityDot,
  Progress,
  Select,
  StatusPill,
  Textarea,
} from "@/components/ui/kit";
import type { Priority, Project } from "@/lib/types";

const COLORS = ["#7c9cff", "#a78bfa", "#4ade9b", "#f5b556", "#56cfd8", "#f2626b"];

export function ProjectsSection() {
  const { state, select, selection, setSection, dispatch } = useOrbit();
  const actions = useActions();
  const [createOpen, setCreateOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const [form, setForm] = useState({
    name: "",
    description: "",
    objectives: "",
    priority: "medium" as Priority,
    target: "",
    color: COLORS[0],
  });

  const projects = useMemo(() => {
    return state.projects
      .filter((p) => (statusFilter === "all" ? true : p.status === statusFilter))
      .filter((p) =>
        query.trim()
          ? `${p.name} ${p.description} ${p.tags.join(" ")}`.toLowerCase().includes(query.toLowerCase())
          : true,
      )
      .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.priority.localeCompare(a.priority));
  }, [state.projects, statusFilter, query]);

  const selected = state.projects.find((p) => p.id === selection.projectId) ?? projects[0] ?? null;
  const selectedTasks = selected ? state.tasks.filter((t) => t.projectId === selected.id) : [];

  function createProject() {
    if (!form.name.trim()) return;
    const p = actions.addProject({
      name: form.name.trim(),
      description: form.description.trim(),
      objectives: form.objectives
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      priority: form.priority,
      target: form.target ? new Date(form.target).toISOString() : null,
      color: form.color,
      status: "planning",
    });
    select({ projectId: p.id });
    setCreateOpen(false);
    setForm({ name: "", description: "", objectives: "", priority: "medium", target: "", color: COLORS[0] });
  }

  return (
    <div className="flex h-full min-h-0">
      {/* list */}
      <div
        className={cn(
          "w-full min-w-0 flex-col border-r border-line md:flex md:w-[330px] md:shrink-0",
          selection.projectId ? "hidden" : "flex",
        )}
      >
        <div className="flex h-[42px] items-center justify-between border-b border-line px-3.5">
          <div className="flex items-center gap-1.5">
            <FolderKanban className="h-3.5 w-3.5 text-accent" />
            <span className="text-[12px] font-medium text-ink">Projects</span>
            <span className="rounded-full bg-overlay px-1.5 py-[1px] text-[10px] text-faint">
              {state.projects.length}
            </span>
          </div>
          <Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3 w-3" /> New
          </Button>
        </div>

        <div className="space-y-1.5 border-b border-line px-2.5 py-2">
          <div className="relative">
            <Search className="absolute top-1/2 left-2 h-3 w-3 -translate-y-1/2 text-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter projects"
              className="w-full rounded-[6px] border border-line bg-surface py-1 pr-2 pl-6.5 text-[11.5px] text-ink placeholder:text-faint focus:border-accent/40 focus:outline-none"
            />
          </div>
          <div className="flex flex-wrap gap-1">
            {["all", "active", "planning", "paused", "shipped"].map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={cn(
                  "rounded-full px-2 py-0.5 text-[10px] capitalize transition",
                  statusFilter === s ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
                )}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {projects.map((p) => {
            const active = selected?.id === p.id;
            const projTasks = state.tasks.filter((t) => t.projectId === p.id);
            return (
              <button
                key={p.id}
                onClick={() => select({ projectId: p.id })}
                className={cn(
                  "mb-1 block w-full rounded-[8px] border px-2.5 py-2 text-left transition-all",
                  active
                    ? "border-accent/35 bg-accent/[0.07]"
                    : "border-transparent hover:border-line hover:bg-raised/50",
                )}
              >
                <div className="flex items-center gap-1.5">
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: p.color }}
                  />
                  <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-ink">
                    {p.name}
                  </span>
                  {p.pinned && <Pin className="h-2.5 w-2.5 shrink-0 text-accent" />}
                  <PriorityDot priority={p.priority} />
                </div>
                <p className="mt-0.5 line-clamp-1 pl-3.5 text-[10.5px] text-faint">
                  {p.description || "No description"}
                </p>
                <div className="mt-1.5 flex items-center gap-2 pl-3.5">
                  <Progress value={p.progress} className="flex-1" />
                  <span className="num shrink-0 text-[9.5px] text-faint">{p.progress}%</span>
                </div>
                <div className="mt-1 flex items-center gap-1.5 pl-3.5 text-[9.5px] text-faint">
                  <StatusPill status={p.status} />
                  <span>
                    {projTasks.filter((t) => t.status === "done").length}/{projTasks.length} tasks
                  </span>
                </div>
              </button>
            );
          })}
          {!projects.length && (
            <div className="px-3 py-8 text-center text-[11.5px] text-faint">
              No projects match this filter.
            </div>
          )}
        </div>
      </div>

      {/* detail */}
      <div
        className={cn(
          "min-w-0 flex-1 flex-col overflow-y-auto md:flex",
          selection.projectId ? "flex" : "hidden",
        )}
      >
        {selected ? (
          <>
            <div className="border-b border-line px-6 py-4">
              <button
                onClick={() => select({ projectId: null })}
                className="mb-2 flex items-center gap-1 text-[11px] text-faint transition hover:text-ink md:hidden"
              >
                <ChevronRight className="h-3 w-3 rotate-180" /> All projects
              </button>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ background: selected.color }}
                    />
                    <h1 className="text-[19px] font-semibold tracking-[-0.02em] text-ink">
                      {selected.name}
                    </h1>
                    <StatusPill status={selected.status} />
                    <PriorityDot priority={selected.priority} />
                  </div>
                  <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-muted">
                    {selected.description || "No description yet."}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[10.5px] text-faint">
                    <span className="flex items-center gap-1">
                      <Flag className="h-2.5 w-2.5" /> lead {selected.lead}
                    </span>
                    {selected.target && (
                      <span className="flex items-center gap-1">
                        <TrendingUp className="h-2.5 w-2.5" /> target{" "}
                        {fmtDate(selected.target)}
                      </span>
                    )}
                    <span>updated {relative(selected.updatedAt)}</span>
                    {selected.tags.map((t) => (
                      <span key={t} className="rounded border border-line px-1 text-[9.5px]">
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      dispatch({
                        type: "project.patch",
                        id: selected.id,
                        patch: { pinned: !selected.pinned },
                      })
                    }
                  >
                    <Pin className="h-3 w-3" /> {selected.pinned ? "Unpin" : "Pin"}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setSection("assistant");
                      setTimeout(
                        () =>
                          window.dispatchEvent(
                            new CustomEvent("orbit:prompt", {
                              detail: { prompt: `Help me plan and execute ${selected.name}` },
                            }),
                          ),
                        80,
                      );
                    }}
                  >
                    <Sparkles className="h-3 w-3" /> Plan with AI
                  </Button>
                </div>
              </div>

              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  { k: "Progress", v: `${selected.progress}%` },
                  { k: "Tasks", v: `${selectedTasks.filter((t) => t.status === "done").length}/${selectedTasks.length}` },
                  { k: "Milestones", v: `${selected.milestones.filter((m) => m.done).length}/${selected.milestones.length}` },
                  { k: "Open blockers", v: String(selectedTasks.filter((t) => t.status === "blocked").length) },
                ].map((m) => (
                  <div key={m.k} className="rounded-[7px] border border-line bg-abyss/50 px-2.5 py-2">
                    <div className="num text-[16px] leading-none font-semibold text-ink">{m.v}</div>
                    <div className="mt-1 text-[9.5px] tracking-wide text-faint uppercase">{m.k}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="grid gap-4 px-6 py-4 xl:grid-cols-2">
              {/* objectives */}
              <section className="panel overflow-hidden">
                <header className="flex items-center gap-1.5 border-b border-line px-3.5 py-2">
                  <Target className="h-3.5 w-3.5 text-accent" />
                  <span className="text-[11.5px] font-medium text-ink">Objectives</span>
                </header>
                <div className="space-y-1.5 p-3">
                  {selected.objectives.length ? (
                    selected.objectives.map((o, i) => (
                      <div key={i} className="flex items-start gap-2">
                        <span className="mt-[6px] h-1 w-1 shrink-0 rounded-full bg-accent/60" />
                        <span className="text-[11.5px] leading-relaxed text-muted">{o}</span>
                      </div>
                    ))
                  ) : (
                    <p className="text-[11.5px] text-faint">No objectives recorded.</p>
                  )}
                </div>
              </section>

              {/* milestones */}
              <section className="panel overflow-hidden">
                <header className="flex items-center justify-between border-b border-line px-3.5 py-2">
                  <div className="flex items-center gap-1.5">
                    <CircleDashed className="h-3.5 w-3.5 text-violet" />
                    <span className="text-[11.5px] font-medium text-ink">Milestones</span>
                  </div>
                  <button
                    onClick={() =>
                      dispatch({
                        type: "project.patch",
                        id: selected.id,
                        patch: {
                          milestones: [
                            ...selected.milestones,
                            {
                              id: `ms-${Date.now()}`,
                              title: "New milestone",
                              due: null,
                              done: false,
                            },
                          ],
                        },
                      })
                    }
                    className="text-[10px] text-accent transition hover:brightness-125"
                  >
                    + add
                  </button>
                </header>
                <div className="divide-y divide-line/60">
                  {selected.milestones.map((m) => (
                    <div key={m.id} className="flex items-center gap-2 px-3 py-1.5">
                      <button
                        onClick={() =>
                          dispatch({
                            type: "project.patch",
                            id: selected.id,
                            patch: {
                              milestones: selected.milestones.map((x) =>
                                x.id === m.id ? { ...x, done: !x.done } : x,
                              ),
                            },
                          })
                        }
                        className={cn(
                          "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border transition",
                          m.done
                            ? "border-signal bg-signal/20"
                            : "border-line-bright hover:border-signal",
                        )}
                      >
                        {m.done && <Check className="h-2 w-2 text-signal" />}
                      </button>
                      <span
                        className={cn(
                          "min-w-0 flex-1 truncate text-[11.5px]",
                          m.done ? "text-faint line-through" : "text-ink",
                        )}
                      >
                        {m.title}
                      </span>
                      {m.due && (
                        <span
                          className={cn(
                            "num shrink-0 text-[9.5px]",
                            !m.done && new Date(m.due) < new Date() ? "text-danger" : "text-faint",
                          )}
                        >
                          {fmtDate(m.due)}
                        </span>
                      )}
                    </div>
                  ))}
                  {!selected.milestones.length && (
                    <p className="px-3 py-3 text-[11.5px] text-faint">No milestones yet.</p>
                  )}
                </div>
              </section>
            </div>

            {/* tasks */}
            <section className="mx-6 mb-6 panel overflow-hidden">
              <header className="flex items-center justify-between border-b border-line px-3.5 py-2">
                <span className="text-[11.5px] font-medium text-ink">
                  Tasks · {selectedTasks.length}
                </span>
                <button
                  onClick={() => {
                    const t = actions.addTask({
                      title: `New task for ${selected.name}`,
                      projectId: selected.id,
                    });
                    select({ taskId: t.id });
                  }}
                  className="text-[10px] text-accent transition hover:brightness-125"
                >
                  + add task
                </button>
              </header>
              <div className="divide-y divide-line/60">
                {sortByPriority(selectedTasks).map((t) => (
                  <div
                    key={t.id}
                    className="group flex items-center gap-2 px-3.5 py-1.5 hover:bg-raised/40"
                  >
                    <button
                      onClick={() =>
                        actions.patchTask(t.id, {
                          status: t.status === "done" ? "todo" : "done",
                        })
                      }
                      className={cn(
                        "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border transition",
                        t.status === "done"
                          ? "border-signal bg-signal/20"
                          : "border-line-bright hover:border-signal",
                      )}
                    >
                      {t.status === "done" && <Check className="h-2 w-2 text-signal" />}
                    </button>
                    <span
                      className={cn(
                        "min-w-0 flex-1 truncate text-[11.5px]",
                        t.status === "done" ? "text-faint line-through" : "text-ink",
                      )}
                    >
                      {t.title}
                    </span>
                    <StatusPill status={t.status} />
                    <span className="num w-16 shrink-0 text-right text-[9.5px] text-faint">
                      {t.due ? relative(t.due) : "—"}
                    </span>
                  </div>
                ))}
                {!selectedTasks.length && (
                  <p className="px-3.5 py-4 text-[11.5px] text-faint">
                    No tasks in this project yet.
                  </p>
                )}
              </div>
            </section>

            <div className="mx-6 mb-6 flex items-center justify-between rounded-[8px] border border-danger/20 bg-danger/[0.03] px-3 py-2">
              <span className="text-[10.5px] text-faint">
                Archiving keeps the record; deleting removes the project and unlinks its tasks.
              </span>
              <div className="flex gap-1.5">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    dispatch({
                      type: "project.patch",
                      id: selected.id,
                      patch: {
                        status: selected.status === "archived" ? "active" : "archived",
                      },
                    })
                  }
                >
                  {selected.status === "archived" ? "Restore" : "Archive"}
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => {
                    dispatch({ type: "project.remove", id: selected.id });
                    select({ projectId: null });
                  }}
                >
                  <Trash2 className="h-3 w-3" /> Delete
                </Button>
              </div>
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center text-[12.5px] text-faint">
            Select or create a project.
          </div>
        )}
      </div>

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create project"
        description="Objectives matter more than the name. Write the one that changes a decision."
        width="max-w-xl"
      >
        <div className="space-y-3">
          <Field label="Name">
            <Input
              autoFocus
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="NightLoop"
            />
          </Field>
          <Field label="Description">
            <Textarea
              rows={2}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="What this project is and why it exists."
            />
          </Field>
          <Field label="Objectives" hint="one per line">
            <Textarea
              rows={3}
              value={form.objectives}
              onChange={(e) => setForm({ ...form, objectives: e.target.value })}
              placeholder={"Ship entity AI that stays readable\nProve modifiers compose"}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Priority">
              <Select
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: e.target.value as Priority })}
              >
                {["low", "medium", "high", "critical"].map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Target date">
              <Input
                type="date"
                value={form.target}
                onChange={(e) => setForm({ ...form, target: e.target.value })}
              />
            </Field>
          </div>
          <Field label="Accent">
            <div className="flex gap-1.5">
              {COLORS.map((c) => (
                <button
                  key={c}
                  onClick={() => setForm({ ...form, color: c })}
                  className={cn(
                    "h-6 w-6 rounded-[6px] border-2 transition",
                    form.color === c ? "border-ink" : "border-transparent",
                  )}
                  style={{ background: c }}
                  aria-label={c}
                />
              ))}
            </div>
          </Field>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setCreateOpen(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={createProject} disabled={!form.name.trim()}>
            Create project
          </Button>
        </div>
      </Modal>
    </div>
  );
}
