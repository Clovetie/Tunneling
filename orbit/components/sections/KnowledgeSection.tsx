"use client";

import React, { useMemo, useState } from "react";
import {
  BookOpen,
  ChevronRight,
  FolderOpen,
  Library,
  Pin,
  Plus,
  Search,
  Sparkles,
  Tag,
} from "lucide-react";
import { useOrbit, useActions } from "@/lib/store";
import { cn, fmtDate, relative } from "@/lib/util";
import { Badge, Button, Field, Input, Modal, Select, Textarea } from "@/components/ui/kit";

export function KnowledgeSection() {
  const { state, selection, select, setSection } = useOrbit();
  const actions = useActions();
  const [query, setQuery] = useState("");
  const [collection, setCollection] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ title: "", body: "", collection: "Inbox", tags: "" });

  const collections = useMemo(
    () => [...new Set(state.notes.map((n) => n.collection))],
    [state.notes],
  );

  const notes = useMemo(() => {
    return state.notes
      .filter((n) => (collection ? n.collection === collection : true))
      .filter((n) =>
        query.trim()
          ? `${n.title} ${n.body} ${n.tags.join(" ")}`.toLowerCase().includes(query.toLowerCase())
          : true,
      )
      .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updatedAt.localeCompare(a.updatedAt));
  }, [state.notes, collection, query]);

  const selected = state.notes.find((n) => n.id === selection.noteId) ?? notes[0] ?? null;

  return (
    <div className="flex h-full min-h-0">
      <div
        className={cn(
          "w-full min-w-0 flex-col border-r border-line md:flex md:w-[300px] md:shrink-0",
          selection.noteId ? "hidden" : "flex",
        )}
      >
        <div className="flex h-[42px] items-center justify-between border-b border-line px-3.5">
          <div className="flex items-center gap-1.5">
            <Library className="h-3.5 w-3.5 text-accent" />
            <span className="text-[12px] font-medium text-ink">Knowledge</span>
            <span className="rounded-full bg-overlay px-1.5 py-[1px] text-[10px] text-faint">
              {state.notes.length}
            </span>
          </div>
          <Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3 w-3" /> Note
          </Button>
        </div>

        <div className="space-y-1.5 border-b border-line px-2.5 py-2">
          <div className="relative">
            <Search className="absolute top-1/2 left-2 h-3 w-3 -translate-y-1/2 text-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search the vault"
              className="w-full rounded-[6px] border border-line bg-surface py-1 pr-2 pl-6.5 text-[11.5px] text-ink placeholder:text-faint focus:border-accent/40 focus:outline-none"
            />
          </div>
          <div className="flex flex-wrap gap-1">
            <button
              onClick={() => setCollection(null)}
              className={cn(
                "rounded-full px-2 py-0.5 text-[10px] transition",
                !collection ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
              )}
            >
              All
            </button>
            {collections.map((c) => (
              <button
                key={c}
                onClick={() => setCollection(c === collection ? null : c)}
                className={cn(
                  "rounded-full px-2 py-0.5 text-[10px] transition",
                  collection === c ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
                )}
              >
                {c}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {notes.map((n) => (
            <button
              key={n.id}
              onClick={() => select({ noteId: n.id })}
              className={cn(
                "mb-1 block w-full rounded-[7px] border px-2.5 py-2 text-left transition",
                selected?.id === n.id
                  ? "border-accent/35 bg-accent/[0.07]"
                  : "border-transparent hover:border-line hover:bg-raised/50",
              )}
            >
              <div className="flex items-center gap-1.5">
                <BookOpen className="h-2.5 w-2.5 shrink-0 text-faint" />
                <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
                  {n.title}
                </span>
                {n.pinned && <Pin className="h-2.5 w-2.5 shrink-0 text-accent" />}
              </div>
              <p className="mt-0.5 line-clamp-2 pl-4 text-[10.5px] leading-snug text-faint">
                {n.body}
              </p>
              <div className="mt-1 flex items-center gap-1.5 pl-4 text-[9.5px] text-faint">
                <span className="rounded border border-line px-1">{n.collection}</span>
                <span>{relative(n.updatedAt)}</span>
              </div>
            </button>
          ))}
          {!notes.length && (
            <div className="px-3 py-8 text-center text-[11.5px] text-faint">
              No notes match. Try a different term.
            </div>
          )}
        </div>
      </div>

      <div
        className={cn(
          "min-w-0 flex-1 overflow-y-auto md:block",
          selection.noteId ? "block" : "hidden",
        )}
      >
        {selected ? (
          <div className="mx-auto max-w-3xl px-6 py-5">
            <button
              onClick={() => select({ noteId: null })}
              className="mb-2 flex items-center gap-1 text-[11px] text-faint transition hover:text-ink md:hidden"
            >
              <ChevronRight className="h-3 w-3 rotate-180" /> All notes
            </button>
            <div className="flex items-center gap-2 text-[10px] text-faint">
              <FolderOpen className="h-2.5 w-2.5" />
              <span>{selected.collection}</span>
              <span className="h-1 w-1 rounded-full bg-line-bright" />
              <span>updated {relative(selected.updatedAt)}</span>
              <span className="h-1 w-1 rounded-full bg-line-bright" />
              <span className="font-mono">{selected.id}</span>
            </div>
            <h1 className="mt-1.5 text-[21px] leading-tight font-semibold tracking-[-0.025em] text-ink">
              {selected.title}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {selected.tags.map((t) => (
                <span
                  key={t}
                  className="flex items-center gap-1 rounded-full border border-line bg-raised px-1.5 py-[1px] text-[9.5px] text-muted"
                >
                  <Tag className="h-2 w-2" /> {t}
                </span>
              ))}
              {selected.linkedProjectId && (
                <button
                  onClick={() => {
                    select({ projectId: selected.linkedProjectId });
                    setSection("projects");
                  }}
                  className="rounded-full border border-accent/25 bg-accent/10 px-1.5 py-[1px] text-[9.5px] text-accent"
                >
                  {state.projects.find((p) => p.id === selected.linkedProjectId)?.name}
                </button>
              )}
              <button
                onClick={() => {
                  setSection("assistant");
                  setTimeout(
                    () =>
                      window.dispatchEvent(
                        new CustomEvent("orbit:prompt", {
                          detail: { prompt: `Find related notes and context for: ${selected.title}` },
                        }),
                      ),
                    80,
                  );
                }}
                className="ml-auto flex items-center gap-1 text-[10px] text-faint transition hover:text-ink"
              >
                <Sparkles className="h-2.5 w-2.5" />
                Ask ORBIT about this
              </button>
            </div>

            <div className="mt-4 rounded-[10px] border border-line bg-surface p-4">
              <div className="space-y-2.5">
                {selected.body.split("\n\n").map((para, i) => (
                  <p key={i} className="text-[13px] leading-[1.8] text-muted">
                    {para}
                  </p>
                ))}
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-1.5">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setSection("assistant");
                  setTimeout(
                    () =>
                      window.dispatchEvent(
                        new CustomEvent("orbit:prompt", {
                          detail: { prompt: `Find the document where I discussed ${selected.title}` },
                        }),
                      ),
                    80,
                  );
                }}
              >
                <Search className="h-3 w-3" /> Related notes
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSection("files")}>
                <FolderOpen className="h-3 w-3" /> Files in this collection
              </Button>
            </div>

            <div className="mt-5 rounded-[8px] border border-line bg-abyss/50 px-3 py-2 text-[10.5px] text-faint">
              Notes are local. The Knowledge Agent quotes them rather than paraphrasing when
              precision matters, and always cites the note id.
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-[12.5px] text-faint">
            Select a note.
          </div>
        )}
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="New note" width="max-w-xl">
        <div className="space-y-3">
          <Field label="Title">
            <Input
              autoFocus
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="What did you learn that you would have to re-derive?"
            />
          </Field>
          <Field label="Body">
            <Textarea
              rows={7}
              value={form.body}
              onChange={(e) => setForm({ ...form, body: e.target.value })}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Collection">
              <Select
                value={form.collection}
                onChange={(e) => setForm({ ...form, collection: e.target.value })}
              >
                {["Inbox", ...collections].map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Tags" hint="comma separated">
              <Input
                value={form.tags}
                onChange={(e) => setForm({ ...form, tags: e.target.value })}
                placeholder="infra, security"
              />
            </Field>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setCreateOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!form.title.trim() || !form.body.trim()}
            onClick={() => {
              const n = actions.addNote({
                title: form.title.trim(),
                body: form.body.trim(),
                collection: form.collection,
                tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
              });
              select({ noteId: n.id });
              setForm({ title: "", body: "", collection: "Inbox", tags: "" });
              setCreateOpen(false);
            }}
          >
            Save note
          </Button>
        </div>
      </Modal>
    </div>
  );
}
