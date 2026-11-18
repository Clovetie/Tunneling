import type { SectionId } from "./types";

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
