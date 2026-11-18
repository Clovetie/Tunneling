"use client";

import React from "react";
import { Check, ExternalLink, FileText, Sparkles } from "lucide-react";
import { cn } from "@/lib/util";
import type { Message, StructuredBlock } from "@/lib/types";
import { Badge } from "@/components/ui/kit";

/** Tiny inline formatter: bold, code, block quote, ordered/unordered lists, headings. */
function Inline({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  const regex = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = regex.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const token = m[0];
    if (token.startsWith("**")) {
      parts.push(
        <strong key={key++} className="font-semibold text-ink">
          {token.slice(2, -2)}
        </strong>,
      );
    } else {
      parts.push(
        <code
          key={key++}
          className="rounded border border-line bg-abyss px-1 py-[1px] font-mono text-[11.5px] text-cyan"
        >
          {token.slice(1, -1)}
        </code>,
      );
    }
    last = m.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

function Paragraph({ text }: { text: string }) {
  return (
    <p className="text-[13px] leading-[1.72] text-muted">
      <Inline text={text} />
    </p>
  );
}

export function MessageBody({
  content,
  structured,
  sources,
  streaming,
}: {
  content: string;
  structured?: Message["structured"];
  sources?: Message["sources"];
  streaming?: boolean;
}) {
  const lines = content.split("\n");
  const blocks: React.ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let quote: string[] = [];

  const flushList = () => {
    if (!list) return;
    const items = list.items;
    blocks.push(
      <ul key={`l-${blocks.length}`} className="my-1 space-y-1 pl-1">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 text-[13px] leading-[1.65] text-muted">
            <span
              className={cn(
                "mt-[7px] h-1 w-1 shrink-0 rounded-full",
                list!.ordered ? "bg-accent" : "bg-faint",
              )}
            />
            <span>
              <Inline text={item} />
            </span>
          </li>
        ))}
      </ul>,
    );
    list = null;
  };

  const flushQuote = () => {
    if (!quote.length) return;
    blocks.push(
      <blockquote
        key={`q-${blocks.length}`}
        className="my-2 border-l-2 border-accent/40 bg-accent/[0.04] py-1.5 pl-3 text-[12.5px] leading-relaxed text-muted italic"
      >
        <Inline text={quote.join(" ")} />
      </blockquote>,
    );
    quote = [];
  };

  lines.forEach((raw, idx) => {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushList();
      flushQuote();
      return;
    }
    if (line.startsWith("> ")) {
      flushList();
      quote.push(line.slice(2));
      return;
    }
    flushQuote();
    const ordered = /^\d+\.\s/.test(line);
    const bullet = /^[-*]\s/.test(line);
    if (ordered || bullet) {
      const text = line.replace(/^(\d+\.|[-*])\s/, "");
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push(text);
      return;
    }
    flushList();
    if (line.startsWith("### ")) {
      blocks.push(
        <h4 key={idx} className="mt-3 mb-1 text-[12px] font-semibold tracking-wide text-ink uppercase">
          {line.slice(4)}
        </h4>,
      );
    } else if (line.startsWith("## ")) {
      blocks.push(
        <h3 key={idx} className="mt-3 mb-1 text-[13px] font-semibold text-ink">
          {line.slice(3)}
        </h3>,
      );
    } else if (line.startsWith("# ")) {
      blocks.push(
        <h2 key={idx} className="mt-3 mb-1 text-[15px] font-semibold text-ink">
          {line.slice(2)}
        </h2>,
      );
    } else {
      blocks.push(<Paragraph key={idx} text={line} />);
    }
  });
  flushList();
  flushQuote();

  return (
    <div className="space-y-1.5">
      {structured?.map((block, i) => (
        <StructuredView key={i} block={block} />
      ))}

      <div className="space-y-1.5">
        {blocks}
        {streaming && (
          <span className="anim-caret ml-0.5 inline-block h-[13px] w-[2px] translate-y-[2px] bg-accent" />
        )}
      </div>

      {sources && sources.length > 0 && (
        <div className="mt-3 rounded-[8px] border border-line bg-abyss/60 p-2.5">
          <div className="mb-1.5 flex items-center gap-1.5">
            <FileText className="h-3 w-3 text-faint" />
            <span className="text-[10px] font-medium tracking-[0.1em] text-faint uppercase">
              Sources ({sources.length})
            </span>
          </div>
          <div className="space-y-1">
            {sources.map((s, i) => (
              <div key={i} className="flex items-start gap-2">
                <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-accent/60" />
                <div className="min-w-0">
                  <div className="flex items-center gap-1 text-[11.5px] text-ink">
                    <span className="truncate">{s.title}</span>
                    <ExternalLink className="h-2.5 w-2.5 shrink-0 text-faint" />
                  </div>
                  <div className="truncate font-mono text-[9.5px] text-faint">{s.url}</div>
                  {s.note && <div className="text-[9.5px] text-faint">{s.note}</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StructuredView({ block }: { block: StructuredBlock }) {
  if (block.kind === "table") {
    return (
      <div className="my-2 overflow-hidden rounded-[8px] border border-line">
        <div className="flex items-center gap-1.5 border-b border-line bg-raised/60 px-2.5 py-1.5">
          <Sparkles className="h-3 w-3 text-accent" />
          <span className="text-[11px] font-medium text-ink">{block.title}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-line bg-abyss/50">
                {block.headers?.map((h) => (
                  <th
                    key={h}
                    className="px-2.5 py-1.5 text-[10px] font-medium tracking-wide text-faint uppercase"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows?.map((row, i) => (
                <tr key={i} className="border-b border-line/50 last:border-0 hover:bg-raised/30">
                  {row.map((cell, j) => (
                    <td
                      key={j}
                      className={cn(
                        "px-2.5 py-1.5 text-[11.5px]",
                        j === 0 ? "text-ink" : "text-muted",
                      )}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (block.kind === "checklist") {
    return (
      <div className="my-2 rounded-[8px] border border-line bg-abyss/50 p-2.5">
        <div className="mb-1.5 text-[10px] font-medium tracking-[0.1em] text-faint uppercase">
          {block.title}
        </div>
        <div className="space-y-1">
          {block.items?.map((it, i) => (
            <div key={i} className="flex items-center gap-2">
              <Check
                className={cn("h-3 w-3 shrink-0", it.done ? "text-signal" : "text-faint")}
              />
              <span className="min-w-0 flex-1 text-[11.5px] text-muted">{it.label}</span>
              {it.value && <span className="num text-[10.5px] text-faint">{it.value}</span>}
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (block.kind === "findings") {
    return (
      <div className="my-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {block.items?.map((it, i) => (
          <div key={i} className="rounded-[7px] border border-line bg-abyss/50 px-2.5 py-2">
            <div className="num text-[15px] leading-none font-semibold text-ink">{it.value}</div>
            <div className="mt-1 text-[9.5px] tracking-wide text-faint uppercase">{it.label}</div>
          </div>
        ))}
      </div>
    );
  }

  if (block.kind === "plan") {
    return (
      <div className="my-2 overflow-hidden rounded-[8px] border border-line">
        <div className="border-b border-line bg-raised/60 px-2.5 py-1.5 text-[11px] font-medium text-ink">
          {block.title}
        </div>
        <div className="divide-y divide-line/60">
          {block.items?.map((it, i) => (
            <div key={i} className="flex items-center justify-between gap-3 px-2.5 py-1.5">
              <span className="text-[11.5px] text-muted">{it.label}</span>
              <Badge tone="neutral">{it.value}</Badge>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (block.kind === "chart") {
    const max = Math.max(...(block.series ?? []).map((s) => s.value), 1);
    return (
      <div className="my-2 rounded-[8px] border border-line bg-abyss/50 p-2.5">
        <div className="mb-2 text-[10px] font-medium tracking-[0.1em] text-faint uppercase">
          {block.title}
        </div>
        <div className="flex h-[92px] items-end gap-2">
          {block.series?.map((s, i) => (
            <div key={i} className="flex min-w-0 flex-1 flex-col items-center gap-1">
              <div className="num text-[10px] text-muted">{s.value}</div>
              <div
                className="w-full rounded-t-[2px] bg-accent/70 transition-all duration-500"
                style={{ height: `${(s.value / max) * 62}px` }}
              />
              <div className="w-full truncate text-center text-[9px] text-faint">{s.label}</div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (block.kind === "code") {
    return (
      <pre className="my-2 overflow-x-auto rounded-[8px] border border-line bg-void p-3 font-mono text-[11px] leading-relaxed text-muted">
        <code>{block.code?.body}</code>
      </pre>
    );
  }

  return null;
}
