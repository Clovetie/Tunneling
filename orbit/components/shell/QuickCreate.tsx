"use client";

import React, { useEffect, useState } from "react";
import { useOrbit, useActions } from "@/lib/store";
import { Button, Field, Input, Modal, Select, Textarea } from "@/components/ui/kit";
import { Sparkles, ListChecks, FolderKanban, CalendarPlus, BellPlus, Upload } from "lucide-react";
import { atLocalHour } from "@/lib/util";

type Kind = "task" | "project" | "event" | "reminder" | "file" | "ask";

export function QuickCreate({
  open,
  onClose,
  initialKind,
}: {
  open: boolean;
  onClose: () => void;
  initialKind?: Kind;
}) {
  const { state, setSection, select } = useOrbit();
  const actions = useActions();
  const [kind, setKind] = useState<Kind>("task");
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const [projectId, setProjectId] = useState<string>("");
  const [when, setWhen] = useState<string>("");

  useEffect(() => {
    if (open) {
      setKind(initialKind ?? "task");
      setTitle("");
      setDetail("");
      setProjectId("");
      setWhen("");
    }
  }, [open, initialKind]);

  const kinds: { id: Kind; label: string; icon: React.ElementType }[] = [
    { id: "task", label: "Task", icon: ListChecks },
    { id: "project", label: "Project", icon: FolderKanban },
    { id: "event", label: "Event", icon: CalendarPlus },
    { id: "reminder", label: "Reminder", icon: BellPlus },
    { id: "file", label: "File", icon: Upload },
    { id: "ask", label: "Ask AI", icon: Sparkles },
  ];

  function submit() {
    if (!title.trim()) return;
    if (kind === "task") {
      actions.addTask({
        title: title.trim(),
        notes: detail.trim(),
        projectId: projectId || null,
        due: when ? new Date(when).toISOString() : null,
      });
      actions.logActivity({
        kind: "system",
        title: "Task created from quick create",
        detail: title.trim(),
        actor: "You",
        status: "ok",
      });
      setSection("tasks");
    } else if (kind === "project") {
      const p = actions.addProject({
        name: title.trim(),
        description: detail.trim(),
        objectives: detail.trim() ? [detail.trim()] : [],
        status: "planning",
      });
      select({ projectId: p.id });
      setSection("projects");
    } else if (kind === "event") {
      const start = when ? new Date(when) : new Date(Date.now() + 3_600_000);
      actions.addEvent({
        title: title.trim(),
        start: start.toISOString(),
        end: new Date(start.getTime() + 45 * 60_000).toISOString(),
        kind: "event",
      });
      setSection("calendar");
    } else if (kind === "reminder") {
      actions.addReminder({
        text: title.trim(),
        at: when ? new Date(when).toISOString() : atLocalHour(0, 18),
      });
      setSection("calendar");
    } else if (kind === "file") {
      actions.addFile({
        name: title.trim().includes(".") ? title.trim() : `${title.trim()}.md`,
        collection: "Inbox",
        summary: detail.trim() || "Queued for analysis.",
      });
      setSection("files");
    } else {
      setSection("assistant");
      window.dispatchEvent(
        new CustomEvent("orbit:prompt", { detail: { prompt: `${title.trim()}${detail ? ` — ${detail}` : ""}` } }),
      );
    }
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Quick create"
      description="One entry point for everything ORBIT can hold."
      width="max-w-xl"
    >
      <div className="mb-4 grid grid-cols-3 gap-1.5 sm:grid-cols-6">
        {kinds.map((k) => {
          const Icon = k.icon;
          const active = kind === k.id;
          return (
            <button
              key={k.id}
              onClick={() => setKind(k.id)}
              className={
                "flex flex-col items-center gap-1 rounded-[7px] border px-1 py-2 text-[11px] transition-colors " +
                (active
                  ? "border-accent/40 bg-accent/10 text-ink"
                  : "border-line text-muted hover:border-line-bright hover:text-ink")
              }
            >
              <Icon className={"h-3.5 w-3.5 " + (active ? "text-accent" : "text-faint")} />
              {k.label}
            </button>
          );
        })}
      </div>

      <div className="space-y-3">
        <Field label={kind === "ask" ? "What should ORBIT do?" : "Title"}>
          <Input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) submit();
            }}
            placeholder={
              kind === "task"
                ? "Audit ghost-peer eviction"
                : kind === "project"
                  ? "NightLoop"
                  : kind === "event"
                    ? "Deep work block"
                    : kind === "reminder"
                      ? "Rotate bridge token"
                      : kind === "file"
                        ? "benchmark-notes.md"
                        : "Summarise my unfinished tasks"
            }
          />
        </Field>

        {(kind === "task" || kind === "project" || kind === "ask" || kind === "file") && (
          <Field label={kind === "project" ? "Description / objectives" : "Detail"} hint="optional">
            <Textarea
              rows={2}
              value={detail}
              onChange={(e) => setDetail(e.target.value)}
              placeholder={
                kind === "project"
                  ? "Prove modifiers compose without collapsing the economy"
                  : "Context ORBIT should carry"
              }
            />
          </Field>
        )}

        {kind === "task" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Project">
              <Select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                <option value="">No project</option>
                {state.projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Due">
              <Input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
            </Field>
          </div>
        )}

        {(kind === "event" || kind === "reminder") && (
          <Field label="When">
            <Input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
          </Field>
        )}
      </div>

      <div className="mt-5 flex items-center justify-between">
        <span className="text-[11px] text-faint">
          {kind === "ask"
            ? "Opens the assistant with your prompt"
            : "Saved locally and available to every agent"}
        </span>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={!title.trim()}>
            Create
          </Button>
        </div>
      </div>
    </Modal>
  );
}
