"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Boxes,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Circle,
  Command,
  FolderKanban,
  Gauge,
  ListChecks,
  Plus,
  Search,
  Settings2,
  Sparkles,
  Workflow,
  Library,
  Pin,
  Plug,
  PanelLeftClose,
} from "lucide-react";
import { useOrbit } from "@/lib/store";
import { cn, relative } from "@/lib/util";
import type { SectionId } from "@/lib/types";
import { Badge } from "@/components/ui/kit";
import { QuickCreate } from "./QuickCreate";

const NAV: { id: SectionId; label: string; icon: React.ElementType }[] = [
  { id: "home", label: "Home", icon: Gauge },
  { id: "assistant", label: "AI Assistant", icon: Sparkles },
  { id: "projects", label: "Projects", icon: FolderKanban },
  { id: "tasks", label: "Tasks", icon: ListChecks },
  { id: "calendar", label: "Calendar", icon: CalendarDays },
  { id: "knowledge", label: "Knowledge", icon: Library },
  { id: "files", label: "Files", icon: Boxes },
  { id: "automations", label: "Automations", icon: Workflow },
  { id: "integrations", label: "Integrations", icon: Plug },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings2 },
];

export function Sidebar({
  onOpenSearch,
  onDeepSearch,
  forceExpanded = false,
}: {
  onOpenSearch: () => void;
  /** Opens the cross-entity search overlay (Shift+Cmd+F). */
  onDeepSearch?: () => void;
  /** The mobile drawer always renders the full rail regardless of desktop prefs. */
  forceExpanded?: boolean;
}) {
  const { state, section, setSection, dispatch, select } = useOrbit();
  const [quickOpen, setQuickOpen] = useState(false);
  const [quickKind, setQuickKind] = useState<"task" | "project" | "file" | "reminder" | undefined>();
  const collapsed = forceExpanded ? false : state.preferences.sidebarCollapsed;

  // Any screen can open quick-create without owning the modal.
  useEffect(() => {
    const onStep = (e: Event) => {
      const dir = (e as CustomEvent<{ dir: 1 | -1 }>).detail?.dir ?? 1;
      dispatch({ type: "tab.step", dir });
    };
    const onClose = () => {
      const ws = state.workspaces.find((w) => w.id === state.activeWorkspaceId);
      if (ws?.activeTabId) dispatch({ type: "tab.close", id: ws.activeTabId });
    };
    const onNew = () => dispatch({ type: "navigate", section: "home" });
    window.addEventListener("orbit:tab-step", onStep);
    window.addEventListener("orbit:tab-close", onClose);
    window.addEventListener("orbit:tab-new", onNew);
    return () => {
      window.removeEventListener("orbit:tab-step", onStep);
      window.removeEventListener("orbit:tab-close", onClose);
      window.removeEventListener("orbit:tab-new", onNew);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onQuick = (e: Event) => {
      const kind = (e as CustomEvent<{ kind?: typeof quickKind }>).detail?.kind;
      setQuickKind(kind);
      setQuickOpen(true);
    };
    window.addEventListener("orbit:quick", onQuick);
    window.addEventListener("orbit:quick-create", onQuick);
    return () => {
      window.removeEventListener("orbit:quick", onQuick);
      window.removeEventListener("orbit:quick-create", onQuick);
    };
  }, []);

  const counts = useMemo(() => {
    const openTasks = state.tasks.filter((t) => t.status !== "done").length;
    const active = state.projects.filter((p) => p.status === "active").length;
    const alerts = state.integrations.filter((i) => i.status === "error").length;
    return { openTasks, active, alerts };
  }, [state.tasks, state.projects, state.integrations]);

  const pinned = state.projects.filter((p) => p.pinned);
  const recent = [...state.projects]
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, 5);
  const connections = state.integrations.filter((i) => i.status === "connected").length;

  return (
    <>
      <aside
        className={cn(
          "relative z-30 flex h-full shrink-0 flex-col border-r border-line bg-abyss transition-[width] duration-200",
          forceExpanded ? "w-full" : collapsed ? "w-[68px]" : "w-[248px]",
        )}
      >
        {/* brand */}
        <div className="flex h-[52px] items-center gap-2.5 border-b border-line px-3.5">
          <div className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-[7px] bg-accent/12 ring-1 ring-accent/30">
            <span className="absolute h-[9px] w-[9px] rounded-full bg-accent" />
            <span className="absolute h-[18px] w-[18px] rounded-full border border-accent/40" />
            <span className="absolute h-[3px] w-[3px] translate-x-[9px] rounded-full bg-signal" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div className="text-[13px] font-semibold tracking-[-0.01em] text-ink">ORBIT</div>
              <div className="truncate text-[10px] tracking-wide text-faint">
                personal command center
              </div>
            </div>
          )}
        </div>

        {/* quick create + search */}
        <div className="flex flex-col gap-1.5 px-2.5 py-2.5">
          {collapsed ? (
            <>
              <button
                onClick={() => setQuickOpen(true)}
                className="flex h-8 w-full items-center justify-center rounded-[7px] bg-accent text-[#0a0d14] transition hover:brightness-110"
                aria-label="Quick create"
              >
                <Plus className="h-4 w-4" />
              </button>
              <button
                onClick={onDeepSearch ?? onOpenSearch}
                className="flex h-8 w-full items-center justify-center rounded-[7px] border border-line text-faint transition hover:text-ink"
                aria-label="Search everything"
                title="Search everything (Shift+Cmd+F)"
              >
                <Search className="h-4 w-4" />
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => setQuickOpen(true)}
                className="group flex h-8 items-center justify-between rounded-[7px] bg-accent px-2.5 text-[12.5px] font-medium text-[#0a0d14] transition hover:brightness-110"
              >
                <span className="flex items-center gap-1.5">
                  <Plus className="h-3.5 w-3.5" /> Quick create
                </span>
                <span className="rounded border border-[#0a0d14]/25 px-1 text-[10px] opacity-70">
                  ⌘K
                </span>
              </button>
              <button
                onClick={onDeepSearch ?? onOpenSearch}
                className="flex h-8 items-center gap-2 rounded-[7px] border border-line bg-surface px-2.5 text-[12px] text-faint transition hover:border-line-bright hover:text-muted"
                title="Search all entities (Shift+Cmd+F)"
              >
                <Search className="h-3.5 w-3.5" />
                <span>Search everything</span>
                <span className="ml-auto flex items-center gap-0.5 text-[10px]">
                  <Command className="h-2.5 w-2.5" />⇧F
                </span>
              </button>
            </>
          )}
        </div>

        {/* nav */}
        <nav className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-2">
          <div className={cn("flex flex-col gap-0.5", collapsed && "items-center")}>
            {NAV.map((item) => {
              const active = section === item.id;
              const Icon = item.icon;
              const badge =
                item.id === "tasks"
                  ? counts.openTasks
                  : item.id === "projects"
                    ? counts.active
                    : item.id === "integrations" && counts.alerts
                      ? counts.alerts
                      : null;
              return (
                <button
                  key={item.id}
                  onClick={() => setSection(item.id)}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    "group relative flex h-[31px] items-center rounded-[6px] text-[12.5px] transition-colors duration-120",
                    collapsed ? "w-9 justify-center" : "w-full gap-2.5 px-2",
                    active
                      ? "bg-raised text-ink"
                      : "text-muted hover:bg-raised/60 hover:text-ink",
                  )}
                >
                  {active && (
                    <span className="absolute top-1/2 left-0 h-3.5 w-[2px] -translate-y-1/2 rounded-full bg-accent" />
                  )}
                  <Icon
                    className={cn(
                      "h-[15px] w-[15px] shrink-0",
                      active ? "text-accent" : "text-faint group-hover:text-muted",
                    )}
                  />
                  {!collapsed && (
                    <>
                      <span className="truncate">{item.label}</span>
                      {badge ? (
                        <span
                          className={cn(
                            "ml-auto num rounded-full px-1.5 py-[1px] text-[10px]",
                            item.id === "integrations"
                              ? "bg-danger/15 text-danger"
                              : "bg-overlay text-faint",
                          )}
                        >
                          {badge}
                        </span>
                      ) : null}
                    </>
                  )}
                </button>
              );
            })}
          </div>

          {!collapsed && (
            <>
              <div className="mt-5 mb-1.5 flex items-center gap-1.5 px-2">
                <Pin className="h-3 w-3 text-faint" />
                <span className="text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
                  Pinned
                </span>
              </div>
              <div className="flex flex-col gap-0.5">
                {pinned.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => {
                      select({ projectId: p.id });
                      setSection("projects");
                    }}
                    className="flex h-[27px] items-center gap-2 rounded-[6px] px-2 text-[12px] text-muted transition-colors hover:bg-raised/60 hover:text-ink"
                  >
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ background: p.color }}
                    />
                    <span className="truncate">{p.name}</span>
                  </button>
                ))}
                {!pinned.length && (
                  <div className="px-2 py-1 text-[11.5px] text-faint">Nothing pinned</div>
                )}
              </div>

              <div className="mt-4 mb-1.5 px-2 text-[10px] font-medium tracking-[0.12em] text-faint uppercase">
                Recent
              </div>
              <div className="flex flex-col gap-0.5">
                {recent.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => {
                      select({ projectId: p.id });
                      setSection("projects");
                    }}
                    className="flex h-[27px] items-center gap-2 rounded-[6px] px-2 text-[12px] text-muted transition-colors hover:bg-raised/60 hover:text-ink"
                  >
                    <Circle className="h-[5px] w-[5px] shrink-0 fill-current text-faint" />
                    <span className="truncate">{p.name}</span>
                    <span className="ml-auto shrink-0 text-[10px] text-faint">
                      {relative(p.updatedAt).replace(" ago", "")}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </nav>

        {/* footer */}
        <div className="border-t border-line p-2.5">
          {collapsed ? (
            <div className="flex flex-col items-center gap-2">
              <div className="h-6 w-6 rounded-full bg-raised text-[10px] leading-6 text-center font-medium text-muted">
                {state.identity.avatarInitials}
              </div>
              <button
                onClick={() =>
                  dispatch({ type: "prefs", patch: { sidebarCollapsed: false } })
                }
                className="text-faint transition hover:text-ink"
                aria-label="Expand sidebar"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : (
            <>
              <div className="mb-2 flex items-center justify-between rounded-[7px] bg-surface/60 px-2 py-1.5">
                <div className="flex items-center gap-1.5">
                  <span className="anim-live h-1.5 w-1.5 rounded-full bg-signal" />
                  <span className="text-[11px] text-muted">
                    {connections} integrations live
                  </span>
                </div>
                <Badge tone="success">ok</Badge>
              </div>
              <div className="flex items-center gap-2 rounded-[7px] px-1 py-1">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/15 text-[10.5px] font-semibold text-accent ring-1 ring-accent/25">
                  {state.identity.avatarInitials}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] font-medium text-ink">
                    {state.identity.name}
                  </div>
                  <div className="truncate text-[10px] text-faint">
                    {state.identity.timezone}
                  </div>
                </div>
                <button
                  onClick={() =>
                    dispatch({ type: "prefs", patch: { sidebarCollapsed: true } })
                  }
                  className="rounded p-1 text-faint transition hover:bg-raised hover:text-ink"
                  aria-label="Collapse sidebar"
                >
                  <PanelLeftClose className="h-3.5 w-3.5" />
                </button>
              </div>
            </>
          )}
        </div>
      </aside>

      <QuickCreate
        open={quickOpen}
        initialKind={quickKind}
        onClose={() => {
          setQuickOpen(false);
          setQuickKind(undefined);
        }}
      />
    </>
  );
}
