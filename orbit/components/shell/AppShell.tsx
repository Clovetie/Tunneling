"use client";

import React, { useCallback, useEffect, useState } from "react";
import { cn } from "@/lib/util";
import { StoreProvider, useOrbit } from "@/lib/store";
import { Sidebar } from "./Sidebar";
import { RightPanel } from "./RightPanel";
import { CommandPalette } from "./CommandPalette";
import { Sections } from "../sections/Sections";
import { TopBar } from "./TopBar";
import { KeyboardHelp } from "./KeyboardHelp";

function Frame() {
  const { commandOpen, setCommandOpen, state, section, setSection } = useOrbit();
  const [help, setHelp] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const apply = () => setIsMobile(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  // The desktop sidebar is hidden below md; the drawer replaces it there.
  void isMobile;

  const openCmd = useCallback(() => setCommandOpen(true), [setCommandOpen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);

      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandOpen(true);
        return;
      }
      if (e.key === "/" && !typing) {
        e.preventDefault();
        setCommandOpen(true);
        return;
      }
      if (e.key === "?" && !typing) {
        e.preventDefault();
        setHelp((v) => !v);
        return;
      }
      if (e.key === "\\" && !typing) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("orbit:toggle-sidebar"));
      }
      if (e.key === "]" && !typing) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("orbit:toggle-panel"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setCommandOpen]);

  useEffect(() => {
    const onToggle = () => {
      // handled by store via dispatch in TopBar; kept for external dispatch
    };
    window.addEventListener("orbit:toggle-sidebar", onToggle);
    return () => window.removeEventListener("orbit:toggle-sidebar", onToggle);
  }, []);

  return (
    <div className="flex h-[100dvh] w-full overflow-hidden bg-void">
      <div className="hidden md:flex">
        <Sidebar onOpenSearch={() => setCommandOpen(true)} />
      </div>

      {mobileNav && (
        <div className="fixed inset-0 z-[85] md:hidden">
          <div
            className="anim-fade absolute inset-0 bg-void/70 backdrop-blur-[2px]"
            onClick={() => setMobileNav(false)}
          />
          <div className="anim-fade-up relative flex h-full w-[250px] flex-col border-r border-line-bright bg-abyss shadow-2xl">
            <Sidebar
              forceExpanded
              onOpenSearch={() => {
                setMobileNav(false);
                setCommandOpen(true);
              }}
            />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          onOpenSearch={() => setCommandOpen(true)}
          onOpenNav={() => setMobileNav(true)}
        />
        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-hidden">
            <Sections />
          </main>
          <RightPanel />
        </div>
      </div>
      <CommandPalette open={commandOpen || searchOpen} onClose={() => { setCommandOpen(false); setSearchOpen(false); }} />
      <KeyboardHelp open={help} onClose={() => setHelp(false)} />
    </div>
  );
}

export function AppShell() {
  return (
    <StoreProvider>
      <Frame />
    </StoreProvider>
  );
}
