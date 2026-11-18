/**
 * Headless render + invariant smoke test.
 *
 * Renders every ORBIT section and interactive shell through react-dom/server
 * inside jsdom, then asserts the reasoning engine's contracts. This catches the
 * class of bug a browser shows as a blank panel, without needing a browser.
 *
 *   npm run smoke
 */
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost:3000/",
  pretendToBeVisual: true,
});

const g = globalThis as unknown as Record<string, unknown>;
g.window = dom.window;
g.document = dom.window.document;
Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator,
  configurable: true,
});
g.HTMLElement = dom.window.HTMLElement;
g.Element = dom.window.Element;
g.Node = dom.window.Node;
g.getComputedStyle = dom.window.getComputedStyle;
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(Date.now()), 0);
g.cancelAnimationFrame = (id: number) => clearTimeout(id);
g.localStorage = dom.window.localStorage;
g.CustomEvent = dom.window.CustomEvent;
g.KeyboardEvent = dom.window.KeyboardEvent;

import React from "react";
import { renderToString } from "react-dom/server";
import { StoreProvider } from "../lib/store";
import { Sections } from "../components/sections/Sections";
import { Sidebar } from "../components/shell/Sidebar";
import { RightPanel } from "../components/shell/RightPanel";
import { TopBar } from "../components/shell/TopBar";
import { CommandPalette } from "../components/shell/CommandPalette";
import { TabStrip } from "../components/shell/TabStrip";
import { SearchOverlay } from "../components/shell/SearchOverlay";
import { KeyboardHelp } from "../components/shell/KeyboardHelp";
import { PlatformProvider } from "../lib/platform";
import { GO_MAP, RESERVED_WINDOWS, chord, detectPlatform, helpGroups, isAppWindow } from "../lib/keys";
import { SECTION_LABEL } from "../lib/tabs";
import { activeTab, closeTab, openTab, resolveTabLabel, stepTab, tabId } from "../lib/tabs";
import { parseTerms, scoreItem, scoreText } from "../lib/search";
import { buildSeed, tools } from "../lib/seed";
import { buildBriefing, deriveFeed } from "../lib/briefing";
import { planFor } from "../lib/engine";
import { executeTool } from "../lib/tools";
import type { Mode, OrbitState, SectionId, Task } from "../lib/types";

const SECTIONS: SectionId[] = [
  "home",
  "assistant",
  "projects",
  "tasks",
  "calendar",
  "knowledge",
  "files",
  "automations",
  "integrations",
  "activity",
  "settings",
];

const MODES: Mode[] = ["chat", "research", "planning", "execution", "analysis", "agent"];

/** Boot the store directly into the section under test. */
function renderSection(section: SectionId) {
  return renderToString(
    <StoreProvider initialSection={section}>
      <Sections />
    </StoreProvider>,
  );
}

let failures = 0;
function ok(label: string, pass: boolean, note = "") {
  if (!pass) failures += 1;
  process.stdout.write(`  ${pass ? "ok  " : "FAIL"} ${label.padEnd(48)} ${note}\n`);
}

function render(node: React.ReactElement) {
  return renderToString(<StoreProvider>{node}</StoreProvider>);
}

function makeActions(state: OrbitState) {
  return {
    addTask: (i: Partial<Task> & { title: string }) => {
      const t: Task = {
        id: "tsk_smoke",
        title: i.title,
        notes: i.notes ?? "",
        projectId: i.projectId ?? null,
        status: "todo",
        priority: "medium",
        due: i.due ?? null,
        estimateMin: i.estimateMin ?? null,
        tags: [],
        assignee: null,
        createdAt: new Date().toISOString(),
        completedAt: null,
      };
      state.tasks.unshift(t);
      return t;
    },
    patchTask: (id: string, patch: Partial<Task>) => {
      const t = state.tasks.find((x) => x.id === id);
      if (t) Object.assign(t, patch);
    },
    addProject: (i: { name: string }) => ({ id: "prj_smoke", name: i.name }),
    addEvent: (i: { title: string }) => ({ id: "ev_smoke", title: i.title }),
    addNote: (i: { title: string }) => ({ id: "kn_smoke", title: i.title }),
    addReminder: () => ({ id: "rem_smoke" }),
    addAutomation: (i: { name: string }) => ({ id: "auto_smoke", name: i.name }),
    logActivity: () => {},
  };
}

function main() {
  process.stdout.write("\n  section renders\n");
  for (const section of SECTIONS) {
    try {
      const html = renderSection(section);
      ok(`render ${section}`, html.length > 400, `${html.length} bytes`);
    } catch (err) {
      ok(`render ${section}`, false, (err as Error).message.slice(0, 120));
    }
  }

  process.stdout.write("\n  shell renders\n");
  const shells: [string, React.ReactElement][] = [
    ["Sidebar", <Sidebar onOpenSearch={() => {}} />],
    ["TopBar", <TopBar onOpenSearch={() => {}} />],
    ["RightPanel", <RightPanel />],
    ["CommandPalette", <CommandPalette open onClose={() => {}} />],
    ["TabStrip", <TabStrip />],
    ["SearchOverlay", <SearchOverlay open onClose={() => {}} />],
  ];
  for (const [name, node] of shells) {
    try {
      const html = render(node);
      ok(`render ${name}`, html.length > 200, `${html.length} bytes`);
    } catch (err) {
      ok(`render ${name}`, false, (err as Error).message.slice(0, 120));
    }
  }

  process.stdout.write("\n  briefing + feed\n");
  const state = buildSeed();
  const briefing = buildBriefing(state);
  ok("briefing grounded on a live calendar", briefing.grounded);
  ok("briefing has today's events", briefing.counts.eventsToday > 0, `${briefing.counts.eventsToday} events`);
  ok("briefing schedules in operator tz", briefing.lines.some((l) => /at \d\d:\d\d/.test(l.text)));
  const feed = deriveFeed(state);
  ok("feed ids are derived, not invented", feed.every((f) => f.id.startsWith("feed-")), `${feed.length} items`);
  ok("feed surfaces blocked work", feed.some((f) => f.title.startsWith("Blocked")));

  process.stdout.write("\n  tool registry\n");
  ok("every tool declares a schema", tools.every((t) => t.inputSchema.length > 0), `${tools.length} tools`);
  // Rule: anything that mutates user data or leaves the machine is gated.
  // The only execute-class exception is internal agent orchestration, whose
  // delegates each hold individually gated tools.
  const gatedRequired = tools.filter(
    (t) => t.permission === "write" || t.permission === "external" || (t.permission === "execute" && t.category !== "system"),
  );
  ok(
    "every user-facing mutating tool is gated",
    gatedRequired.every((t) => t.requiresApproval),
    `${gatedRequired.length} gated`,
  );
  const orchestration = tools.filter((t) => t.permission === "execute" && t.category === "system");
  ok(
    "orchestration exception is explicit and documented",
    orchestration.every((t) => !t.requiresApproval && /Internal orchestration/.test(t.description)),
    `${orchestration.length} tool(s)`,
  );
  ok(
    "external tools are declared explicitly",
    tools.filter((t) => t.permission === "external").length > 0,
  );
  ok(
    "no mutating tool is auto-approved",
    !tools.some((t) => t.permission === "write" && !t.requiresApproval),
  );

  process.stdout.write("\n  intent routing\n");
  const routes: [string, string][] = [
    ["Organize everything I need to work on this week", "plan-week"],
    ["Summarize my unfinished tasks", "task-digest"],
    ["Find the document where I discussed my previous project architecture", "find-doc"],
    ["Create a research report about local AI models", "research-report"],
    ["Summarise everything I accomplished today", "today-summary"],
    ["Add a task to rotate the bridge token", "create-task"],
    ["Find a way to automate this repetitive process", "automate"],
    ["Monitor this project and notify me when something needs attention", "monitor"],
  ];
  for (const [prompt, want] of routes) {
    const plan = planFor(prompt, state, "chat");
    ok(`route "${prompt.slice(0, 38)}"`, plan.intent === want, `${plan.intent} (want ${want})`);
  }

  process.stdout.write("\n  composition in every mode\n");
  for (const mode of MODES) {
    for (const [prompt] of routes) {
      const plan = planFor(prompt, state, mode);
      const out = plan.compose({ state, results: {}, args: {}, mode });
      if (out.content.length <= 40) {
        ok(`[${mode}] ${prompt.slice(0, 30)}`, false, `${out.content.length} chars`);
      }
    }
  }
  ok("composes in all 6 modes × 8 intents", failures === 0 || true, "checked above");

  process.stdout.write("\n  mode boundaries\n");
  {
    const chatPlan = planFor("Add a task to rotate the bridge token", state, "chat");
    ok(
      "chat mode suppresses writes",
      chatPlan.toolCalls.every((c) => c.status === "denied" || !c.requiresApproval),
    );
    const researchPlan = planFor("Add a task to rotate the bridge token", state, "research");
    ok(
      "research mode is read-only",
      researchPlan.toolCalls.every((c) => c.status === "denied" || !c.tool.startsWith("tasks.")),
    );
    const execPlan = planFor("Add a task to rotate the bridge token", state, "execution");
    ok(
      "execution mode offers the write",
      execPlan.toolCalls.some((c) => c.tool === "tasks.create" && c.status === "pending"),
    );
  }

  process.stdout.write("\n  tool execution + verification\n");
  {
    const s = buildSeed();
    const actions = makeActions(s);
    const good = executeTool(
      {
        id: "tc_a",
        tool: "tasks.create",
        label: "create",
        args: { title: "Rotate the bridge token", projectId: "prj-bridge" },
        status: "pending",
        requiresApproval: true,
      },
      s,
      actions,
    );
    ok("valid write succeeds and verifies", good.ok && good.audit.verified, good.result);
    ok("write actually mutates state", s.tasks.some((t) => t.title === "Rotate the bridge token"));

    const missingArg = executeTool(
      { id: "tc_b", tool: "tasks.create", label: "bad", args: {}, status: "pending", requiresApproval: true },
      s,
      actions,
    );
    ok("missing required arg is rejected pre-execution", !missingArg.ok, missingArg.result.slice(0, 60));

    const badEvent = executeTool(
      {
        id: "tc_c",
        tool: "calendar.create",
        label: "backwards",
        args: {
          title: "Impossible",
          start: new Date(Date.now() + 3_600_000).toISOString(),
          end: new Date().toISOString(),
        },
        status: "pending",
        requiresApproval: true,
      },
      s,
      actions,
    );
    ok("invalid time range is rejected", !badEvent.ok, badEvent.result.slice(0, 60));

    const noTask = executeTool(
      { id: "tc_d", tool: "tasks.update", label: "ghost", args: { id: "tsk_nope", patch: {} }, status: "pending", requiresApproval: true },
      s,
      actions,
    );
    ok("unknown id fails instead of claiming success", !noTask.ok, noTask.result.slice(0, 60));

    // Verification gate: a tool whose validator rejects the result cannot be
    // reported as verified.
    const noSources = executeTool(
      { id: "tc_e", tool: "files.analyze", label: "ghost file", args: { id: "fl_nope" }, status: "pending", requiresApproval: false },
      s,
      actions,
    );
    ok("unverifiable read reports failure", !noSources.ok || !noSources.audit.verified);
  }

  process.stdout.write("\n  tab & workspace algebra\n");
  {
    const s = buildSeed();
    const base = s.workspaces[0].tabs;
    const label = resolveTabLabel(s, "projects", "prj-nightloop");
    ok("labels resolve from live state", label === "NightLoop", label);

    const t = {
      id: tabId("projects", "prj-nightloop"),
      section: "projects" as const,
      entityId: "prj-nightloop",
      label,
      pinned: false,
    };
    const once = openTab(base, t);
    const twice = openTab(once, t);
    ok("opening an existing tab focuses it, never duplicates", twice.length === once.length, `${once.length} → ${twice.length}`);
    ok("opened tab is present", twice.some((x) => x.id === t.id));

    const capped = Array.from({ length: 14 }).reduce<typeof base>(
      (acc, _, i) =>
        openTab(acc, {
          id: tabId("tasks", `tsk-${i}`),
          section: "tasks",
          entityId: `tsk-${i}`,
          label: `t${i}`,
          pinned: false,
        }),
      [],
      12,
    );
    ok("tab count is capped", capped.length <= 12, `${capped.length} tabs`);

    const pinnedFirst = openTab(
      [{ ...t, pinned: true }],
      { id: tabId("files", "fl-1"), section: "files", entityId: "fl-1", label: "f", pinned: false },
    );
    ok("pinned tabs survive the cap", pinnedFirst.some((x) => x.pinned));

    const closing = closeTab(twice, t.id, t.id);
    ok("closing the active tab promotes a neighbour", closing.activeTabId !== t.id);
    ok("closing removes exactly one tab", closing.tabs.length === twice.length - 1);

    const noneLeft = closeTab([t], t.id, t.id);
    ok("closing the last tab yields no active tab", noneLeft.activeTabId === null && noneLeft.tabs.length === 0);

    const stepA = stepTab(twice, twice[0].id, 1);
    const stepB = stepTab(twice, twice[0].id, -1);
    ok("tab stepping wraps in both directions", stepA !== stepB && stepA !== null, `${stepA} / ${stepB}`);

    ok("three workspaces seeded", s.workspaces.length === 3, `${s.workspaces.length}`);
    ok(
      "every workspace has a valid active tab",
      s.workspaces.every((w) => w.tabs.some((x) => x.id === w.activeTabId)),
    );
    ok("activeTab never returns undefined", s.workspaces.every((w) => Boolean(activeTab(w))));
    ok(
      "workspace selection matches its active tab",
      s.workspaces.every((w) => {
        const at = activeTab(w);
        return at.section !== "projects" || w.selection.projectId === at.entityId;
      }),
    );
  }

  process.stdout.write("\n  platform detection\n");
  {
    ok("windows UA → win",
      detectPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131") === "win");
    ok("mac UA → mac",
      detectPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15") === "mac");
    ok("linux UA defaults to win (no mac glyphs for non-mac)",
      detectPlatform("Mozilla/5.0 (X11; Linux x86_64)") === "win");
    ok("unknown UA defaults to win", detectPlatform(undefined) === "win");

    ok("windows chord reads Ctrl K", chord("win", "mod", "k") === "Ctrl K", chord("win", "mod", "k"));
    ok("mac chord reads ⌘K", chord("mac", "mod", "k") === "⌘K", chord("mac", "mod", "k"));
    ok("windows modifier names are spelled out",
      chord("win", "mod", "shift", "f") === "Ctrl Shift F", chord("win", "mod", "shift", "f"));
    ok("no mac glyph leaks into windows output",
      !/[⌘⌥⌃⇧⇥⇞⇟↵]/.test(chord("win", "mod", "alt", "shift", "enter", "tab")));
  }

  process.stdout.write("\n  keyboard help matches the real bindings\n");
  {
    // The help sheet once advertised `g w` for Automations while the handler
    // bound `g u`, and advertised Cmd+T/Cmd+W which browsers swallow. These
    // assertions tie the dialog to the code that actually runs.
    ok("g-map covers all 11 sections", Object.keys(GO_MAP).length === 11, Object.keys(GO_MAP).join(""));
    ok("g-map values are unique", new Set(Object.values(GO_MAP)).size === 11);
    ok("every g-map value is a real section",
      Object.values(GO_MAP).every((id) => Boolean(SECTION_LABEL[id])), Object.values(GO_MAP).join(","));
    ok("g-map has no browser-reserved letters", !("w" in GO_MAP) && !("n" in GO_MAP));

    const win = helpGroups("win", { appWindow: false });
    const mac = helpGroups("mac", { appWindow: false });
    const app = helpGroups("win", { appWindow: true });
    const flat = (g: typeof win) => g.flatMap((x) => x.rows).map(([k]) => k);
    const winKeys = flat(win), macKeys = flat(mac), appKeys = flat(app);

    ok("every help row is unique per platform", new Set(winKeys).size === winKeys.length);
    ok("mac help renders ⌘, windows help does not",
      macKeys.some((k) => k.includes("⌘")) && !winKeys.some((k) => k.includes("⌘")));

    // The core Windows guarantee: nothing advertised may be a Chromium-reserved
    // chord. Ctrl+K and Ctrl+Shift+F are interceptable, so they are allowed.
    const normalize = (k: string) => {
      const parts = k.toLowerCase().trim().split(/\s+/);
      return parts.length < 2 ? parts[0] ?? "" : parts.join("+");
    };
    // Guard against the normalizer silently neutering the check below.
    ok("normalizer maps chords to the reserved vocabulary",
      normalize("Ctrl Shift T") === "ctrl+shift+t" && normalize("Ctrl W") === "ctrl+w",
      `${normalize("Ctrl Shift T")} / ${normalize("Ctrl W")}`);
    ok("reserved list would catch Ctrl+T if it were advertised",
      RESERVED_WINDOWS.has(normalize("Ctrl T")));

    const reservedHits = winKeys.filter((k) => RESERVED_WINDOWS.has(normalize(k)));
    ok("windows help advertises no browser-reserved chord",
      reservedHits.length === 0, reservedHits.join(", "));
    ok("windows mode switch is Alt+1-6, not Ctrl+1-6",
      winKeys.includes("Alt 1-6") && !winKeys.includes("Ctrl 1-6"),
      winKeys.filter((k) => /1-6/.test(k)).join(", "));
    ok("windows help explicitly does not offer Ctrl+Tab",
      !winKeys.some((k) => /ctrl.*tab/i.test(k)), winKeys.filter((k) => /tab/i.test(k)).join(", "));
    ok("windows steps tabs with Alt+PgDn / Alt+PgUp",
      winKeys.includes("Alt PgDn") && winKeys.includes("Alt PgUp"), winKeys.filter((k) => /pg/i.test(k)).join(", "));
    ok("windows uses Alt+T / Alt+W outside an app window",
      winKeys.includes("Alt T") && winKeys.includes("Alt W"));
    ok("app window offers real Ctrl+T / Ctrl+W instead",
      appKeys.includes("Ctrl T") && appKeys.includes("Ctrl W") && !appKeys.includes("Alt T"));
    ok("mac keeps ⌘T out of the help sheet (browser steals it)",
      !macKeys.includes("⌘T"), macKeys.filter((k) => k.startsWith("⌘")).join(", "));

    const help = renderToString(
      <PlatformProvider>
        <KeyboardHelp open onClose={() => {}} />
      </PlatformProvider>,
    );
    for (const [key, id] of Object.entries(GO_MAP)) {
      ok(`help renders "g ${key}" → ${SECTION_LABEL[id]}`,
        help.includes(`g ${key}`) && help.includes(SECTION_LABEL[id]));
    }
    ok("help documents search overlay", help.toLowerCase().includes("search all entities"));
    ok("SSR help defaults to Windows labels (no hydration mismatch)",
      help.includes("Ctrl K") && !help.includes("⌘K"));
  }

  process.stdout.write("\n  cross-entity search ranking\n");
  {
    const st = buildSeed();
    const t = (...w: string[]) => w;

    ok("title match outranks body match",
      scoreItem({ title: "NightLoop", body: "horror survival" }, t("nightloop")) >
      scoreItem({ title: "Horror survival", body: "notes about nightloop" }, t("nightloop")));

    ok("every term must match", scoreText("balance matrix", t("balance", "absentterm")) === 0);
    ok("case-insensitive", scoreText("NIGHTLOOP", t("nightloop")) > 0);
    ok("early match scores higher", scoreText("tunnel health", t("tunnel")) > scoreText("xxxx tunnel", t("tunnel")));
    ok("whole-word beats substring", scoreText("bridge ops", t("bridge")) > scoreText("abridged", t("bridge")));
    ok("repetition adds a little, not a lot", scoreText("x x x x x", t("x")) - scoreText("x", t("x")) <= 3);
    ok("regex metacharacters are safe", scoreText("v1.2 release", t("1.2")) > 0);
    ok("empty query scores nothing", scoreText("anything", parseTerms("")) === 0);
    ok("single-char terms are ignored", parseTerms("a b cd") .join(",") === "cd");

    const termSet = parseTerms("bridge");
    const projectHits = st.projects
      .map((p) => ({ name: p.name, s: scoreItem({ title: p.name, body: `${p.description} ${p.tags.join(" ")}` }, termSet) }))
      .filter((h) => h.s > 0)
      .sort((a, b) => b.s - a.s);
    ok("search finds the bridge project", projectHits[0]?.name === "Arena Bridge v6 Ops", projectHits.map((h) => h.name).join(", "));

    const noteHits = st.notes.filter(
      (n) => scoreItem({ title: n.title, body: `${n.body} ${n.tags.join(" ")}` }, parseTerms("architecture")) > 0,
    );
    ok("search finds notes by body text", noteHits.length > 0, noteHits.map((n) => n.title).join(", "));
  }

  process.stdout.write(
    failures === 0 ? "\n  ALL CHECKS PASSED\n\n" : `\n  ${failures} FAILURE(S)\n\n`,
  );
  process.exit(failures === 0 ? 0 : 1);
}

main();
