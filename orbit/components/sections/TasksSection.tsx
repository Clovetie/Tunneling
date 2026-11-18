"use client";

import React, { useMemo, useState } from "react";
import {
  ArrowDownUp,
  Check,
  Columns3,
  Filter,
  List,
  ListChecks,
  Plus,
  Search,
  Sparkles,
  Trash2,
  X,
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
  Select,
  StatusPill,
  Textarea,
} from "@/components/ui/kit";
import type { Priority, Task } from "@/lib/types";

const COLUMNS: { id: Task["status"]; label: string; tone: string }[] = [
  { id: "todo", label: "Todo", tone: "bg-faint" },
  { id: "doing", label: "In progress", tone: "bg-accent" },
  { id: "blocked", label: "Blocked", tone: "bg-danger" },
  { id: "done", label: "Done", tone: "bg-signal" },
];

export function TasksSection() {
  const { state, select, selection, setSection } = useOrbit();
  const actions = useActions();
  const [view, setView] = useState<"board" | "list">("board");
  const [query, setQuery] = useState("");
  const [projectFilter, setProjectFilter] = useState("all");
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(selection.taskId ?? null);
  const [form, setForm] = useState({
    title: "",
    notes: "",
    projectId: "",
    priority: "medium" as Priority,
    due: "",
    estimate: "",
  });
  const [dragId, setDragId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    return state.tasks
      .filter((t) => (projectFilter === "all" ? true : t.projectId === projectFilter))
      .filter((t) => (priorityFilter === "all" ? true : t.priority === priorityFilter))
      .filter((t) =>
        query.trim() ? `${t.title} ${t.notes}`.toLowerCase().includes(query.toLowerCase()) : true,
      );
  }, [state.tasks, projectFilter, priorityFilter, query]);

  const detail = state.tasks.find((t) => t.id === detailId) ?? null;

  function createTask() {
    if (!form.title.trim()) return;
    actions.addTask({
      title: form.title.trim(),
      notes: form.notes.trim(),
      projectId: form.projectId || null,
      priority: form.priority,
      due: form.due ? new Date(form.due).toISOString() : null,
      estimateMin: form.estimate ? Number(form.estimate) : null,
    });
    setCreateOpen(false);
    setForm({ title: "", notes: "", projectId: "", priority: "medium", due: "", estimate: "" });
  }

  const stats = {
    open: state.tasks.filter((t) => t.status !== "done").length,
    doing: state.tasks.filter((t) => t.status === "doing").length,
    blocked: state.tasks.filter((t) => t.status === "blocked").length,
    overdue: state.tasks.filter(
      (t) => t.status !== "done" && t.due && new Date(t.due) < new Date(),
    ).length,
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
        <div className="flex items-center gap-1.5">
          <ListChecks className="h-3.5 w-3.5 text-accent" />
          <span className="text-[13px] font-semibold text-ink">Tasks</span>
          <span className="rounded-full bg-overlay px-1.5 py-[1px] text-[10px] text-faint">
            {state.tasks.length}
          </span>
        </div>

        <div className="ml-2 hidden items-center gap-2 text-[10px] text-faint sm:flex">
          <span className="text-muted">{stats.open} open</span>
          <span className="h-2.5 w-px bg-line" />
          <span className="text-accent">{stats.doing} in progress</span>
          <span className="h-2.5 w-px bg-line" />
          <span className="text-danger">{stats.blocked} blocked</span>
          <span className="h-2.5 w-px bg-line" />
          <span className={stats.overdue ? "text-warn" : ""}>{stats.overdue} overdue</span>
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <div className="relative hidden sm:block">
            <Search className="absolute top-1/2 left-2 h-3 w-3 -translate-y-1/2 text-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search tasks"
              className="w-[150px] rounded-[6px] border border-line bg-surface py-1 pr-2 pl-6.5 text-[11.5px] text-ink placeholder:text-faint focus:border-accent/40 focus:outline-none"
            />
          </div>
          <Select
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            className="h-7 w-[130px] py-0 text-[11px]"
          >
            <option value="all">All projects</option>
            {state.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
          <Select
            value={priorityFilter}
            onChange={(e) => setPriorityFilter(e.target.value)}
            className="h-7 w-[110px] py-0 text-[11px] capitalize"
          >
            <option value="all">Any priority</option>
            {["critical", "high", "medium", "low"].map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
          <div className="flex overflow-hidden rounded-[6px] border border-line">
            <button
              onClick={() => setView("board")}
              className={cn(
                "px-1.5 py-1 transition",
                view === "board" ? "bg-raised text-ink" : "text-faint hover:text-muted",
              )}
              aria-label="Board view"
            >
              <Columns3 className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => setView("list")}
              className={cn(
                "px-1.5 py-1 transition",
                view === "list" ? "bg-raised text-ink" : "text-faint hover:text-muted",
              )}
              aria-label="List view"
            >
              <List className="h-3.5 w-3.5" />
            </button>
          </div>
          <Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3 w-3" /> Task
          </Button>
        </div>
      </div>

      {view === "board" ? (
        <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden p-3">
          <div className="flex h-full min-w-max gap-2.5">
            {COLUMNS.map((col) => {
              const items = sortByPriority(filtered.filter((t) => t.status === col.id));
              return (
                <div
                  key={col.id}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => {
                    if (dragId) actions.patchTask(dragId, { status: col.id });
                    setDragId(null);
                  }}
                  className="flex w-[266px] shrink-0 flex-col rounded-[9px] border border-line bg-surface/40"
                >
                  <div className="flex items-center gap-1.5 border-b border-line px-2.5 py-2">
                    <span className={cn("h-1.5 w-1.5 rounded-full", col.tone)} />
                    <span className="text-[11.5px] font-medium text-ink">{col.label}</span>
                    <span className="num ml-auto text-[10px] text-faint">{items.length}</span>
                  </div>
                  <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-1.5">
                    {items.map((t) => {
                      const project = state.projects.find((p) => p.id === t.projectId);
                      const overdue = t.due && t.status !== "done" && new Date(t.due) < new Date();
                      return (
                        <div
                          key={t.id}
                          draggable
                          onDragStart={() => setDragId(t.id)}
                          onDragEnd={() => setDragId(null)}
                          onClick={() => setDetailId(t.id)}
                          className={cn(
                            "group cursor-pointer rounded-[7px] border border-line bg-abyss/70 p-2.5 transition-all hover:border-line-bright hover:bg-raised",
                            dragId === t.id && "opacity-50",
                          )}
                        >
                          <div className="flex items-start gap-1.5">
                            <PriorityDot priority={t.priority} />
                            <span
                              className={cn(
                                "min-w-0 flex-1 text-[11.5px] leading-snug",
                                t.status === "done" ? "text-faint line-through" : "text-ink",
                              )}
                            >
                              {t.title}
                            </span>
                          </div>
                          {t.notes && (
                            <p className="mt-1 line-clamp-2 pl-3 text-[10px] leading-relaxed text-faint">
                              {t.notes}
                            </p>
                          )}
                          <div className="mt-1.5 flex items-center gap-1.5 pl-3">
                            {project && (
                              <span className="flex items-center gap-1 truncate">
                                <span
                                  className="h-1 w-1 shrink-0 rounded-full"
                                  style={{ background: project.color }}
                                />
                                <span className="truncate text-[9.5px] text-faint">
                                  {project.name}
                                </span>
                              </span>
                            )}
                            <span
                              className={cn(
                                "num ml-auto shrink-0 text-[9.5px]",
                                overdue ? "text-danger" : "text-faint",
                              )}
                            >
                              {t.due ? relative(t.due) : ""}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                    {!items.length && (
                      <div className="rounded-[7px] border border-dashed border-line px-2 py-6 text-center text-[10.5px] text-faint">
                        Drop here
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full border-collapse">
            <thead className="sticky top-0 z-10 bg-abyss">
              <tr className="border-b border-line">
                {["", "Task", "Project", "Status", "Priority", "Due", "Estimate"].map((h) => (
                  <th
                    key={h}
                    className="px-3 py-2 text-left text-[10px] font-medium tracking-wide text-faint uppercase"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortByPriority(filtered).map((t) => {
                const project = state.projects.find((p) => p.id === t.projectId);
                return (
                  <tr
                    key={t.id}
                    onClick={() => setDetailId(t.id)}
                    className="cursor-pointer border-b border-line/50 hover:bg-raised/40"
                  >
                    <td className="w-8 px-3 py-1.5">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          actions.patchTask(t.id, {
                            status: t.status === "done" ? "todo" : "done",
                          });
                        }}
                        className={cn(
                          "flex h-3.5 w-3.5 items-center justify-center rounded-full border transition",
                          t.status === "done"
                            ? "border-signal bg-signal/20"
                            : "border-line-bright hover:border-signal",
                        )}
                      >
                        {t.status === "done" && <Check className="h-2 w-2 text-signal" />}
                      </button>
                    </td>
                    <td className="px-3 py-1.5">
                      <span
                        className={cn(
                          "text-[12px]",
                          t.status === "done" ? "text-faint line-through" : "text-ink",
                        )}
                      >
                        {t.title}
                      </span>
                    </td>
                    <td className="px-3 py-1.5">
                      <span className="flex items-center gap-1.5">
                        {project && (
                          <span
                            className="h-1.5 w-1.5 rounded-full"
                            style={{ background: project.color }}
                          />
                        )}
                        <span className="text-[11px] text-muted">{project?.name ?? "—"}</span>
                      </span>
                    </td>
                    <td className="px-3 py-1.5">
                      <StatusPill status={t.status} />
                    </td>
                    <td className="px-3 py-1.5">
                      <span className="flex items-center gap-1.5 text-[11px] text-muted capitalize">
                        <PriorityDot priority={t.priority} />
                        {t.priority}
                      </span>
                    </td>
                    <td className="num px-3 py-1.5 text-[11px] text-faint">
                      {t.due ? relative(t.due) : "—"}
                    </td>
                    <td className="num px-3 py-1.5 text-[11px] text-faint">
                      {t.estimateMin ? `${t.estimateMin}m` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!filtered.length && (
            <div className="px-4 py-12 text-center text-[12px] text-faint">No tasks match.</div>
          )}
        </div>
      )}

      {/* detail drawer */}
      {detail && (
        <div className="fixed inset-0 z-[80] flex justify-end">
          <div className="anim-fade absolute inset-0 bg-void/60 backdrop-blur-[1px]" onClick={() => setDetailId(null)} />
          <div className="anim-fade-up relative flex h-full w-full max-w-[420px] flex-col border-l border-line-bright bg-surface shadow-2xl">
            <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <span className="text-[11px] font-medium tracking-[0.1em] text-faint uppercase">
                Task detail
              </span>
              <button
                onClick={() => setDetailId(null)}
                className="rounded p-1 text-faint hover:bg-raised hover:text-ink"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
              <textarea
                value={detail.title}
                onChange={(e) => actions.patchTask(detail.id, { title: e.target.value })}
                rows={2}
                className="w-full resize-none rounded-[7px] border border-transparent bg-transparent px-1 py-1 text-[15px] font-medium text-ink hover:border-line focus:border-accent/40 focus:outline-none"
              />
              <div className="flex flex-wrap gap-1.5">
                {COLUMNS.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => actions.patchTask(detail.id, { status: c.id })}
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[10.5px] transition",
                      detail.status === c.id
                        ? "border-accent/40 bg-accent/10 text-ink"
                        : "border-line text-faint hover:text-muted",
                    )}
                  >
                    {c.label}
                  </button>
                ))}
              </div>

              <Field label="Notes">
                <Textarea
                  rows={4}
                  value={detail.notes}
                  onChange={(e) => actions.patchTask(detail.id, { notes: e.target.value })}
                  placeholder="What has to be true for this to be done?"
                />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Priority">
                  <Select
                    value={detail.priority}
                    onChange={(e) =>
                      actions.patchTask(detail.id, { priority: e.target.value as Priority })
                    }
                  >
                    {["critical", "high", "medium", "low"].map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Project">
                  <Select
                    value={detail.projectId ?? ""}
                    onChange={(e) =>
                      actions.patchTask(detail.id, { projectId: e.target.value || null })
                    }
                  >
                    <option value="">No project</option>
                    {state.projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Due">
                  <Input
                    type="datetime-local"
                    value={detail.due ? new Date(detail.due).toISOString().slice(0, 16) : ""}
                    onChange={(e) =>
                      actions.patchTask(detail.id, {
                        due: e.target.value ? new Date(e.target.value).toISOString() : null,
                      })
                    }
                  />
                </Field>
                <Field label="Estimate (min)">
                  <Input
                    type="number"
                    value={detail.estimateMin ?? ""}
                    onChange={(e) =>
                      actions.patchTask(detail.id, {
                        estimateMin: e.target.value ? Number(e.target.value) : null,
                      })
                    }
                  />
                </Field>
              </div>

              <div className="rounded-[7px] border border-line bg-abyss/50 p-2.5 text-[10.5px] text-faint">
                <div className="flex justify-between">
                  <span>Created</span>
                  <span className="num">{fmtDate(detail.createdAt)}</span>
                </div>
                {detail.completedAt && (
                  <div className="mt-1 flex justify-between">
                    <span>Completed</span>
                    <span className="num">{fmtDate(detail.completedAt)}</span>
                  </div>
                )}
                <div className="mt-1 flex justify-between">
                  <span>Id</span>
                  <span className="font-mono">{detail.id}</span>
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between border-t border-line p-3">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setSection("assistant");
                  setTimeout(
                    () =>
                      window.dispatchEvent(
                        new CustomEvent("orbit:prompt", {
                          detail: { prompt: `Help me plan and execute: ${detail.title}` },
                        }),
                      ),
                    80,
                  );
                }}
              >
                <Sparkles className="h-3 w-3" /> Ask AI
              </Button>
              <div className="flex gap-1.5">
                <Button size="sm" variant="ghost" onClick={() => setDetailId(null)}>
                  Close
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => {
                    actions.removeTask(detail.id);
                    setDetailId(null);
                  }}
                >
                  <Trash2 className="h-3 w-3" /> Delete
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="Create task" width="max-w-lg">
        <div className="space-y-3">
          <Field label="Title">
            <Input
              autoFocus
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="Audit ghost-peer eviction"
              onKeyDown={(e) => e.key === "Enter" && createTask()}
            />
          </Field>
          <Field label="Notes">
            <Textarea
              rows={2}
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              placeholder="Definition of done"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Project">
              <Select
                value={form.projectId}
                onChange={(e) => setForm({ ...form, projectId: e.target.value })}
              >
                <option value="">No project</option>
                {state.projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Priority">
              <Select
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: e.target.value as Priority })}
              >
                {["critical", "high", "medium", "low"].map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Due">
              <Input
                type="datetime-local"
                value={form.due}
                onChange={(e) => setForm({ ...form, due: e.target.value })}
              />
            </Field>
            <Field label="Estimate (min)">
              <Input
                type="number"
                value={form.estimate}
                onChange={(e) => setForm({ ...form, estimate: e.target.value })}
              />
            </Field>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setCreateOpen(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={createTask} disabled={!form.title.trim()}>
            Create
          </Button>
        </div>
      </Modal>
    </div>
  );
}
