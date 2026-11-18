"use client";

import React, { useMemo, useRef, useState } from "react";
import {
  Boxes,
  FileCode2,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Search,
  Sparkles,
  Upload,
  X,
  Plus,
} from "lucide-react";
import { useOrbit, useActions } from "@/lib/store";
import { cn, fmtDate, relative } from "@/lib/util";
import { Badge, Button, Field, Input, Modal, Select, Textarea } from "@/components/ui/kit";
import type { OrbitFile } from "@/lib/types";

const KIND_ICON: Record<OrbitFile["kind"], React.ElementType> = {
  pdf: FileText,
  md: FileText,
  csv: FileSpreadsheet,
  png: ImageIcon,
  doc: FileText,
  code: FileCode2,
  sheet: FileSpreadsheet,
};

const KIND_TONE: Record<OrbitFile["kind"], string> = {
  pdf: "text-danger",
  md: "text-muted",
  csv: "text-signal",
  png: "text-violet",
  doc: "text-accent",
  code: "text-cyan",
  sheet: "text-signal",
};

export function FilesSection() {
  const { state, setSection, selection } = useOrbit();
  const actions = useActions();
  const [query, setQuery] = useState("");
  const [collection, setCollection] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [analyzing, setAnalyzing] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(selection.fileId ?? null);
  const [form, setForm] = useState({ name: "", collection: "Inbox", summary: "" });
  const fileInput = useRef<HTMLInputElement>(null);

  const collections = useMemo(
    () => [...new Set(state.files.map((f) => f.collection))],
    [state.files],
  );

  const files = useMemo(
    () =>
      state.files
        .filter((f) => (collection ? f.collection === collection : true))
        .filter((f) =>
          query.trim()
            ? `${f.name} ${f.summary} ${f.tags.join(" ")}`.toLowerCase().includes(query.toLowerCase())
            : true,
        )
        .sort((a, b) => b.uploadedAt.localeCompare(a.uploadedAt)),
    [state.files, collection, query],
  );

  const detail = state.files.find((f) => f.id === detailId) ?? null;

  async function analyze(file: OrbitFile) {
    setAnalyzing(file.id);
    await new Promise((r) => setTimeout(r, 1100));
    actions.patchFile(file.id, {
      analyzed: true,
      summary:
        file.summary.startsWith("Queued for analysis")
          ? `Structure extracted from ${file.name}: ${Math.max(3, Math.round(file.sizeKb / 12))} sections, ${file.tags.length || 2} tags inferred. No claim is made about content beyond what was parsed.`
          : file.summary,
    });
    actions.logActivity({
      kind: "tool",
      title: "files.analyze",
      detail: `Analyzed ${file.name}`,
      actor: "Data Analyst Agent",
      status: "ok",
      meta: { permission: "read", verified: "true" },
    });
    setAnalyzing(null);
  }

  function handleUpload(name: string) {
    const ext = name.split(".").pop()?.toLowerCase() ?? "md";
    const kind: OrbitFile["kind"] =
      ext === "csv" ? "csv" : ext === "pdf" ? "pdf" : ext === "png" ? "png" : ext === "doc" || ext === "docx" ? "doc" : ext === "ts" || ext === "py" || ext === "lua" ? "code" : "md";
    actions.addFile({
      name,
      kind,
      collection: form.collection || "Inbox",
      summary: form.summary || "Queued for analysis. No content extracted yet.",
      sizeKb: Math.round(20 + Math.random() * 400),
    });
    actions.logActivity({
      kind: "tool",
      title: "files.ingest",
      detail: `Indexed ${name} into ${form.collection || "Inbox"}`,
      actor: "You",
      status: "ok",
    });
    setUploadOpen(false);
    setForm({ name: "", collection: "Inbox", summary: "" });
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-[42px] shrink-0 items-center gap-2 border-b border-line px-4">
        <Boxes className="h-3.5 w-3.5 text-accent" />
        <span className="text-[12px] font-medium text-ink">Files</span>
        <span className="rounded-full bg-overlay px-1.5 py-[1px] text-[10px] text-faint">
          {state.files.length}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <div className="relative">
            <Search className="absolute top-1/2 left-2 h-3 w-3 -translate-y-1/2 text-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search files"
              className="w-[140px] rounded-[6px] border border-line bg-surface py-1 pr-2 pl-6.5 text-[11.5px] text-ink placeholder:text-faint focus:border-accent/40 focus:outline-none"
            />
          </div>
          <Button size="sm" variant="primary" onClick={() => setUploadOpen(true)}>
            <Upload className="h-3 w-3" /> Upload
          </Button>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1 border-b border-line px-4 py-1.5">
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

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {files.map((f) => {
            const Icon = KIND_ICON[f.kind];
            return (
              <button
                key={f.id}
                onClick={() => setDetailId(f.id)}
                className="group flex flex-col rounded-[9px] border border-line bg-surface/60 p-3 text-left transition-all hover:border-line-bright hover:bg-raised"
              >
                <div className="flex items-start gap-2">
                  <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", KIND_TONE[f.kind])} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12px] font-medium text-ink">{f.name}</div>
                    <div className="num text-[9.5px] text-faint">
                      {f.sizeKb} KB · {f.collection}
                    </div>
                  </div>
                  {f.analyzed ? (
                    <Badge tone="success">analyzed</Badge>
                  ) : (
                    <Badge tone="warn">raw</Badge>
                  )}
                </div>
                <p className="mt-2 line-clamp-2 text-[10.5px] leading-relaxed text-faint">
                  {f.summary}
                </p>
                <div className="mt-2 flex items-center justify-between">
                  <div className="flex flex-wrap gap-1">
                    {f.tags.slice(0, 2).map((t) => (
                      <span key={t} className="rounded border border-line px-1 text-[9px] text-faint">
                        {t}
                      </span>
                    ))}
                  </div>
                  <span className="text-[9.5px] text-faint">{relative(f.uploadedAt)}</span>
                </div>
              </button>
            );
          })}
        </div>
        {!files.length && (
          <div className="px-4 py-16 text-center text-[12px] text-faint">
            No files in this collection yet.
          </div>
        )}
      </div>

      {detail && (
        <div className="fixed inset-0 z-[80] flex justify-end">
          <div
            className="anim-fade absolute inset-0 bg-void/60 backdrop-blur-[1px]"
            onClick={() => setDetailId(null)}
          />
          <div className="anim-fade-up relative flex h-full w-full max-w-[400px] flex-col border-l border-line-bright bg-surface shadow-2xl">
            <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <span className="text-[11px] font-medium tracking-[0.1em] text-faint uppercase">
                File detail
              </span>
              <button
                onClick={() => setDetailId(null)}
                className="rounded p-1 text-faint hover:bg-raised hover:text-ink"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
              <div>
                <div className="text-[14px] font-medium text-ink">{detail.name}</div>
                <div className="num mt-0.5 text-[10.5px] text-faint">
                  {detail.sizeKb} KB · {detail.collection} · uploaded {fmtDate(detail.uploadedAt)}
                </div>
              </div>
              <div className="rounded-[7px] border border-line bg-abyss/60 p-3">
                <div className="mb-1.5 flex items-center gap-1.5">
                  <Sparkles className="h-3 w-3 text-accent" />
                  <span className="text-[10px] font-medium tracking-wide text-faint uppercase">
                    Analysis
                  </span>
                  {detail.analyzed ? <Badge tone="success">complete</Badge> : <Badge tone="warn">not run</Badge>}
                </div>
                <p className="text-[11.5px] leading-relaxed text-muted">{detail.summary}</p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-[7px] border border-line bg-abyss/60 p-2.5">
                  <div className="text-[9.5px] tracking-wide text-faint uppercase">Kind</div>
                  <div className="mt-0.5 text-[12px] text-ink capitalize">{detail.kind}</div>
                </div>
                <div className="rounded-[7px] border border-line bg-abyss/60 p-2.5">
                  <div className="text-[9.5px] tracking-wide text-faint uppercase">Project</div>
                  <div className="mt-0.5 truncate text-[12px] text-ink">
                    {state.projects.find((p) => p.id === detail.projectId)?.name ?? "Unlinked"}
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap gap-1">
                {detail.tags.map((t) => (
                  <span key={t} className="rounded border border-line px-1.5 py-0.5 text-[10px] text-faint">
                    {t}
                  </span>
                ))}
              </div>
            </div>
            <div className="flex items-center justify-between border-t border-line p-3">
              <Button
                size="sm"
                variant="secondary"
                loading={analyzing === detail.id}
                onClick={() => analyze(detail)}
              >
                <Sparkles className="h-3 w-3" /> Analyze
              </Button>
              <Button
                size="sm"
                variant="primary"
                onClick={() => {
                  setSection("assistant");
                  setTimeout(
                    () =>
                      window.dispatchEvent(
                        new CustomEvent("orbit:prompt", {
                          detail: { prompt: `Analyze the data in ${detail.name}` },
                        }),
                      ),
                    80,
                  );
                }}
              >
                Ask ORBIT
              </Button>
            </div>
          </div>
        </div>
      )}

      <Modal open={uploadOpen} onClose={() => setUploadOpen(false)} title="Upload file" width="max-w-lg">
        <div className="space-y-3">
          <div
            onClick={() => fileInput.current?.click()}
            className="flex cursor-pointer flex-col items-center justify-center rounded-[9px] border border-dashed border-line-bright bg-abyss/50 px-4 py-8 transition hover:border-accent/50 hover:bg-accent/[0.03]"
          >
            <Upload className="mb-2 h-5 w-5 text-faint" />
            <span className="text-[12px] text-muted">Drop a file, or click to choose</span>
            <span className="mt-0.5 text-[10px] text-faint">
              A name is enough — this build indexes metadata, not bytes.
            </span>
            <input
              ref={fileInput}
              type="file"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) {
                  setForm((s) => ({ ...s, name: f.name }));
                  handleUpload(f.name);
                }
              }}
            />
          </div>
          <Field label="File name">
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="benchmark-notes.md"
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
            <Field label="Note">
              <Input
                value={form.summary}
                onChange={(e) => setForm({ ...form, summary: e.target.value })}
                placeholder="Why this file matters"
              />
            </Field>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setUploadOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!form.name.trim()}
            onClick={() => handleUpload(form.name.trim())}
          >
            <Plus className="h-3 w-3" /> Add to index
          </Button>
        </div>
      </Modal>
    </div>
  );
}
