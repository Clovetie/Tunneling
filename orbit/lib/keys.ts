import type { SectionId } from "./types";

/* ------------------------------------------------------------- platform */

export type Platform = "mac" | "win";

/**
 * ORBIT targets Windows, so "win" is the default and the fallback when the UA
 * cannot be read. Mac is detected explicitly so the hints stay correct for
 * anyone opening the app on a laptop.
 *
 * Note that iPadOS Safari reports a Macintosh UA. ORBIT is desktop-first and
 * has no touch-specific bindings, so treating that as "mac" is harmless; if
 * touch gestures are ever added, gate this on `maxTouchPoints` as well.
 */
export function detectPlatform(ua: string | undefined): Platform {
  return /macintosh|mac os x|iphone|ipad|ipod/i.test(ua ?? "") ? "mac" : "win";
}

/** Best-effort read of the live platform; falls back to Windows during SSR. */
export function currentPlatform(): Platform {
  if (typeof navigator === "undefined") return "win";
  return detectPlatform(navigator.userAgent);
}

type Token =
  | "mod"
  | "alt"
  | "shift"
  | "ctrl"
  | "enter"
  | "tab"
  | "pageup"
  | "pagedown"
  | "esc"
  | "dot";

const SYMBOLS: Record<Platform, Record<Token | "sep", string>> = {
  mac: {
    mod: "⌘",
    alt: "⌥",
    shift: "⇧",
    ctrl: "⌃",
    enter: "↵",
    tab: "⇥",
    pageup: "⇞",
    pagedown: "⇟",
    esc: "Esc",
    dot: ".",
    sep: "",
  },
  win: {
    mod: "Ctrl",
    alt: "Alt",
    shift: "Shift",
    ctrl: "Ctrl",
    enter: "Enter",
    tab: "Tab",
    pageup: "PgUp",
    pagedown: "PgDn",
    esc: "Esc",
    dot: ".",
    sep: " ",
  },
};

/**
 * Render a binding for display. Single letters are uppercased; any other
 * string is passed through unchanged so callers can print `?`, `/` or `g h`.
 */
export function chord(platform: Platform, ...parts: (Token | string)[]): string {
  const s = SYMBOLS[platform];
  return parts
    .map((p) => {
      if (p in s && p !== "sep") return s[p as Token];
      return p.length === 1 ? p.toUpperCase() : p;
    })
    .join(s.sep);
}

/* ------------------------------------------------------------ g-chords */

/**
 * The `g`-chord navigation map.
 *
 * This exists as one exported constant because it was previously duplicated:
 * the shell's key handler and the shortcuts dialog each carried their own copy,
 * and they drifted (the dialog advertised `g w` for Automations while the
 * handler bound `g u`). Both now read this, and `scripts/smoke.tsx` asserts the
 * help sheet renders exactly these bindings.
 */
export const GO_MAP: Record<string, SectionId> = {
  h: "home",
  a: "assistant",
  p: "projects",
  t: "tasks",
  c: "calendar",
  k: "knowledge",
  f: "files",
  u: "automations",
  i: "integrations",
  l: "activity",
  s: "settings",
};

export const GO_ROWS: [string, SectionId][] = Object.entries(GO_MAP).map(([k, v]) => [
  `g ${k}`,
  v,
]);

/* ---------------------------------------------------- reserved chords */

/**
 * Chords Chrome reserves for itself on Windows and Linux. These are swallowed
 * before JavaScript sees them, so binding them in a normal tab is a promise the
 * app cannot keep. (Chromium has blocked them since Chrome 4.)
 */
export const RESERVED_WINDOWS = new Set([
  "ctrl+t",
  "ctrl+w",
  "ctrl+shift+t",
  "ctrl+shift+w",
  "ctrl+n",
  "ctrl+shift+n",
  "ctrl+tab",
  "ctrl+shift+tab",
  "ctrl+1",
  "ctrl+2",
  "ctrl+3",
  "ctrl+4",
  "ctrl+5",
  "ctrl+6",
  "ctrl+7",
  "ctrl+8",
  "ctrl+9",
  "ctrl+pageup",
  "ctrl+pagedown",
]);

/**
 * A Chromium *app window* — ORBIT installed via "Install this site as an app" —
 * is exempt from the reserved list and receives Ctrl+T / Ctrl+W again. That is
 * the one supported way to get real tab shortcuts on Windows, so the app
 * detects it and the help sheet adapts.
 */
export function isAppWindow(): boolean {
  if (typeof window === "undefined") return false;
  const standalone =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(display-mode: standalone)").matches;
  const iosStandalone = (window.navigator as { standalone?: boolean }).standalone === true;
  return standalone || iosStandalone;
}

/* ------------------------------------------------------ help contents */

export type HelpGroup = { title: string; rows: [string, string][] };

/**
 * The shortcuts dialog is generated from this rather than hand-written, so a
 * binding cannot be advertised unless it is defined here — and the tests
 * assert every row matches a real handler.
 */
export function helpGroups(platform: Platform, opts?: { appWindow?: boolean }): HelpGroup[] {
  const c = (...parts: (Token | string)[]) => chord(platform, ...parts);
  const isMac = platform === "mac";
  const appWindow = opts?.appWindow ?? false;

  // Tab stepping: Ctrl+Tab is reserved on Windows, so Windows uses Alt+PgUp/PgDn.
  const stepNext = isMac ? c("ctrl", "tab") : c("alt", "pagedown");
  const stepPrev = isMac ? c("ctrl", "shift", "tab") : c("alt", "pageup");

  // Tab create/close: only available as Ctrl chords inside an installed app
  // window; otherwise they are Alt chords, which always reach the page.
  const newTab = appWindow ? c("mod", "t") : c("alt", "t");
  const closeTab = appWindow ? c("mod", "w") : c("alt", "w");
  const reopenTab = appWindow ? c("mod", "shift", "t") : c("alt", "shift", "t");

  return [
    {
      title: "Global",
      rows: [
        [c("mod", "k"), "Command palette"],
        ["/", "Command palette"],
        [c("mod", "shift", "f"), "Search all entities"],
        ["\\", "Collapse / expand sidebar"],
        ["]", "Show / hide intelligence panel"],
        ["?", "This help"],
        [c("esc"), "Dismiss overlay"],
      ],
    },
    {
      title: "Tabs & panes",
      rows: [
        [newTab, "New tab (Home)"],
        [reopenTab, "Reopen closed tab"],
        [closeTab, "Close active tab"],
        [stepNext, "Next tab"],
        [stepPrev, "Previous tab"],
        ["double-click", "Rename workspace"],
      ],
    },
    {
      title: "Assistant",
      rows: [
        [c("mod", "enter"), "Send message"],
        [c("shift", "enter"), "New line"],
        [c(isMac ? "mod" : "alt", "1-6"), "Switch mode (Chat → Agent)"],
        [c("mod", "dot"), "Stop generation"],
      ],
    },
    {
      title: "Navigation",
      rows: GO_ROWS.map(([k, id]) => [k, id] as [string, string]),
    },
  ];
}
