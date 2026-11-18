"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ArrowUp,
  Bot,
  Braces,
  Check,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  Copy,
  FileSearch,
  FolderOpen,
  Layers,
  Loader2,
  Network,
  Paperclip,
  Pencil,
  Pin,
  Plus,
  Search,
  ShieldQuestion,
  Sparkles,
  SquareStack,
  Terminal,
  X,
  Zap,
  BarChart3,
  Compass,
  ListTree,
  Play,
  Brain,
} from "lucide-react";
import { useOrbit, useActions } from "@/lib/store";
import { executeTool } from "@/lib/tools";
import { planFor } from "@/lib/engine";
import { chord } from "@/lib/keys";
import { usePlatform } from "@/lib/platform";
import { cn, fmtTime, relative, sleep, uid } from "@/lib/util";
import { Badge, Button, HoverTip, Input, Modal, Textarea } from "@/components/ui/kit";
import type {
  AgentId,
  AgentStep,
  Conversation,
  Message,
  Mode,
  StructuredBlock,
  ToolCall,
} from "@/lib/types";
import { MessageBody } from "./assistant/MessageBody";
import { ToolTrace } from "./assistant/ToolTrace";
import { AgentTrace } from "./assistant/AgentTrace";
import { AgentDock } from "./assistant/AgentDock";

const MODES: { id: Mode; label: string; icon: React.ElementType; blurb: string; accent: string }[] = [
  { id: "chat", label: "Chat", icon: Compass, blurb: "Discuss and reason. No external actions.", accent: "#8b96a5" },
  { id: "research", label: "Research", icon: FileSearch, blurb: "Gather, compare, cite. Sources stay attached.", accent: "#56cfd8" },
  { id: "planning", label: "Planning", icon: ListTree, blurb: "Break objectives into sequenced work.", accent: "#a78bfa" },
  { id: "execution", label: "Execution", icon: Play, blurb: "Perform approved operations. Every write is gated.", accent: "#4ade9b" },
  { id: "analysis", label: "Analysis", icon: BarChart3, blurb: "Examine files and data. Read-only.", accent: "#f5b556" },
  { id: "agent", label: "Agent", icon: Network, blurb: "Delegate to specialists via the Supervisor.", accent: "#7c9cff" },
];

const SUGGESTIONS = [
  "Organize everything I need to work on this week",
  "Summarize my unfinished tasks",
  "Find the document where I discussed my previous project architecture",
  "Create a research report about local AI models",
  "Monitor this project and notify me when something needs attention",
  "Summarise everything I accomplished today",
  "Find a way to automate this repetitive process",
  "Help me plan and execute this software project",
];

export function AssistantSection() {
  const { state, dispatch, setSection, select } = useOrbit();
  const { platform } = usePlatform();
  const actions = useActions();
  const [mode, setMode] = useState<Mode>("chat");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [folder, setFolder] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [contextOpen, setContextOpen] = useState(false);
  const [modeMenu, setModeMenu] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef(false);
  // Named so the Stop button and the Cmd+. shortcut below share one path.
  const stop = useCallback(() => {
    abortRef.current = true;
    setBusy(false);
  }, []);

  const conv = state.conversations.find((c) => c.id === state.activeConversationId) ?? null;

  const visibleConvs = useMemo(() => {
    return state.conversations
      .filter((c) => (showArchived ? true : !c.archived))
      .filter((c) => (folder ? c.folder === folder : true))
      .filter((c) =>
        filter.trim() ? c.title.toLowerCase().includes(filter.toLowerCase()) : true,
      )
      .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updatedAt.localeCompare(a.updatedAt));
  }, [state.conversations, filter, folder, showArchived]);

  const folders = useMemo(
    () => [...new Set(state.conversations.map((c) => c.folder))],
    [state.conversations],
  );

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [conv?.messages.length, busy]);

  const newConversation = useCallback(
    (seedMode: Mode = mode) => {
      const c: Conversation = {
        id: uid("cnv"),
        title: "New conversation",
        folder: "Daily",
        projectId: null,
        pinned: false,
        archived: false,
        updatedAt: new Date().toISOString(),
        contextIds: [],
        messages: [],
      };
      dispatch({ type: "conv.add", conversation: c });
      setMode(seedMode);
      setTimeout(() => inputRef.current?.focus(), 60);
    },
    [dispatch, mode],
  );

  useEffect(() => {
    const onPrompt = (e: Event) => {
      const detail = (e as CustomEvent<{ prompt: string }>).detail;
      if (!detail?.prompt) return;
      send(detail.prompt);
    };
    const onFocus = () => {
      setSection("assistant");
      setTimeout(() => inputRef.current?.focus(), 80);
    };
    window.addEventListener("orbit:prompt", onPrompt);
    window.addEventListener("orbit:focus-assistant", onFocus);
    return () => {
      window.removeEventListener("orbit:prompt", onPrompt);
      window.removeEventListener("orbit:focus-assistant", onFocus);
    };
  });

  async function send(raw?: string) {
    const text = (raw ?? input).trim();
    if (!text || busy) return;
    abortRef.current = false;
    setInput("");
    setBusy(true);

    let target = conv;
    if (!target) {
      const c: Conversation = {
        id: uid("cnv"),
        title: text.slice(0, 64),
        folder: "Daily",
        projectId: null,
        pinned: false,
        archived: false,
        updatedAt: new Date().toISOString(),
        contextIds: [],
        messages: [],
      };
      dispatch({ type: "conv.add", conversation: c });
      target = c;
    }

    const userMsg: Message = {
      id: uid("m"),
      role: "user",
      content: text,
      mode,
      createdAt: new Date().toISOString(),
    };
    dispatch({ type: "msg.add", conversationId: target.id, message: userMsg });

    const plan = planFor(text, state, mode);
    const orbitMsg: Message = {
      id: uid("m"),
      role: "orbit",
      content: "",
      mode,
      agent: plan.agent,
      createdAt: new Date().toISOString(),
      toolCalls: plan.toolCalls,
      steps: plan.steps,
      streaming: true,
    };
    dispatch({ type: "msg.add", conversationId: target.id, message: orbitMsg });

    if (mode === "agent" || plan.steps.length > 1) {
      const runtimeId = uid("rt");
      dispatch({
        type: "runtime.add",
        runtime: {
          id: runtimeId,
          objective: text,
          supervisorNote:
            plan.steps.length > 1
              ? "Multi-step objective: delegating, then reviewing before reporting."
              : "Single-step objective: handled without delegation.",
          status: "running",
          startedAt: new Date().toISOString(),
          steps: plan.steps,
          mode,
        },
      });
      // animate agent steps
      for (let i = 0; i < plan.steps.length; i++) {
        if (abortRef.current) break;
        dispatch({
          type: "runtime.patch",
          id: runtimeId,
          patch: {
            steps: plan.steps.map((s, idx) =>
              idx < i
                ? { ...s, status: "done", finishedAt: new Date().toISOString() }
                : idx === i
                  ? { ...s, status: "working" }
                  : s,
            ),
          },
        });
        dispatch({
          type: "msg.patch",
          conversationId: target.id,
          messageId: orbitMsg.id,
          patch: {
            steps: plan.steps.map((s, idx) =>
              idx < i
                ? { ...s, status: "done", finishedAt: new Date().toISOString() }
                : idx === i
                  ? { ...s, status: "working" }
                  : s,
            ),
          },
        });
        await sleep(320);
      }
      dispatch({
        type: "runtime.patch",
        id: runtimeId,
        patch: {
          status: "review",
          steps: plan.steps.map((s) => ({ ...s, status: "done", finishedAt: new Date().toISOString() })),
        },
      });
      window.setTimeout(() => {
        dispatch({
          type: "runtime.patch",
          id: runtimeId,
          patch: {
            status: "complete",
            steps: plan.steps.map((s) => ({ ...s, status: "done", finishedAt: new Date().toISOString() })),
          },
        });
      }, 2600);
    }

    // execute tools sequentially, respecting approvals
    const results: Record<string, { ok: boolean; result: string; verified: boolean }> = {};
    const toolState = { ...state };

    for (const call of plan.toolCalls) {
      if (call.status === "denied") {
        results[call.id] = { ok: false, result: call.result ?? "denied", verified: false };
        dispatch({
          type: "msg.patch",
          conversationId: target.id,
          messageId: orbitMsg.id,
          patch: {
            toolCalls: plan.toolCalls.map((c) =>
              c.id === call.id ? { ...c, status: "denied" } : c,
            ),
          },
        });
        continue;
      }

      dispatch({
        type: "msg.patch",
        conversationId: target.id,
        messageId: orbitMsg.id,
        patch: {
          toolCalls: plan.toolCalls.map((c) =>
            c.id === call.id ? { ...c, status: call.requiresApproval ? "pending" : "pending" } : c,
          ),
        },
      });
      await sleep(call.requiresApproval ? 420 : 260);

      if (call.requiresApproval && state.preferences.confirmAllWrites) {
        // park the call: it stays pending for the operator to approve
        results[call.id] = {
          ok: false,
          result: "Awaiting operator approval",
          verified: false,
        };
        continue;
      }

      const res = executeTool(call, toolState as typeof state, actions);
      results[call.id] = { ok: res.ok, result: res.result, verified: res.audit.verified };
      dispatch({
        type: "msg.patch",
        conversationId: target.id,
        messageId: orbitMsg.id,
        patch: {
          toolCalls: plan.toolCalls.map((c) =>
            c.id === call.id
              ? {
                  ...c,
                  status: res.ok && res.audit.verified ? "success" : res.ok ? "success" : "error",
                  result: res.result,
                  durationMs: res.audit.durationMs,
                  auditAt: res.audit.at,
                }
              : c,
          ),
        },
      });
      // refresh working copy so later tools see earlier writes
      if (res.mutated) {
        (toolState.tasks as unknown[]) = state.tasks;
      }
    }

    const composed = plan.compose({ state, results, args: {}, mode });

    // stream the prose
    const words = composed.content.split(/(\s+)/);
    let acc = "";
    for (let i = 0; i < words.length; i++) {
      if (abortRef.current) break;
      acc += words[i];
      if (i % 3 === 0 || i === words.length - 1) {
        dispatch({
          type: "msg.patch",
          conversationId: target.id,
          messageId: orbitMsg.id,
          patch: { content: acc },
        });
        await sleep(9);
      }
    }

    dispatch({
      type: "msg.patch",
      conversationId: target.id,
      messageId: orbitMsg.id,
      patch: {
        content: composed.content,
        structured: composed.structured,
        sources: composed.sources,
        streaming: false,
      },
    });

    setBusy(false);
  }

  const pendingApprovals = (conv?.messages ?? [])
    .flatMap((m) => m.toolCalls ?? [])
    .filter((c) => c.status === "pending" && c.requiresApproval);

  function approveCall(callId: string) {
    if (!conv) return;
    const msg = conv.messages.find((m) => m.toolCalls?.some((c) => c.id === callId));
    if (!msg) return;
    const call = msg.toolCalls!.find((c) => c.id === callId)!;
    const res = executeTool(call, state, actions);
    dispatch({
      type: "tool.patch",
      conversationId: conv.id,
      messageId: msg.id,
      toolId: callId,
      patch: {
        status: res.ok && res.audit.verified ? "success" : "error",
        result: res.result,
        durationMs: res.audit.durationMs,
        auditAt: res.audit.at,
      },
    });
    actions.logActivity({
      kind: "approval",
      title: `Approved ${call.tool}`,
      detail: res.result,
      actor: "You",
      status: res.ok ? "ok" : "error",
    });
  }

  function denyCall(callId: string) {
    if (!conv) return;
    const msg = conv.messages.find((m) => m.toolCalls?.some((c) => c.id === callId));
    if (!msg) return;
    dispatch({
      type: "tool.patch",
      conversationId: conv.id,
      messageId: msg.id,
      toolId: callId,
      patch: { status: "denied", result: "Denied by operator. Nothing was written." },
    });
    actions.logActivity({
      kind: "approval",
      title: "Denied a write",
      detail: `${msg.toolCalls?.find((c) => c.id === callId)?.tool} was blocked by you.`,
      actor: "You",
      status: "warn",
    });
  }

  const activeMode = MODES.find((m) => m.id === mode)!;

  return (
    <div className="flex h-full min-h-0">
      {/* conversation rail */}
      <div className="hidden w-[246px] shrink-0 flex-col border-r border-line bg-abyss/60 md:flex">
        <div className="flex h-[42px] items-center justify-between border-b border-line px-3">
          <span className="text-[11px] font-medium tracking-[0.1em] text-muted uppercase">
            Conversations
          </span>
          <button
            onClick={() => newConversation()}
            className="rounded p-1 text-faint transition hover:bg-raised hover:text-ink"
            aria-label="New conversation"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="border-b border-line px-2.5 py-2">
          <div className="relative">
            <Search className="absolute top-1/2 left-2 h-3 w-3 -translate-y-1/2 text-faint" />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search history"
              className="w-full rounded-[6px] border border-line bg-surface py-1 pr-2 pl-6.5 text-[11.5px] text-ink placeholder:text-faint focus:border-accent/40 focus:outline-none"
            />
          </div>
        </div>
        <div className="flex flex-wrap gap-1 border-b border-line px-2.5 py-1.5">
          <button
            onClick={() => setFolder(null)}
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] transition",
              !folder ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
            )}
          >
            All
          </button>
          {folders.map((f) => (
            <button
              key={f}
              onClick={() => setFolder(f === folder ? null : f)}
              className={cn(
                "rounded-full px-2 py-0.5 text-[10px] transition",
                folder === f ? "bg-accent/15 text-accent" : "text-faint hover:text-muted",
              )}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {visibleConvs.map((c) => (
            <button
              key={c.id}
              onClick={() => dispatch({ type: "conv.active", id: c.id })}
              className={cn(
                "group mb-0.5 block w-full rounded-[6px] px-2 py-1.5 text-left transition-colors",
                c.id === state.activeConversationId
                  ? "bg-raised"
                  : "hover:bg-raised/50",
              )}
            >
              <div className="flex items-center gap-1.5">
                {c.pinned && <Pin className="h-2.5 w-2.5 shrink-0 text-accent" />}
                <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink">{c.title}</span>
                {c.archived && <Archive className="h-2.5 w-2.5 shrink-0 text-faint" />}
              </div>
              <div className="flex items-center gap-1.5 pl-0.5">
                <span className="text-[9.5px] text-faint">{c.folder}</span>
                <span className="h-0.5 w-0.5 rounded-full bg-line-bright" />
                <span className="text-[9.5px] text-faint">{relative(c.updatedAt)}</span>
                {c.messages.length > 0 && (
                  <>
                    <span className="h-0.5 w-0.5 rounded-full bg-line-bright" />
                    <span className="text-[9.5px] text-faint">{c.messages.length} msg</span>
                  </>
                )}
              </div>
            </button>
          ))}
          {!visibleConvs.length && (
            <div className="px-2 py-6 text-center text-[11px] text-faint">No conversations.</div>
          )}
        </div>
        <div className="border-t border-line p-2">
          <button
            onClick={() => setShowArchived((v) => !v)}
            className="flex w-full items-center gap-1.5 rounded-[6px] px-2 py-1 text-[10.5px] text-faint transition hover:text-muted"
          >
            <Archive className="h-3 w-3" />
            {showArchived ? "Hide archived" : "Show archived"}
          </button>
        </div>
      </div>

      {/* thread */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* header */}
        <div className="flex h-[42px] shrink-0 items-center gap-2 border-b border-line px-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-[12.5px] font-medium text-ink">
                {conv?.title ?? "New conversation"}
              </span>
              {conv && (
                <button
                  onClick={() => {
                    setRenameValue(conv.title);
                    setRenameOpen(true);
                  }}
                  className="rounded p-0.5 text-faint opacity-0 transition hover:text-ink group-hover:opacity-100 focus:opacity-100"
                  aria-label="Rename conversation"
                >
                  <Pencil className="h-2.5 w-2.5" />
                </button>
              )}
            </div>
          </div>
          {conv && (
            <div className="flex items-center gap-1">
              <HoverTip content={conv.pinned ? "Unpin" : "Pin"}>
                <button
                  onClick={() =>
                    dispatch({ type: "conv.patch", id: conv.id, patch: { pinned: !conv.pinned } })
                  }
                  className={cn(
                    "rounded p-1 transition hover:bg-raised",
                    conv.pinned ? "text-accent" : "text-faint hover:text-ink",
                  )}
                >
                  <Pin className="h-3.5 w-3.5" />
                </button>
              </HoverTip>
              <HoverTip content={conv.archived ? "Unarchive" : "Archive"}>
                <button
                  onClick={() =>
                    dispatch({ type: "conv.patch", id: conv.id, patch: { archived: !conv.archived } })
                  }
                  className="rounded p-1 text-faint transition hover:bg-raised hover:text-ink"
                >
                  <Archive className="h-3.5 w-3.5" />
                </button>
              </HoverTip>
              <HoverTip content="New conversation">
                <button
                  onClick={() => newConversation()}
                  className="rounded p-1 text-faint transition hover:bg-raised hover:text-ink"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </HoverTip>
            </div>
          )}
        </div>

        {/* mode bar */}
        <div className="flex shrink-0 items-center gap-2 border-b border-line bg-abyss/40 px-4 py-1.5">
          <div className="relative">
            <button
              onClick={() => setModeMenu((v) => !v)}
              className="flex items-center gap-1.5 rounded-[6px] border border-line bg-surface px-2 py-1 text-[11.5px] transition hover:border-line-bright"
            >
              <activeMode.icon className="h-3 w-3" style={{ color: activeMode.accent }} />
              <span className="font-medium text-ink">{activeMode.label}</span>
              <ChevronDown className="h-2.5 w-2.5 text-faint" />
            </button>
            {modeMenu && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setModeMenu(false)} />
                <div className="anim-fade-up absolute top-[calc(100%+6px)] left-0 z-50 w-[290px] overflow-hidden rounded-[9px] border border-line-bright bg-surface shadow-2xl">
                  {MODES.map((m) => {
                    const Icon = m.icon;
                    return (
                      <button
                        key={m.id}
                        onClick={() => {
                          setMode(m.id);
                          setModeMenu(false);
                        }}
                        className={cn(
                          "flex w-full items-start gap-2 px-2.5 py-2 text-left transition-colors",
                          mode === m.id ? "bg-accent/10" : "hover:bg-raised/70",
                        )}
                      >
                        <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: m.accent }} />
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-[12px] font-medium text-ink">{m.label}</span>
                            {mode === m.id && <Check className="h-3 w-3 text-accent" />}
                          </div>
                          <div className="text-[10.5px] leading-snug text-faint">{m.blurb}</div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </>
            )}
          </div>
          <span className="hidden truncate text-[10.5px] text-faint sm:block">
            {activeMode.blurb}
          </span>
          <div className="ml-auto flex items-center gap-1.5">
            {conv?.projectId && (
              <button
                onClick={() => {
                  select({ projectId: conv.projectId });
                  setSection("projects");
                }}
                className="hidden items-center gap-1 rounded-full border border-accent/25 bg-accent/10 px-1.5 py-[1px] text-[10px] text-accent sm:flex"
              >
                <FolderOpen className="h-2.5 w-2.5" />
                {state.projects.find((p) => p.id === conv.projectId)?.name}
              </button>
            )}
            {mode !== "chat" && (
              <Badge tone={mode === "execution" ? "success" : mode === "research" ? "accent" : "violet"} dot>
                {mode} mode
              </Badge>
            )}
            {mode === "execution" && (
              <Badge tone="warn">
                <ShieldQuestion className="h-2.5 w-2.5" /> gated
              </Badge>
            )}
          </div>
        </div>

        {/* messages */}
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-6">
          {!conv || conv.messages.length === 0 ? (
            <div className="mx-auto max-w-2xl pt-8">
              <div className="mb-1 flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-[8px] bg-accent/12 ring-1 ring-accent/25">
                  <Sparkles className="h-4 w-4 text-accent" />
                </div>
                <div>
                  <div className="text-[15px] font-semibold tracking-[-0.015em] text-ink">
                    What should ORBIT do?
                  </div>
                  <div className="text-[11.5px] text-faint">
                    {activeMode.label} mode · {activeMode.blurb}
                  </div>
                </div>
              </div>
              <div className="mt-4 grid gap-1.5 sm:grid-cols-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="group flex items-start gap-2 rounded-[8px] border border-line bg-surface/60 px-3 py-2 text-left transition-all hover:border-line-bright hover:bg-raised"
                  >
                    <ArrowUp className="mt-[3px] h-3 w-3 shrink-0 rotate-45 text-faint transition group-hover:text-accent" />
                    <span className="text-[11.5px] leading-relaxed text-muted group-hover:text-ink">
                      {s}
                    </span>
                  </button>
                ))}
              </div>
              <div className="mt-5 rounded-[8px] border border-line bg-abyss/50 p-3">
                <div className="mb-1.5 flex items-center gap-1.5">
                  <Terminal className="h-3 w-3 text-faint" />
                  <span className="text-[10px] font-medium tracking-[0.1em] text-faint uppercase">
                    What this build actually runs
                  </span>
                </div>
                <p className="text-[11px] leading-relaxed text-muted">
                  Requests are routed by intent against your live state. Every action is a real
                  tool call with a schema, a permission level, validation and an audit entry.
                  Writes stop at the approval gate when{" "}
                  <span className="text-ink">Confirm all writes</span> is on (it is). The assistant
                  cannot report success that a tool did not verify.
                </p>
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-4">
              {conv.messages.map((m) => (
                <MessageView
                  key={m.id}
                  message={m}
                  onApprove={approveCall}
                  onDeny={denyCall}
                />
              ))}
            </div>
          )}
        </div>

        {/* approvals */}
        {pendingApprovals.length > 0 && (
          <div className="shrink-0 border-t border-warn/25 bg-warn/[0.06] px-4 py-2">
            <div className="mx-auto flex max-w-3xl items-center gap-2">
              <ShieldQuestion className="h-3.5 w-3.5 shrink-0 text-warn" />
              <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink">
                {pendingApprovals.length} write{pendingApprovals.length === 1 ? "" : "s"} waiting on
                your approval — nothing has been applied.
              </span>
              <Button size="sm" variant="primary" onClick={() => approveCall(pendingApprovals[0].id)}>
                Approve
              </Button>
              <Button size="sm" variant="ghost" onClick={() => denyCall(pendingApprovals[0].id)}>
                Deny
              </Button>
            </div>
          </div>
        )}

        {/* composer */}
        <div className="shrink-0 border-t border-line bg-abyss/60 px-4 py-3">
          <div className="mx-auto max-w-3xl">
            {contextOpen && conv && (
              <div className="anim-fade-up mb-1.5 rounded-[9px] border border-line bg-surface p-2.5">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-[10px] font-medium tracking-[0.1em] text-faint uppercase">
                    Conversation context
                  </span>
                  <button
                    onClick={() => setContextOpen(false)}
                    className="text-[10px] text-faint hover:text-ink"
                  >
                    done
                  </button>
                </div>
                <div className="grid max-h-[140px] grid-cols-2 gap-1 overflow-y-auto">
                  {[...state.projects.map((p) => ({ id: p.id, label: p.name, kind: "project" })),
                    ...state.notes.map((n) => ({ id: n.id, label: n.title, kind: "note" })),
                    ...state.files.map((f) => ({ id: f.id, label: f.name, kind: "file" })),
                  ].map((item) => {
                    const attached = conv.contextIds.includes(item.id);
                    return (
                      <button
                        key={item.id}
                        onClick={() =>
                          dispatch({
                            type: "conv.patch",
                            id: conv.id,
                            patch: {
                              contextIds: attached
                                ? conv.contextIds.filter((x) => x !== item.id)
                                : [...conv.contextIds, item.id],
                              projectId:
                                item.kind === "project" && !attached ? item.id : conv.projectId,
                            },
                          })
                        }
                        className={cn(
                          "flex items-center gap-1.5 truncate rounded-[5px] border px-1.5 py-1 text-left text-[10.5px] transition",
                          attached
                            ? "border-accent/35 bg-accent/10 text-ink"
                            : "border-line text-muted hover:border-line-bright",
                        )}
                      >
                        <Check
                          className={cn(
                            "h-2.5 w-2.5 shrink-0",
                            attached ? "text-accent" : "text-transparent",
                          )}
                        />
                        <span className="truncate">{item.label}</span>
                      </button>
                    );
                  })}
                </div>
                <p className="mt-1.5 text-[9.5px] leading-relaxed text-faint">
                  Attached items are the context an answer may draw on. Nothing outside this list is
                  quoted back to you as fact.
                </p>
              </div>
            )}
            <div
              className={cn(
                "rounded-[10px] border bg-surface transition-colors",
                busy ? "border-accent/40" : "border-line focus-within:border-accent/45",
              )}
            >
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.metaKey || e.ctrlKey) && e.key === ".") {
                    e.preventDefault();
                    stop();
                    return;
                  }
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    send();
                  }
                  if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
                    e.preventDefault();
                    send();
                  }
                  // Ctrl+1-8 switches Chrome tabs on Windows, so that is Alt
                  // there; only macOS can use the Cmd number row.
                  const modeChord =
                    platform === "mac"
                      ? (e.metaKey || e.ctrlKey) && !e.altKey
                      : e.altKey && !e.metaKey && !e.ctrlKey;
                  if (modeChord && /^[1-6]$/.test(e.key)) {
                    e.preventDefault();
                    setMode(MODES[Number(e.key) - 1].id);
                  }
                }}
                rows={2}
                placeholder={`Message ORBIT in ${activeMode.label} mode…`}
                className="max-h-[180px] w-full resize-none bg-transparent px-3 pt-2.5 text-[13px] leading-relaxed text-ink placeholder:text-faint focus:outline-none"
              />
              <div className="flex items-center gap-1 px-2 pb-2">
                <HoverTip content="Attach a file from Files">
                  <button
                    onClick={() => setSection("files")}
                    className="rounded p-1 text-faint transition hover:bg-raised hover:text-ink"
                  >
                    <Paperclip className="h-3.5 w-3.5" />
                  </button>
                </HoverTip>
                <HoverTip content={`Context (${conv?.contextIds.length ?? 0})`}>
                  <button
                    onClick={() => setContextOpen((v) => !v)}
                    className={cn(
                      "rounded p-1 transition hover:bg-raised hover:text-ink",
                      contextOpen ? "text-accent" : "text-faint",
                    )}
                  >
                    <Layers className="h-3.5 w-3.5" />
                  </button>
                </HoverTip>
                <div className="mx-1 h-3 w-px bg-line" />
                <span className="text-[10px] text-faint">
                  {mode === "execution"
                    ? "Writes are gated"
                    : mode === "chat"
                      ? "No external actions"
                      : "Read-only unless approved"}
                </span>
                <div className="ml-auto flex items-center gap-1.5">
                  {busy ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={stop}
                    >
                      <CircleSlash className="h-3 w-3" /> Stop
                    </Button>
                  ) : (
                    <>
                      <span className="hidden text-[10px] text-faint sm:block">
                        {chord(platform, "mod", "enter")} to send
                      </span>
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => send()}
                        disabled={!input.trim()}
                      >
                        <ArrowUp className="h-3 w-3" /> Send
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </div>
            <div className="mt-1.5 flex items-center justify-between px-0.5">
              <span className="text-[10px] text-faint">
                ORBIT never claims a result a tool did not confirm.
              </span>
              <div className="flex items-center gap-2 text-[10px] text-faint">
                <span className="flex items-center gap-1">
                  <Braces className="h-2.5 w-2.5" /> {state.preferences.confirmAllWrites ? "write gate on" : "write gate off"}
                </span>
                <span className="flex items-center gap-1">
                  <SquareStack className="h-2.5 w-2.5" /> {conv?.contextIds.length ?? 0} context items
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <AgentDock />

      <Modal
        open={renameOpen}
        onClose={() => setRenameOpen(false)}
        title="Rename conversation"
        width="max-w-md"
      >
        <Input value={renameValue} onChange={(e) => setRenameValue(e.target.value)} autoFocus />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRenameOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              if (conv && renameValue.trim())
                dispatch({
                  type: "conv.patch",
                  id: conv.id,
                  patch: { title: renameValue.trim() },
                });
              setRenameOpen(false);
            }}
          >
            Save
          </Button>
        </div>
      </Modal>
    </div>
  );
}

function MessageView({
  message,
  onApprove,
  onDeny,
}: {
  message: Message;
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
}) {
  const [showTrace, setShowTrace] = useState(true);
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="anim-fade-up flex justify-end">
        <div className="max-w-[80%]">
          <div className="rounded-[10px] rounded-br-[3px] border border-line-bright bg-raised px-3.5 py-2.5">
            <p className="text-[13px] leading-relaxed whitespace-pre-wrap text-ink">
              {message.content}
            </p>
          </div>
          <div className="mt-1 flex items-center justify-end gap-1.5 text-[9.5px] text-faint">
            <span>{fmtTime(message.createdAt)}</span>
            <span>·</span>
            <span className="uppercase">{message.mode}</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="anim-fade-up">
      <div className="flex items-start gap-2.5">
        <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] bg-accent/12 ring-1 ring-accent/25">
          <Bot className="h-3.5 w-3.5 text-accent" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <span className="text-[11.5px] font-medium text-ink">ORBIT</span>
            <span className="rounded border border-line px-1 py-[0.5px] text-[9px] tracking-wide text-faint uppercase">
              {message.mode}
            </span>
            {message.agent && (
              <span className="rounded border border-violet/25 bg-violet/10 px-1 py-[0.5px] text-[9px] text-violet uppercase">
                {message.agent} agent
              </span>
            )}
            <span className="text-[9.5px] text-faint">{fmtTime(message.createdAt)}</span>
          </div>

          {message.steps && message.steps.length > 0 && (
            <div className="mb-2">
              <button
                onClick={() => setShowTrace((v) => !v)}
                className="flex items-center gap-1 text-[10px] text-faint transition hover:text-muted"
              >
                <ChevronRight className={cn("h-2.5 w-2.5 transition-transform", showTrace && "rotate-90")} />
                {message.steps.length} agent steps
              </button>
              {showTrace && <AgentTrace steps={message.steps} />}
            </div>
          )}

          {message.toolCalls && message.toolCalls.length > 0 && (
            <ToolTrace
              calls={message.toolCalls}
              onApprove={onApprove}
              onDeny={onDeny}
            />
          )}

          {message.content ? (
            <MessageBody
              content={message.content}
              structured={message.structured}
              sources={message.sources}
              streaming={message.streaming}
            />
          ) : (
            <div className="flex items-center gap-1.5 py-1 text-[12px] text-muted">
              <Loader2 className="h-3 w-3 animate-spin text-accent" />
              <span>Working…</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
