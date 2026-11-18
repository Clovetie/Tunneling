"use client";

import React, { useEffect, useState } from "react";
import { chord } from "@/lib/keys";
import { usePlatform } from "@/lib/platform";
import {
  Bell,
  ChevronRight,
  CircleDot,
  Command,
  HelpCircle,
  LayoutPanelLeft,
  Menu,
  Search,
  Sparkles,
} from "lucide-react";
import { useOrbit } from "@/lib/store";
import { cn, fmtClock, fmtLongDate } from "@/lib/util";
import { HoverTip } from "@/components/ui/kit";

const SECTION_LABEL: Record<string, string> = {
  home: "Home",
  assistant: "AI Assistant",
  projects: "Projects",
  tasks: "Tasks",
  calendar: "Calendar",
  knowledge: "Knowledge",
  files: "Files",
  automations: "Automations",
  integrations: "Integrations",
  activity: "Activity",
  settings: "Settings",
};

export function TopBar({
  onOpenSearch,
  onOpenNav,
}: {
  onOpenSearch: () => void;
  onOpenNav?: () => void;
}) {
  const { section, state, dispatch, setCommandOpen } = useOrbit();
  const { platform } = usePlatform();
  const [clock, setClock] = useState("--:--:--");
  const [date, setDate] = useState("");

  useEffect(() => {
    const tick = () => {
      setClock(fmtClock(new Date(), state.identity.timezone));
      setDate(fmtLongDate(new Date(), state.identity.timezone));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [state.identity.timezone]);

  useEffect(() => {
    const onSidebar = () =>
      dispatch({
        type: "prefs",
        patch: { sidebarCollapsed: !state.preferences.sidebarCollapsed },
      });
    const onPanel = () =>
      dispatch({
        type: "prefs",
        patch: { rightPanelOpen: !state.preferences.rightPanelOpen },
      });
    window.addEventListener("orbit:toggle-sidebar", onSidebar);
    window.addEventListener("orbit:toggle-panel", onPanel);
    return () => {
      window.removeEventListener("orbit:toggle-sidebar", onSidebar);
      window.removeEventListener("orbit:toggle-panel", onPanel);
    };
  }, [dispatch, state.preferences.sidebarCollapsed, state.preferences.rightPanelOpen]);

  const alerts = state.integrations.filter((i) => i.status === "error").length;

  return (
    <header className="flex h-[52px] shrink-0 items-center gap-2 border-b border-line bg-abyss/80 px-3 backdrop-blur md:gap-3 md:px-4">
      {onOpenNav && (
        <button
          onClick={onOpenNav}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[6px] text-muted transition hover:bg-raised hover:text-ink md:hidden"
          aria-label="Open navigation"
        >
          <Menu className="h-4 w-4" />
        </button>
      )}
      <div className="flex min-w-0 items-center gap-1.5 text-[12px]">
        <span className="text-faint">ORBIT</span>
        <ChevronRight className="h-3 w-3 text-faint/60" />
        <span className="font-medium text-ink">{SECTION_LABEL[section] ?? section}</span>
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        <div className="hidden items-center gap-2 rounded-[7px] border border-line bg-surface px-2.5 py-1 text-[11px] md:flex">
          <span className="num text-muted">{clock}</span>
          <span className="h-2.5 w-px bg-line" />
          <span className="text-faint">{date}</span>
          <span className="h-2.5 w-px bg-line" />
          <span className="flex items-center gap-1 text-faint">
            <CircleDot className="h-2.5 w-2.5 text-signal" />
            {state.identity.timezone.replace("Asia/", "")}
          </span>
        </div>

        <button
          onClick={onOpenSearch}
          className="hidden items-center gap-2 rounded-[7px] border border-line bg-surface px-2.5 py-1 text-[11.5px] text-faint transition hover:border-line-bright hover:text-muted sm:flex"
        >
          <Search className="h-3 w-3" />
          Search
          <span className="flex items-center gap-0.5 rounded border border-line px-1 text-[9.5px]">
            <Command className="h-2 w-2" /> K
          </span>
        </button>

        <HoverTip content={`Command palette (${chord(platform, "mod", "k")})`}>
          <button
            onClick={() => setCommandOpen(true)}
            className="flex h-7 w-7 items-center justify-center rounded-[6px] text-faint transition hover:bg-raised hover:text-ink"
            aria-label="Command palette"
          >
            <Command className="h-3.5 w-3.5" />
          </button>
        </HoverTip>

        <HoverTip content="Toggle intelligence panel (])">
          <button
            onClick={() =>
              dispatch({
                type: "prefs",
                patch: { rightPanelOpen: !state.preferences.rightPanelOpen },
              })
            }
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-[6px] transition hover:bg-raised hover:text-ink",
              state.preferences.rightPanelOpen ? "text-accent" : "text-faint",
            )}
            aria-label="Toggle panel"
          >
            <LayoutPanelLeft className="h-3.5 w-3.5" />
          </button>
        </HoverTip>

        <HoverTip content={alerts ? `${alerts} integration needs attention` : "No alerts"}>
          <button
            onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "?" }))}
            className="relative flex h-7 w-7 items-center justify-center rounded-[6px] text-faint transition hover:bg-raised hover:text-ink"
            aria-label="Alerts"
          >
            <Bell className="h-3.5 w-3.5" />
            {alerts > 0 && (
              <span className="absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-danger" />
            )}
          </button>
        </HoverTip>

        <button
          onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "?" }))}
          className="flex h-7 w-7 items-center justify-center rounded-[6px] text-faint transition hover:bg-raised hover:text-ink"
          aria-label="Keyboard shortcuts"
        >
          <HelpCircle className="h-3.5 w-3.5" />
        </button>

        <button
          onClick={() => window.dispatchEvent(new CustomEvent("orbit:focus-assistant"))}
          className="ml-1 flex h-7 items-center gap-1.5 rounded-[6px] bg-accent px-2.5 text-[11.5px] font-medium text-[#0a0d14] transition hover:brightness-110"
        >
          <Sparkles className="h-3 w-3" />
          Ask
        </button>
      </div>
    </header>
  );
}
