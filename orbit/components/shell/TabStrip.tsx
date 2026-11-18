"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  Layers,
  Pin,
  Plus,
  RotateCcw,
  X,
  Check,
  Trash2,
} from "lucide-react";
import { useOrbit } from "@/lib/store";
import { cn } from "@/lib/util";
import { SECTION_LABEL } from "@/lib/tabs";
import { Badge, HoverTip } from "@/components/ui/kit";

export function TabStrip() {
  const {
    tabs,
    activeTabId,
    dispatch,
    workspace,
    state,
    reopenTab,
    closeTab,
    setSection,
  } = useOrbit();
  const [wsOpen, setWsOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  const closed = state.closedTabs.length;

  // Keep the active tab in view when it changes from anywhere in the app.
  useEffect(() => {
    const el = scroller.current?.querySelector<HTMLElement>('[data-active="true"]');
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeTabId]);

  // Alt+Shift+T is handled here rather than in the shell so the shortcut and
  // the strip's own reopen button share one code path.
  useEffect(() => {
    const onReopen = () => reopenTab();
    window.addEventListener("orbit:tab-reopen", onReopen);
    return () => window.removeEventListener("orbit:tab-reopen", onReopen);
  }, [reopenTab]);

  return (
    <div className="flex h-[34px] shrink-0 items-stretch border-b border-line bg-void/60">
      {/* workspace switcher */}
      <div className="relative flex shrink-0 items-center border-r border-line">
        <button
          onClick={() => setWsOpen((v) => !v)}
          className="flex h-full items-center gap-1.5 px-2.5 text-[11px] text-muted transition hover:bg-raised/70 hover:text-ink"
        >
          <Layers className="h-3 w-3 text-faint" />
          <span className="max-w-[92px] truncate">{workspace?.name ?? "Main"}</span>
          <ChevronDown className="h-2.5 w-2.5 text-faint" />
        </button>

        {wsOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setWsOpen(false)} />
            <div className="anim-fade-up absolute top-[calc(100%+1px)] left-0 z-50 w-[224px] overflow-hidden rounded-[9px] border border-line-bright bg-surface shadow-2xl">
              <div className="border-b border-line px-2.5 py-1.5 text-[9.5px] font-medium tracking-[0.1em] text-faint uppercase">
                Workspaces
              </div>
              {state.workspaces.map((w) => (
                <div
                  key={w.id}
                  className={cn(
                    "group flex items-center gap-1.5 px-2 py-1.5 transition-colors",
                    w.id === state.activeWorkspaceId ? "bg-accent/10" : "hover:bg-raised/70",
                  )}
                >
                  {renaming === w.id ? (
                    <input
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={() => {
                        if (draft.trim())
                          dispatch({ type: "workspace.rename", id: w.id, name: draft.trim() });
                        setRenaming(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") e.currentTarget.blur();
                        if (e.key === "Escape") setRenaming(null);
                      }}
                      className="min-w-0 flex-1 rounded border border-accent/40 bg-abyss px-1 py-0.5 text-[11.5px] text-ink focus:outline-none"
                    />
                  ) : (
                    <button
                      onClick={() => {
                        dispatch({ type: "workspace.switch", id: w.id });
                        setWsOpen(false);
                      }}
                      onDoubleClick={() => {
                        setRenaming(w.id);
                        setDraft(w.name);
                      }}
                      className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                    >
                      {w.id === state.activeWorkspaceId ? (
                        <Check className="h-2.5 w-2.5 shrink-0 text-accent" />
                      ) : (
                        <span className="h-2.5 w-2.5 shrink-0" />
                      )}
                      <span className="truncate text-[11.5px] text-ink">{w.name}</span>
                      <span className="num ml-auto shrink-0 text-[9px] text-faint">
                        {w.tabs.length}
                      </span>
                    </button>
                  )}
                  {state.workspaces.length > 1 && renaming !== w.id && (
                    <button
                      onClick={() => dispatch({ type: "workspace.remove", id: w.id })}
                      className="shrink-0 rounded p-0.5 text-faint opacity-0 transition hover:text-danger group-hover:opacity-100"
                      aria-label={`Delete workspace ${w.name}`}
                    >
                      <Trash2 className="h-2.5 w-2.5" />
                    </button>
                  )}
                </div>
              ))}
              <button
                onClick={() => {
                  dispatch({
                    type: "workspace.add",
                    name: `Workspace ${state.workspaces.length + 1}`,
                  });
                  setWsOpen(false);
                }}
                className="flex w-full items-center gap-1.5 border-t border-line px-2 py-1.5 text-[11px] text-muted transition hover:bg-raised/70 hover:text-ink"
              >
                <Plus className="h-2.5 w-2.5" /> New workspace
              </button>
              <div className="border-t border-line px-2.5 py-1.5 text-[9.5px] leading-relaxed text-faint">
                Each workspace keeps its own tabs and selection. Double-click a name to rename.
              </div>
            </div>
          </>
        )}
      </div>

      {/* tabs */}
      <div
        ref={scroller}
        className="no-scrollbar flex min-w-0 flex-1 items-stretch overflow-x-auto"
      >
        {tabs.map((tab) => {
          const active = tab.id === activeTabId;
          return (
            <div
              key={tab.id}
              data-active={active}
              className={cn(
                "group relative flex min-w-0 shrink-0 items-center gap-1.5 border-r border-line pr-1 pl-2.5 transition-colors",
                active ? "bg-surface text-ink" : "text-muted hover:bg-raised/50",
              )}
            >
              {active && (
                <span className="absolute inset-x-0 top-0 h-[1.5px] bg-accent" />
              )}
              <button
                onClick={() => dispatch({ type: "tab.activate", id: tab.id })}
                className="flex min-w-0 items-center gap-1.5 py-1.5 text-[11.5px]"
              >
                {tab.pinned && <Pin className="h-2.5 w-2.5 shrink-0 text-accent" />}
                {tab.entityId && (
                  <span className="shrink-0 text-[9px] tracking-wide text-faint uppercase">
                    {SECTION_LABEL[tab.section]}
                  </span>
                )}
                <span className={cn("max-w-[132px] truncate", active && "font-medium")}>
                  {tab.label}
                </span>
              </button>
              <button
                onClick={() => dispatch({ type: "tab.pin", id: tab.id })}
                className={cn(
                  "shrink-0 rounded p-0.5 transition",
                  tab.pinned
                    ? "text-accent"
                    : "text-faint opacity-0 group-hover:opacity-100 hover:text-ink",
                )}
                aria-label={tab.pinned ? "Unpin tab" : "Pin tab"}
              >
                <Pin className="h-2.5 w-2.5" />
              </button>
              <button
                onClick={() => closeTab(tab.id)}
                className="shrink-0 rounded p-0.5 text-faint opacity-0 transition group-hover:opacity-100 hover:text-ink"
                aria-label={`Close ${tab.label}`}
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </div>
          );
        })}

        <HoverTip content="New tab">
          <button
            onClick={() => setSection("home")}
            className="flex w-7 shrink-0 items-center justify-center text-faint transition hover:bg-raised/60 hover:text-ink"
            aria-label="New tab"
          >
            <Plus className="h-3 w-3" />
          </button>
        </HoverTip>

        {closed > 0 && (
          <HoverTip content={`Reopen ${state.closedTabs[0].label}`}>
            <button
              onClick={reopenTab}
              className="flex w-7 shrink-0 items-center justify-center gap-1 text-faint transition hover:bg-raised/60 hover:text-ink"
              aria-label="Reopen closed tab"
            >
              <RotateCcw className="h-2.5 w-2.5" />
              <span className="num text-[9px]">{closed}</span>
            </button>
          </HoverTip>
        )}
      </div>

      <div className="hidden shrink-0 items-center gap-1.5 border-l border-line px-2 sm:flex">
        <Badge tone="neutral">
          {tabs.length} {tabs.length === 1 ? "tab" : "tabs"}
        </Badge>
      </div>
    </div>
  );
}
