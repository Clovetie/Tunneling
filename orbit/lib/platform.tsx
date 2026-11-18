"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { currentPlatform, isAppWindow, type Platform } from "@/lib/keys";

const PlatformContext = createContext<{ platform: Platform; appWindow: boolean }>({
  platform: "win",
  appWindow: false,
});

/**
 * Platform is only readable in the browser, and rendering a Mac glyph during
 * SSR would cause a hydration mismatch. So the provider starts at the Windows
 * default (also the SSR value) and corrects itself in an effect — one frame of
 * Windows labels on a Mac, which is invisible in practice and never mismatches,
 * because the server and the first client render agree.
 */
export function PlatformProvider({ children }: { children: React.ReactNode }) {
  const [platform, setPlatform] = useState<Platform>("win");
  const [appWindow, setAppWindow] = useState(false);

  useEffect(() => {
    setPlatform(currentPlatform());
    setAppWindow(isAppWindow());
    const mq =
      typeof window.matchMedia === "function"
        ? window.matchMedia("(display-mode: standalone)")
        : null;
    if (!mq) return;
    const onChange = () => setAppWindow(isAppWindow());
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return (
    <PlatformContext.Provider value={{ platform, appWindow }}>{children}</PlatformContext.Provider>
  );
}

export function usePlatform() {
  return useContext(PlatformContext);
}
