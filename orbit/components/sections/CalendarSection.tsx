"use client";

import React, { useMemo, useState } from "react";
import {
  BellPlus,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Plus,
  Sparkles,
  Check,
} from "lucide-react";
import { useOrbit, useActions } from "@/lib/store";
import {
  atLocalHour,
  cn,
  dayKey,
  fmtDate,
  fmtTime,
  minutesUntil,
  relative,
} from "@/lib/util";
import {
  Badge,
  Button,
  Field,
  Input,
  Modal,
  Select,
  Textarea,
} from "@/components/ui/kit";
import type { CalendarEvent } from "@/lib/types";

const KIND_STYLE: Record<CalendarEvent["kind"], { bar: string; label: string }> = {
  routine: { bar: "bg-line-bright", label: "Routine" },
  focus: { bar: "bg-accent", label: "Focus" },
  deadline: { bar: "bg-danger", label: "Deadline" },
  reminder: { bar: "bg-warn", label: "Reminder" },
  event: { bar: "bg-violet", label: "Event" },
};

export function CalendarSection() {
  const { state, setSection } = useOrbit();
  const actions = useActions();
  const [offset, setOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [reminderOpen, setReminderOpen] = useState(false);
  const [form, setForm] = useState({
    title: "",
    date: new Date().toISOString().slice(0, 10),
    start: "20:45",
    end: "22:15",
    kind: "focus" as CalendarEvent["kind"],
    projectId: "",
  });
  const [remForm, setRemForm] = useState({ text: "", at: "" });

  const days = useMemo(() => {
    const base = new Date();
    base.setDate(base.getDate() + offset * 7);
    const monday = new Date(base);
    const dow = (monday.getDay() + 6) % 7;
    monday.setDate(monday.getDate() - dow);
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(monday);
      d.setDate(monday.getDate() + i);
      return d;
    });
  }, [offset]);

  const byDay = useMemo(() => {
    const map: Record<string, CalendarEvent[]> = {};
    for (const e of state.events) {
      const k = dayKey(e.start);
      (map[k] ||= []).push(e);
    }
    for (const k of Object.keys(map)) map[k].sort((a, b) => a.start.localeCompare(b.start));
    return map;
  }, [state.events]);

  const todayKey = dayKey(new Date().toISOString());

  function createEvent() {
    if (!form.title.trim()) return;
    const start = new Date(`${form.date}T${form.start}:00`);
    const end = new Date(`${form.date}T${form.end}:00`);
    actions.addEvent({
      title: form.title.trim(),
      start: start.toISOString(),
      end: end.toISOString(),
      kind: form.kind,
      projectId: form.projectId || null,
      source: "orbit",
    });
    actions.logActivity({
      kind: "tool",
      title: "calendar.create",
      detail: `Created "${form.title}" on ${form.date} ${form.start}`,
      actor: "You",
      status: "ok",
      meta: { permission: "external", verified: "true" },
    });
    setCreateOpen(false);
    setForm({ ...form, title: "" });
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
        <div className="flex items-center gap-1.5">
          <CalendarDays className="h-3.5 w-3.5 text-accent" />
          <span className="text-[13px] font-semibold text-ink">Calendar</span>
          <Badge tone="neutral">{state.identity.timezone}</Badge>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <div className="flex items-center rounded-[6px] border border-line">
            <button
              onClick={() => setOffset((o) => o - 1)}
              className="px-1.5 py-1 text-faint transition hover:text-ink"
              aria-label="Previous week"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => setOffset(0)}
              className="border-x border-line px-2 py-1 text-[11px] text-muted transition hover:text-ink"
            >
              This week
            </button>
            <button
              onClick={() => setOffset((o) => o + 1)}
              className="px-1.5 py-1 text-faint transition hover:text-ink"
              aria-label="Next week"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
          <Button size="sm" variant="secondary" onClick={() => setReminderOpen(true)}>
            <BellPlus className="h-3 w-3" /> Reminder
          </Button>
          <Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3 w-3" /> Event
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="grid min-w-[840px] grid-cols-7 gap-px bg-line">
          {days.map((d) => {
            const k = dayKey(d.toISOString());
            const events = byDay[k] ?? [];
            const isToday = k === todayKey;
            return (
              <div key={k} className={cn("min-h-[420px] bg-abyss/40 p-1.5", isToday && "bg-accent/[0.04]")}>
                <div className="mb-1.5 flex items-baseline justify-between px-0.5">
                  <span
                    className={cn(
                      "text-[10px] font-medium tracking-wide uppercase",
                      isToday ? "text-accent" : "text-faint",
                    )}
                  >
                    {new Intl.DateTimeFormat("en-GB", { weekday: "short" }).format(d)}
                  </span>
                  <span
                    className={cn(
                      "num text-[12px]",
                      isToday ? "font-semibold text-accent" : "text-muted",
                    )}
                  >
                    {d.getDate()}
                  </span>
                </div>
                <div className="space-y-1">
                  {events.map((e) => {
                    const style = KIND_STYLE[e.kind];
                    const past = minutesUntil(e.end) < 0;
                    return (
                      <div
                        key={e.id}
                        className={cn(
                          "group relative overflow-hidden rounded-[5px] border border-line bg-surface px-1.5 py-1 transition-colors hover:border-line-bright",
                          past && "opacity-45",
                        )}
                      >
                        <div className={cn("absolute inset-y-0 left-0 w-[2px]", style.bar)} />
                        <div className="pl-1">
                          <div className="truncate text-[10.5px] leading-tight text-ink">{e.title}</div>
                          <div className="num text-[9px] text-faint">
                            {fmtTime(e.start)}–{fmtTime(e.end)}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                  {!events.length && (
                    <div className="rounded-[5px] border border-dashed border-line/60 px-1 py-3 text-center text-[9px] text-faint">
                      clear
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="shrink-0 border-t border-line">
        <div className="flex items-center justify-between px-4 py-1.5">
          <div className="flex items-center gap-1.5">
            <Clock3 className="h-3 w-3 text-warn" />
            <span className="text-[11px] font-medium tracking-[0.1em] text-muted uppercase">
              Reminders
            </span>
            <span className="rounded-full bg-overlay px-1.5 py-[1px] text-[10px] text-faint">
              {state.reminders.filter((r) => !r.done).length}
            </span>
          </div>
          <button
            onClick={() => setSection("assistant")}
            className="flex items-center gap-1 text-[10.5px] text-accent transition hover:brightness-125"
          >
            <Sparkles className="h-2.5 w-2.5" /> Ask ORBIT to schedule
          </button>
        </div>
        <div className="flex gap-1.5 overflow-x-auto px-4 pb-2.5">
          {state.reminders.map((r) => (
            <div
              key={r.id}
              className={cn(
                "flex min-w-[180px] shrink-0 items-center gap-1.5 rounded-[6px] border border-line bg-surface px-2 py-1.5",
                r.done && "opacity-45",
              )}
            >
              <button
                onClick={() => actions.patchReminder(r.id, { done: !r.done })}
                className={cn(
                  "flex h-3 w-3 shrink-0 items-center justify-center rounded-full border transition",
                  r.done ? "border-signal bg-signal/20" : "border-line-bright hover:border-signal",
                )}
                aria-label="Toggle reminder"
              >
                {r.done && <Check className="h-2 w-2 text-signal" />}
              </button>
              <div className="min-w-0 flex-1">
                <div className={cn("truncate text-[10.5px]", r.done ? "text-faint line-through" : "text-ink")}>
                  {r.text}
                </div>
                <div className="num text-[9px] text-faint">
                  {fmtDate(r.at)} · {relative(r.at)}
                </div>
              </div>
              <Badge tone="neutral">{r.repeat}</Badge>
            </div>
          ))}
        </div>
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="Create event" width="max-w-lg">
        <div className="space-y-3">
          <Field label="Title">
            <Input
              autoFocus
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="Deep work: modifier matrix"
            />
          </Field>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Date">
              <Input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />
            </Field>
            <Field label="Start">
              <Input type="time" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} />
            </Field>
            <Field label="End">
              <Input type="time" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Kind">
              <Select
                value={form.kind}
                onChange={(e) => setForm({ ...form, kind: e.target.value as CalendarEvent["kind"] })}
              >
                {Object.keys(KIND_STYLE).map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Project">
              <Select
                value={form.projectId}
                onChange={(e) => setForm({ ...form, projectId: e.target.value })}
              >
                <option value="">None</option>
                {state.projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        </div>
        <div className="mt-5 flex items-center justify-between">
          <span className="text-[10.5px] text-faint">
            Written locally. Nothing is sent to Google without the calendar tool.
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={createEvent} disabled={!form.title.trim()}>
              Create
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={reminderOpen} onClose={() => setReminderOpen(false)} title="Add reminder" width="max-w-md">
        <div className="space-y-3">
          <Field label="Reminder">
            <Input
              autoFocus
              value={remForm.text}
              onChange={(e) => setRemForm({ ...remForm, text: e.target.value })}
              placeholder="Rotate bridge token"
            />
          </Field>
          <Field label="When">
            <Input
              type="datetime-local"
              value={remForm.at}
              onChange={(e) => setRemForm({ ...remForm, at: e.target.value })}
            />
          </Field>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setReminderOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              if (!remForm.text.trim()) return;
              actions.addReminder({
                text: remForm.text.trim(),
                at: remForm.at ? new Date(remForm.at).toISOString() : atLocalHour(0, 18),
              });
              setRemForm({ text: "", at: "" });
              setReminderOpen(false);
            }}
          >
            Add
          </Button>
        </div>
      </Modal>
    </div>
  );
}
