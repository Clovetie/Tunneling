"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  Boxes,
  CalendarDays,
  FolderKanban,
  Hash,
  Library,
  ListChecks,
  Search,
  Sparkles,
  Workflow,
  X,
} from "lucide-react";
import { useOrbit } from "@/lib/store";
import { cn, fmtDate, relative } from "@/lib/util";
import { parseTerms, scoreText } from "@/lib/search";
import type { SectionId } from "@/lib/types";

type Group = "Projects" | "Tasks" | "Notes" | "Files" | "Automations" | "Events" | "Conversations";

interface Hit {
  id: string;
  group: Group;
  title: string;
  snippet: string;
  meta: string;
  section: SectionId;
  entityId: string | null;
  score: number;
}

const ICONS: Record<Group, React.ElementType> = {
  Projects: FolderKanban,
  Tasks: ListChecks,
  Notes: Library,
  Files: Boxes,
  Automations: Workflow,
  Events: CalendarDays,
  Conversations: Sparkles,
};

const GROUP_SECTION: Record<Group, SectionId> = {
  Projects: "projects",
  Tasks: "tasks",
  Notes: "knowledge",
  Files: "files",
  Automations: "automations",
  Events: "calendar",
  Conversations: "assistant",
};

/**
 * Search runs across every entity type at once. Results are ranked by match
 * quality, then by recency, and each opens in its own tab.
 */
export function SearchOverlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { state, openEntity, setSection, dispatch } = useOrbit();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const [scope, setScope] = useState<Group | "All">("All");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ("");
      setCursor(0);
      setScope("All");
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open]);

  const hits = useMemo<Hit[]>(() => {
    const terms = parseTerms(q);
    if (!terms.length) return [];

    const out: Hit[] = [];

    for (const p of state.projects) {
      const s = scoreText(`${p.name} ${p.description} ${p.objectives.join(" ")} ${p.tags.join(" ")}`, terms);
      if (s)
        out.push({
          id: p.id,
          group: "Projects",
          title: p.name,
          snippet: p.description,
          meta: `${p.status} · ${p.progress}%`,
          section: "projects",
          entityId: p.id,
          score: s,
        });
    }
    for (const t of state.tasks) {
      const s = scoreText(`${t.title} ${t.notes} ${t.tags.join(" ")}`, terms);
      if (s)
        out.push({
          id: t.id,
          group: "Tasks",
          title: t.title,
          snippet: t.notes || "No notes.",
          meta: `${t.status} · ${t.due ? relative(t.due) : "no due date"}`,
          section: "tasks",
          entityId: t.id,
          score: s + (t.status === "done" ? -6 : 2),
        });
    }
    for (const n of state.notes) {
      const s = scoreText(`${n.title} ${n.body} ${n.tags.join(" ")} ${n.collection}`, terms);
      if (s)
        out.push({
          id: n.id,
          group: "Notes",
          title: n.title,
          snippet: n.body,
          meta: `${n.collection} · ${relative(n.updatedAt)}`,
          section: "knowledge",
          entityId: n.id,
          score: s,
        });
    }
    for (const f of state.files) {
      const s = scoreText(`${f.name} ${f.summary} ${f.tags.join(" ")}`, terms);
      if (s)
        out.push({
          id: f.id,
          group: "Files",
          title: f.name,
          snippet: f.summary,
          meta: `${f.kind} · ${f.sizeKb} KB`,
          section: "files",
          entityId: f.id,
          score: s,
        });
    }
    for (const a of state.automations) {
      const s = scoreText(`${a.name} ${a.description} ${a.tags.join(" ")}`, terms);
      if (s)
        out.push({
          id: a.id,
          group: "Automations",
          title: a.name,
          snippet: a.description,
          meta: `${a.enabled ? "enabled" : "disabled"} · ${a.triggerDetail}`,
          section: "automations",
          entityId: a.id,
          score: s,
        });
    }
    for (const e of state.events) {
      const s = scoreText(`${e.title} ${e.location ?? ""}`, terms);
      if (s)
        out.push({
          id: e.id,
          group: "Events",
          title: e.title,
          snippet: `${fmtDate(e.start)} · ${e.kind}`,
          meta: relative(e.start),
          section: "calendar",
          entityId: null,
          score: s,
        });
    }
    for (const c of state.conversations) {
      const body = c.messages.map((m) => m.content).join(" ");
      const s = scoreText(`${c.title} ${body}`, terms);
      if (s)
        out.push({
          id: c.id,
          group: "Conversations",
          title: c.title,
          snippet: body.slice(0, 140) || "Empty conversation.",
          meta: `${c.folder} · ${relative(c.updatedAt)}`,
          section: "assistant",
          entityId: null,
          score: s,
        });
    }

    return out
      .filter((h) => scope === "All" || h.group === scope)
      .sort((a, b) => b.score - a.score)
      .slice(0, 24);
  }, [q, state, scope]);

  const grouped = useMemo(() => {
    const map = new Map<Group, Hit[]>();
    for (const h of hits) {
      if (!map.has(h.group)) map.set(h.group, []);
      map.get(h.group)!.push(h);
    }
    return [...map.entries()];
  }, [hits]);

  const flat = grouped.flatMap(([, items]) => items);

  function openHit(hit: Hit) {
    if (hit.group === "Conversations") {
      dispatch({ type: "conv.active", id: hit.id });
      setSection("assistant");
    } else if (hit.section === "calendar") {
      setSection("calendar");
    } else if (hit.entityId) {
      openEntity(hit.section, hit.entityId, hit.title);
    } else {
      setSection(hit.section);
    }
    onClose();
  }

  if (!open) return null;

  const counts = new Map<Group, number>();
  for (const h of hits) counts.set(h.group, (counts.get(h.group) ?? 0) + 1);

  return (
    <div className="fixed inset-0 z-[96] flex items-start justify-center p-4 sm:pt-[9vh]">
      <div className="anim-fade fixed inset-0 bg-void/82 backdrop-blur-[4px]" onClick={onClose} />
      <div className="anim-fade-up relative flex max-h-[76vh] w-full max-w-[660px] flex-col overflow-hidden rounded-xl border border-line-bright bg-surface shadow-[0_40px_120px_-30px_rgba(0,0,0,1)]">
        <div className="flex items-center gap-2.5 border-b border-line px-3.5 py-3">
          <Search className="h-4 w-4 shrink-0 text-faint" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setCursor(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setCursor((c) => Math.min(c + 1, flat.length - 1));
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(c - 1, 0));
              }
              if (e.key === "Enter" && flat[cursor]) openHit(flat[cursor]);
            }}
            placeholder="Search everything — projects, tasks, notes, files, automations, conversations…"
            className="min-w-0 flex-1 bg-transparent text-[13.5px] text-ink placeholder:text-faint focus:outline-none"
          />
          {q && (
            <button
              onClick={() => setQ("")}
              className="shrink-0 rounded p-0.5 text-faint transition hover:text-ink"
              aria-label="Clear search"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
          <span className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[10px] text-faint">
            esc
          </span>
        </div>

        <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-line px-2.5 py-1.5">
          <button
            onClick={() => setScope("All")}
            className={cn(
              "shrink-0 rounded-full px-2 py-0.5 text-[10.5px] transition",
              scope === "All" ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
            )}
          >
            All {hits.length > 0 && <span className="num">({hits.length})</span>}
          </button>
          {(Object.keys(ICONS) as Group[]).map((g) => (
            <button
              key={g}
              onClick={() => setScope(scope === g ? "All" : g)}
              className={cn(
                "shrink-0 rounded-full px-2 py-0.5 text-[10.5px] transition",
                scope === g ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
              )}
            >
              {g} {counts.has(g) && <span className="num">({counts.get(g)})</span>}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto py-1">
          {!q.trim() && (
            <div className="px-4 py-10 text-center">
              <Hash className="mx-auto mb-2 h-5 w-5 text-faint" />
              <p className="text-[12px] text-muted">
                Type to search across {state.projects.length} projects, {state.tasks.length} tasks,{" "}
                {state.notes.length} notes, {state.files.length} files and{" "}
                {state.conversations.length} conversations.
              </p>
              <p className="mt-1 text-[10.5px] text-faint">
                Every result opens in its own tab. Ranking prefers whole-word and early matches.
              </p>
            </div>
          )}

          {q.trim() && !flat.length && (
            <div className="px-4 py-10 text-center text-[12px] text-faint">
              No matches for “{q}”. ORBIT will not invent a result that is not in your data.
            </div>
          )}

          {grouped.map(([group, items]) => {
            const Icon = ICONS[group];
            return (
              <div key={group}>
                <div className="flex items-center gap-1.5 px-3.5 pt-2 pb-1">
                  <Icon className="h-2.5 w-2.5 text-faint" />
                  <span className="text-[9.5px] font-medium tracking-[0.12em] text-faint uppercase">
                    {group}
                  </span>
                  <span className="num text-[9px] text-faint">{items.length}</span>
                </div>
                {items.map((hit) => {
                  const i = flat.indexOf(hit);
                  const active = i === cursor;
                  return (
                    <button
                      key={`${hit.group}-${hit.id}`}
                      onMouseEnter={() => setCursor(i)}
                      onClick={() => openHit(hit)}
                      className={cn(
                        "flex w-full items-start gap-2.5 px-3.5 py-2 text-left transition-colors",
                        active ? "bg-accent/10" : "hover:bg-raised/60",
                      )}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline gap-2">
                          <span className="truncate text-[12px] text-ink">{hit.title}</span>
                          <span className="ml-auto shrink-0 text-[9.5px] text-faint">{hit.meta}</span>
                        </div>
                        <p className="mt-0.5 line-clamp-1 text-[10.5px] leading-relaxed text-faint">
                          {hit.snippet}
                        </p>
                      </div>
                      {active && (
                        <ArrowUpRight className="mt-[3px] h-3 w-3 shrink-0 text-accent" />
                      )}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>

        <div className="flex shrink-0 items-center justify-between border-t border-line px-3.5 py-1.5 text-[10px] text-faint">
          <span>↑↓ move · ↵ open in tab · esc close</span>
          <span>{flat.length} results</span>
        </div>
      </div>
    </div>
  );
}
