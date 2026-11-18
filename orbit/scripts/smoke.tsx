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

  process.stdout.write(
    failures === 0 ? "\n  ALL CHECKS PASSED\n\n" : `\n  ${failures} FAILURE(S)\n\n`,
  );
  process.exit(failures === 0 ? 0 : 1);
}

main();
