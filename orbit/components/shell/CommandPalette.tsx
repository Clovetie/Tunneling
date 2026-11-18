"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  Boxes,
  CalendarDays,
  FolderKanban,
  Library,
  ListChecks,
  Search,
  Sparkles,
  Workflow,
  Zap,
  CornerDownLeft,
} from "lucide-react";
import { useOrbit } from "@/lib/store";
import { cn } from "@/lib/util";
import type { SectionId } from "@/lib/types";

interface Entry {
  id: string;
  label: string;
  hint: string;
  group: string;
  icon: React.ElementType;
  run: () => void;
}

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { state, setSection, select, dispatch } = useOrbit();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ("");
      setCursor(0);
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open]);

  const entries = useMemo<Entry[]>(() => {
    const sections: { id: SectionId; label: string; icon: React.ElementType }[] = [
      { id: "home", label: "Home", icon: Zap },
      { id: "assistant", label: "AI Assistant", icon: Sparkles },
      { id: "projects", label: "Projects", icon: FolderKanban },
      { id: "tasks", label: "Tasks", icon: ListChecks },
      { id: "calendar", label: "Calendar", icon: CalendarDays },
      { id: "knowledge", label: "Knowledge", icon: Library },
      { id: "files", label: "Files", icon: Boxes },
      { id: "automations", label: "Automations", icon: Workflow },
    ];

    const nav: Entry[] = sections.map((s) => ({
      id: `nav-${s.id}`,
      label: `Go to ${s.label}`,
      hint: "Section",
      group: "Navigate",
      icon: s.icon,
      run: () => {
        setSection(s.id);
        onClose();
      },
    }));

    const projectEntries: Entry[] = state.projects.map((p) => ({
      id: `prj-${p.id}`,
      label: p.name,
      hint: `${p.status} · ${p.priority}`,
      group: "Projects",
      icon: FolderKanban,
      run: () => {
        select({ projectId: p.id });
        setSection("projects");
        onClose();
      },
    }));

    const taskEntries: Entry[] = state.tasks
      .filter((t) => t.status !== "done")
      .slice(0, 8)
      .map((t) => ({
        id: `tsk-${t.id}`,
        label: t.title,
        hint: t.status,
        group: "Tasks",
        icon: ListChecks,
        run: () => {
          select({ taskId: t.id });
          setSection("tasks");
          onClose();
        },
      }));

    const noteEntries: Entry[] = state.notes.slice(0, 6).map((n) => ({
      id: `kn-${n.id}`,
      label: n.title,
      hint: n.collection,
      group: "Knowledge",
      icon: Library,
      run: () => {
        select({ noteId: n.id });
        setSection("knowledge");
        onClose();
      },
    }));

    const convEntries: Entry[] = state.conversations
      .filter((c) => !c.archived)
      .slice(0, 6)
      .map((c) => ({
        id: `cnv-${c.id}`,
        label: c.title,
        hint: c.folder,
        group: "Conversations",
        icon: Sparkles,
        run: () => {
          dispatch({ type: "conv.active", id: c.id });
          setSection("assistant");
          onClose();
        },
      }));

    return [...nav, ...projectEntries, ...taskEntries, ...noteEntries, ...convEntries];
  }, [state, setSection, select, dispatch, onClose]);

  const filtered = useMemo(() => {
    if (!q.trim()) return entries.slice(0, 12);
    const needle = q.toLowerCase();
    return entries
      .filter(
        (e) =>
          e.label.toLowerCase().includes(needle) || e.group.toLowerCase().includes(needle),
      )
      .slice(0, 14);
  }, [entries, q]);

  const askEntry: Entry = {
    id: "ask",
    label: q ? `Ask ORBIT: "${q}"` : "Ask ORBIT anything",
    hint: "Assistant",
    group: "Action",
    icon: Sparkles,
    run: () => {
      onClose();
      setSection("assistant");
      if (q)
        setTimeout(
          () => window.dispatchEvent(new CustomEvent("orbit:prompt", { detail: { prompt: q } })),
          60,
        );
    },
  };

  const results = q.trim() ? [askEntry, ...filtered] : filtered;

  useEffect(() => {
    setCursor(0);
  }, [q]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[95] flex items-start justify-center p-4 sm:pt-[12vh]">
      <div className="anim-fade fixed inset-0 bg-void/80 backdrop-blur-[4px]" onClick={onClose} />
      <div className="anim-fade-up relative w-full max-w-[600px] overflow-hidden rounded-xl border border-line-bright bg-surface shadow-[0_40px_120px_-30px_rgba(0,0,0,1)]">
        <div className="flex items-center gap-2.5 border-b border-line px-3.5 py-3">
          <Search className="h-4 w-4 text-faint" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setCursor((c) => Math.min(c + 1, results.length - 1));
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(c - 1, 0));
              }
              if (e.key === "Enter" && results[cursor]) results[cursor].run();
            }}
            placeholder="Search projects, tasks, notes, conversations — or ask ORBIT…"
            className="flex-1 bg-transparent text-[13.5px] text-ink placeholder:text-faint focus:outline-none"
          />
          <span className="rounded border border-line px-1.5 py-0.5 text-[10px] text-faint">esc</span>
        </div>

        <div className="max-h-[52vh] overflow-y-auto py-1.5">
          {results.length === 0 && (
            <div className="px-4 py-8 text-center text-[12.5px] text-faint">
              No matches. Press Enter to ask ORBIT instead.
            </div>
          )}
          {results.map((entry, i) => {
            const Icon = entry.icon;
            const active = i === cursor;
            const showGroup = i === 0 || results[i - 1]?.group !== entry.group;
            return (
              <React.Fragment key={entry.id}>
                {showGroup && (
                  <div className="px-3.5 pt-2 pb-1 text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
                    {entry.group}
                  </div>
                )}
                <button
                  onMouseEnter={() => setCursor(i)}
                  onClick={entry.run}
                  className={cn(
                    "flex w-full items-center gap-2.5 px-3.5 py-2 text-left transition-colors",
                    active ? "bg-accent/10" : "hover:bg-raised/60",
                  )}
                >
                  <Icon className={cn("h-3.5 w-3.5", active ? "text-accent" : "text-faint")} />
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">{entry.label}</span>
                  <span className="shrink-0 text-[10.5px] text-faint">{entry.hint}</span>
                  {active && <CornerDownLeft className="h-3 w-3 shrink-0 text-accent" />}
                </button>
              </React.Fragment>
            );
          })}
        </div>

        <div className="flex items-center justify-between border-t border-line px-3.5 py-2 text-[10.5px] text-faint">
          <span className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <ArrowRight className="h-2.5 w-2.5" /> navigate
            </span>
            <span>↑↓ move</span>
            <span>↵ open</span>
          </span>
          <span>{results.length} results</span>
        </div>
      </div>
    </div>
  );
}
