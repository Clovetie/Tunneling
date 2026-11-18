"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/util";
import { StoreProvider, useOrbit } from "@/lib/store";
import { GO_MAP } from "@/lib/keys";
import { PlatformProvider, usePlatform } from "@/lib/platform";
import type { SectionId } from "@/lib/types";
import { Sidebar } from "./Sidebar";
import { RightPanel } from "./RightPanel";
import { CommandPalette } from "./CommandPalette";
import { Sections } from "../sections/Sections";
import { TopBar } from "./TopBar";
import { TabStrip } from "./TabStrip";
import { SearchOverlay } from "./SearchOverlay";
import { KeyboardHelp } from "./KeyboardHelp";

function Frame() {
  const { commandOpen, setCommandOpen, state, section, setSection } = useOrbit();
  const { platform, appWindow } = usePlatform();
  const [help, setHelp] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

  const openCmd = useCallback(() => setCommandOpen(true), [setCommandOpen]);
  // `g` is a chord prefix: press g, then a section key within ~1.1s.
  const chordArmed = useRef(false);
  const chordTimer = useRef<number | null>(null);

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
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "f") {
        e.preventDefault();
        setSearchOpen(true);
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
      // Tab stepping. Cmd+Tab is the macOS app switcher and Ctrl+Tab is
      // reserved by Chrome on Windows, so Windows uses Alt+PgUp/PgDn — which
      // the page reliably receives in a normal browser tab.
      const onMac = platform === "mac";
      const stepMod = onMac ? e.metaKey && !e.ctrlKey && !e.altKey : false;
      const stepAlt = !onMac && e.altKey && !e.ctrlKey && !e.metaKey;
      if (onMac && stepMod && e.key === "Tab") {
        e.preventDefault();
        window.dispatchEvent(
          new CustomEvent("orbit:tab-step", { detail: { dir: e.shiftKey ? -1 : 1 } }),
        );
      }
      if (stepAlt && (e.key === "PageDown" || e.key === "PageUp")) {
        e.preventDefault();
        window.dispatchEvent(
          new CustomEvent("orbit:tab-step", { detail: { dir: e.key === "PageDown" ? 1 : -1 } }),
        );
      }
      // Tab create/close. In a normal browser tab Chrome reserves Ctrl+T and
      // Ctrl+W and closes the page before the app sees the key, so the default
      // is Alt+T / Alt+W. Inside an installed Chromium app window those Ctrl
      // chords reach the page again, so both are accepted when we detect one.
      const tabMod = (e.metaKey || e.ctrlKey) && !e.altKey;
      const tabAlt = e.altKey && !e.metaKey && !e.ctrlKey;
      if (appWindow && tabMod && e.key.toLowerCase() === "w") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("orbit:tab-close"));
      }
      if (appWindow && tabMod && e.key.toLowerCase() === "t") {
        e.preventDefault();
        window.dispatchEvent(
          new CustomEvent(e.shiftKey ? "orbit:tab-reopen" : "orbit:tab-new"),
        );
      }
      if (tabAlt && e.key.toLowerCase() === "w") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("orbit:tab-close"));
      }
      if (tabAlt && e.key.toLowerCase() === "t") {
        e.preventDefault();
        window.dispatchEvent(
          new CustomEvent(e.shiftKey ? "orbit:tab-reopen" : "orbit:tab-new"),
        );
      }
      // g-chord navigation, as advertised in the shortcuts dialog.
      if (!typing && e.key === "g") {
        chordArmed.current = true;
        if (chordTimer.current) window.clearTimeout(chordTimer.current);
        chordTimer.current = window.setTimeout(() => {
          chordArmed.current = false;
          chordTimer.current = null;
        }, 1100);
        return;
      }
      if (!typing && chordArmed.current) {
        const target = GO_MAP[e.key.toLowerCase()];
        chordArmed.current = false;
        if (chordTimer.current) {
          window.clearTimeout(chordTimer.current);
          chordTimer.current = null;
        }
        if (target) {
          e.preventDefault();
          setSection(target);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setCommandOpen, setSection, platform, appWindow]);

  return (
    <div className="flex h-[100dvh] w-full overflow-hidden bg-void">
      <div className="hidden md:flex">
        <Sidebar onOpenSearch={() => setCommandOpen(true)} onDeepSearch={() => setSearchOpen(true)} />
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
        <TabStrip />
        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-hidden">
            <Sections />
          </main>
          <RightPanel />
        </div>
      </div>
      <CommandPalette
        open={commandOpen}
        onClose={() => setCommandOpen(false)}
      />
      <SearchOverlay open={searchOpen} onClose={() => setSearchOpen(false)} />
      <KeyboardHelp open={help} onClose={() => setHelp(false)} />
    </div>
  );
}

export function AppShell() {
  return (
    <StoreProvider>
      <PlatformProvider>
        <Frame />
      </PlatformProvider>
    </StoreProvider>
  );
}
