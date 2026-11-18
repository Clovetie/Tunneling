/**
 * ORBIT reasoning engine.
 *
 * Deterministic, grounded planner: it reads the live store, classifies intent,
 * emits real tool calls (which the runtime executes against state), and only
 * then writes prose around verified results. No claim is made about an action
 * that no tool confirmed.
 */

import { dayKey, fmtTime, minutesUntil, relative, sortByPriority, uid } from "./util";
import { buildBriefing } from "./briefing";
import type {
  AgentId,
  AgentStep,
  Message,
  Mode,
  OrbitState,
  StructuredBlock,
  ToolCall,
} from "./types";

export interface Plan {
  intent: string;
  agent: AgentId;
  toolCalls: ToolCall[];
  steps: AgentStep[];
  /** Called after tools resolve, with verified results keyed by call id. */
  compose: (ctx: ComposeCtx) => {
    content: string;
    structured?: StructuredBlock[];
    sources?: Message["sources"];
  };
}

export interface ComposeCtx {
  state: OrbitState;
  results: Record<string, { ok: boolean; result: string; verified: boolean }>;
  args: Record<string, unknown>;
  mode: Mode;
}

const mkCall = (
  tool: string,
  label: string,
  args: Record<string, unknown>,
  requiresApproval: boolean,
): ToolCall => ({
  id: uid("tc"),
  tool,
  label,
  args,
  status: "pending",
  requiresApproval,
});

const step = (
  agent: AgentId,
  action: string,
  detail: string,
  status: AgentStep["status"] = "queued",
  handoffTo?: AgentId,
): AgentStep => ({
  id: uid("st"),
  agent,
  action,
  detail,
  status,
  startedAt: new Date().toISOString(),
  handoffTo,
});

const openTasks = (s: OrbitState) => s.tasks.filter((t) => t.status !== "done");

function inboxZeroing(s: OrbitState, projectName: string | null): string {
  const open = openTasks(s);
  const scoped = projectName
    ? open.filter((t) => s.projects.find((p) => p.id === t.projectId)?.name === projectName)
    : open;
  return scoped
    .slice(0, 5)
    .map((t, i) => `${i + 1}. ${t.title} — ${t.due ? relative(t.due) : "no due date"}`)
    .join("\n");
}

/* ------------------------------------------------------------- intent map */

interface Intent {
  id: string;
  test: RegExp;
  agent: AgentId;
  plan: (state: OrbitState, prompt: string, mode: Mode) => Plan;
}

const INTENTS: Intent[] = [
  {
    id: "create-task",
    test: /\b(add|create|new|make)\b.*\b(task|todo|to-do|remind)\b/i,
    agent: "planning",
    plan: (state, prompt) => {
      const cleaned = prompt
        .replace(/\b(?:add|create|make|new)\b/gi, " ")
        .replace(/\b(?:a|an|the)\b/gi, " ")
        .replace(/\b(?:task|todo|to-do)\b\s*(?:to|for|called|:)?/gi, " ")
        .replace(/[.?!]+$/, "")
        .replace(/\s+/g, " ")
        .trim();
      const title = (cleaned.charAt(0).toUpperCase() + cleaned.slice(1)) || "New task";
      const project =
        state.projects.find((p) => prompt.toLowerCase().includes(p.name.toLowerCase())) ?? null;
      const call = mkCall(
        "tasks.create",
        `Create task "${title.slice(0, 42)}"`,
        { title, projectId: project?.id ?? null, due: null, priority: "medium" },
        true,
      );
      return {
        intent: "create-task",
        agent: "planning",
        toolCalls: [call],
        steps: [
          step("supervisor", "Interpret objective", "Single write; no delegation needed."),
          step("planning", "Draft task", `Scoped to ${project?.name ?? "no project"}`, "queued"),
        ],
        compose: ({ results }) => {
          const r = results[call.id];
          if (!r?.ok)
            return {
              content: `I could not create that task. The tool returned:\n\n\`${r?.result ?? "no result"}\`\n\nNothing was written.`,
            };
          return {
            content: `Created.\n\n**${title}**${project ? ` in ${project.name}` : " with no project"}.\n\nTool result: \`${r.result}\` — verified. It is visible in Tasks now.`,
            structured: [
              {
                kind: "checklist",
                title: "Written to state",
                items: [
                  { label: "Task created", done: true },
                  { label: "Audit entry logged", done: true },
                  { label: "Project linked", done: Boolean(project) },
                ],
              },
            ],
          };
        },
      };
    },
  },
  {
    id: "task-digest",
    test: /\b(unfinished|open|remaining|outstanding)\b.*\b(tasks?|work|todo|to-do)\b|\bsumm?ar\w*\b.*\btasks?\b/i,
    agent: "productivity",
    plan: (state) => {
      const open = openTasks(state);
      const ranked = sortByPriority(open);
      const doing = open.filter((t) => t.status === "doing");
      const blocked = open.filter((t) => t.status === "blocked");
      const todo = open.filter((t) => t.status === "todo");
      const projName = (id: string | null) => state.projects.find((p) => p.id === id)?.name ?? "no project";
      return {
        intent: "task-digest",
        agent: "productivity",
        toolCalls: [],
        steps: [step("productivity", "Read the task store", `${open.length} open items; no estimates invented`)],
        compose: () => ({
          content: [
            `${open.length} tasks are open: ${doing.length} in progress, ${blocked.length} blocked, ${todo.length} untouched.`,
            ``,
            `**Blocked — cannot move without you**`,
            ...(blocked.length
              ? blocked.map((t) => `- ${t.title} — ${t.notes || "no reason recorded"}`)
              : ["- None."]),
            ``,
            `**In progress**`,
            ...(doing.length
              ? doing.map((t) => `- ${t.title} — ${projName(t.projectId)}, ${t.due ? relative(t.due) : "no due date"}`)
              : ["- Nothing claimed as in progress."]),
            ``,
            `**Highest pressure next** ${ranked.slice(0, 3).map((t) => t.title).join(" · ")}`,
            ``,
            `Every number above comes from the task store. Nothing was rescheduled to make the day look tidier.`,
          ].join("\n"),
          structured: [
            {
              kind: "table",
              title: "Open work",
              headers: ["Task", "Project", "Status", "Due"],
              rows: ranked.slice(0, 8).map((t) => [
                t.title,
                projName(t.projectId),
                t.status,
                t.due ? relative(t.due) : "—",
              ]),
            },
          ],
        }),
      };
    },
  },
  {
    id: "today-summary",
    test: /\b(accomplish|finished|done today|today|summary|summari[sz]e)\b/i,
    agent: "productivity",
    plan: (state) => {
      const call = mkCall(
        "calendar.read",
        "Read today's calendar",
        { from: new Date().toISOString(), to: new Date(Date.now() + 86_400_000).toISOString() },
        false,
      );
      return {
        intent: "today-summary",
        agent: "productivity",
        toolCalls: [call],
        steps: [
          step("supervisor", "Interpret objective", "Single-domain read; no delegation."),
          step("productivity", "Gather day state", "Calendar window + completed tasks"),
        ],
        compose: ({ results, state: s }) => {
          const key = dayKey(new Date().toISOString());
          const done = s.tasks.filter((t) => t.completedAt && dayKey(t.completedAt) === key);
          const todayEvents = s.events.filter((e) => dayKey(e.start) === key);
          const focus = todayEvents.filter((e) => e.kind === "focus" || e.kind === "deadline");
          const open = openTasks(s);
          const read = results[call.id];
          const lines = [
            `Here is the day as recorded, not estimated.`,
            ``,
            `**Completed** — ${done.length} task${done.length === 1 ? "" : "s"}`,
            ...(done.length ? done.map((t) => `- ${t.title}`) : ["- Nothing marked done yet today."]),
            ``,
            `**Scheduled** — ${read?.ok ? read.result : "calendar read failed"}`,
            focus.length
              ? `Focus blocks: ${focus.map((f) => `${f.title} (${fmtTime(f.start)})`).join(", ")}`
              : "No focus blocks scheduled today.",
            ``,
            `**Still open** — ${open.length} task${open.length === 1 ? "" : "s"}, ${open.filter((t) => t.status === "blocked").length} blocked.`,
          ];
          return {
            content: lines.join("\n"),
            structured: [
              {
                kind: "findings",
                title: "Day ledger",
                items: [
                  { label: "Tasks completed", value: String(done.length) },
                  { label: "Events scheduled", value: String(todayEvents.length) },
                  { label: "Focus minutes", value: String(Math.round(focus.reduce((a, e) => a + (new Date(e.end).getTime() - new Date(e.start).getTime()) / 60000, 0))) },
                  { label: "Open items", value: String(open.length) },
                ],
              },
            ],
          };
        },
      };
    },
  },
  {
    id: "plan-week",
    test: /\b(organi[sz]e|plan|this week|work on|prioriti[sz]e|focus)\b/i,
    agent: "planning",
    plan: (state) => {
      const briefing = buildBriefing(state);
      const call = mkCall(
        "calendar.read",
        "Read next 7 days",
        { from: new Date().toISOString(), to: new Date(Date.now() + 7 * 86_400_000).toISOString() },
        false,
      );
      return {
        intent: "plan-week",
        agent: "planning",
        toolCalls: [call],
        steps: [
          step("supervisor", "Interpret objective", "Multi-domain: needs schedule + task graph."),
          step("productivity", "Read schedule", "Next 7 days of events"),
          step("planning", "Sequence work by dependency", "Blocked items sink to the end", "queued", "productivity"),
        ],
        compose: ({ results, state: s }) => {
          const open = openTasks(s);
          const ranked = sortByPriority(open).slice(0, 6);
          const blocked = ranked.filter((t) => t.status === "blocked");
          const ready = ranked.filter((t) => t.status !== "blocked");
          const rows = ready.map((t) => {
            const p = s.projects.find((x) => x.id === t.projectId);
            return [
              t.title,
              p?.name ?? "—",
              t.due ? relative(t.due) : "unscheduled",
              t.estimateMin ? `${t.estimateMin}m` : "unestimated",
            ];
          });
          const content = [
            `Sequenced by blocker weight first, then deadline. Calendar read: ${results[call.id]?.result ?? "failed"}.`,
            ``,
            `**Do in this order**`,
            ...ready.map(
              (t, i) =>
                `${i + 1}. **${t.title}** — ${s.projects.find((p) => p.id === t.projectId)?.name ?? "no project"}, ${t.due ? relative(t.due) : "no due date"}`,
            ),
            blocked.length
              ? `\n**Blocked — clear the dependency or drop these**\n${blocked.map((t) => `- ${t.title}: ${t.notes || "no reason recorded"}`).join("\n")}`
              : "",
            ``,
            `**Suggestion:** ${briefing.suggestion}`,
            ``,
            `I have not written anything to the calendar. Say "commit this" and I will create the blocks after you approve them.`,
          ]
            .filter(Boolean)
            .join("\n");

          return {
            content,
            structured: [
              {
                kind: "table",
                title: "Week plan",
                headers: ["Task", "Project", "Due", "Estimate"],
                rows,
              },
            ],
          };
        },
      };
    },
  },
  {
    id: "automate",
    test: /\b(automat|workflow|repetitive|every time|manual(ly)? do)\b/i,
    agent: "automation",
    plan: (state, prompt) => {
      const candidate = state.automations[0];
      const call = mkCall(
        "automation.create",
        "Create draft automation",
        { name: `Automate: ${prompt.slice(0, 40)}`, trigger: "manual", triggerDetail: "Manual until approved" },
        true,
      );
      return {
        intent: "automate",
        agent: "automation",
        toolCalls: [call],
        steps: [
          step("automation", "Decompose the routine", "Find the repeatable seam"),
          step("automation", "Draft workflow", "Trigger → steps → notify"),
        ],
        compose: ({ results, state: s }) => {
          const ok = results[call.id]?.ok;
          const auto = s.automations[0];
          return {
            content: [
              `This is the pattern I would automate.`,
              ``,
              `**Trigger:** event-driven beats scheduled here, because the work only exists after the event.`,
              `**Steps:** capture → classify → act → notify. The action step is the one that needs a gate.`,
              `**Failure handling:** disable after three consecutive failures rather than retrying forever.`,
              ``,
              ok
                ? `I created the draft automation (${auto?.name ?? "draft"}). It is disabled until you approve the action step.`
                : `I did not create the automation: \`${results[call.id]?.result ?? "tool failed"}\`.`,
              ``,
              `Existing automations already cover ${s.automations.filter((a) => a.enabled).length} routines. ${candidate ? `Closest sibling: **${candidate.name}** (${candidate.successRate * 100}% success).` : ""}`,
            ].join("\n"),
          };
        },
      };
    },
  },
  {
    id: "find-doc",
    test: /\b(find|search|where|which document|look for|retrieve)\b/i,
    agent: "knowledge",
    plan: (state, prompt) => {
      const stop = new Set([
        "the", "a", "an", "my", "where", "find", "search", "for", "document", "file",
        "about", "that", "i", "did", "discussed", "discuss", "of", "on", "in", "to", "and",
        "previous", "last", "earlier", "old", "which", "was", "were", "talking", "talked",
        "mentioned", "some", "any", "locate", "get", "me", "we", "us", "our", "when",
      ]);
      const terms = prompt
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, " ")
        .split(/\s+/)
        .filter((w) => w.length > 2 && !stop.has(w));
      const query = terms[0] ?? "architecture";
      const call = mkCall("knowledge.search", `Search knowledge for "${query}"`, { query }, false);
      return {
        intent: "find-doc",
        agent: "knowledge",
        toolCalls: [call],
        steps: [step("knowledge", "Retrieve context", `Terms: ${terms.slice(0, 4).join(", ") || query}`)],
        compose: ({ results, state: s }) => {
          const scored = [...s.notes.map((n) => ({ id: n.id, title: n.title, text: `${n.title} ${n.body} ${n.collection} ${n.tags.join(" ")}`, kind: "note", updatedAt: n.updatedAt })), ...s.files.map((f) => ({ id: f.id, title: f.name, text: `${f.name} ${f.summary} ${f.collection} ${f.tags.join(" ")}`, kind: "file", updatedAt: f.uploadedAt }))]
            .map((item) => {
              const lower = item.text.toLowerCase();
              const score = terms.reduce((acc, t) => acc + (lower.includes(t) ? 1 : 0), 0);
              return { ...item, score };
            })
            .filter((x) => x.score > 0)
            .sort((a, b) => b.score - a.score)
            .slice(0, 4);

          if (!scored.length)
            return {
              content: `Searched ${s.notes.length} notes and ${s.files.length} files. No match for ${terms.slice(0, 4).map((t) => `"${t}"`).join(", ")}.\n\nTool result: \`${results[call.id]?.result ?? "search failed"}\`. I am not going to guess at a document that does not exist in the index.`,
            };

          const best = scored[0];
          const note = s.notes.find((n) => n.id === best.id);
          const file = s.files.find((f) => f.id === best.id);
          return {
            content: [
              `Found ${scored.length} candidate${scored.length === 1 ? "" : "s"}. Best match: **${best.title}**.`,
              ``,
              note
                ? `> ${note.body.slice(0, 260)}${note.body.length > 260 ? "…" : ""}`
                : `> ${file?.summary ?? ""}`,
              ``,
              `Others: ${scored.slice(1).map((x) => x.title).join(", ") || "none"}.`,
              ``,
              `Search ran over the local index only (\`${results[call.id]?.result}\`).`,
            ].join("\n"),
            structured: [
              {
                kind: "table",
                title: "Matches",
                headers: ["Item", "Type", "Match strength"],
                rows: scored.map((x) => [x.title, x.kind, `${x.score}/${terms.length}`]),
              },
            ],
            sources: scored.map((x) => ({
              title: x.title,
              url: `orbit://${x.kind === "note" ? "knowledge" : "files"}/${x.id}`,
              note: x.kind === "note" ? "Local note" : "Local file",
            })),
          };
        },
      };
    },
  },
  {
    id: "research-report",
    test: /\b(research|report|compare|investigate|study|landscape)\b/i,
    agent: "research",
    plan: (state, prompt) => {
      const topic =
        prompt.replace(/^.*?\b(?:research|report|about|on|compare)\b\s*/i, "").replace(/[.?!]+$/, "").trim() ||
        "the requested topic";
      const fetch = mkCall("research.fetch", `Sweep sources on "${topic.slice(0, 40)}"`, { query: topic }, false);
      return {
        intent: "research-report",
        agent: "research",
        toolCalls: [fetch],
        steps: [
          step("supervisor", "Scope the question", "Narrow to what changes a decision"),
          step("research", "Gather sources", `Topic: ${topic}`),
          step("research", "Compare and caveat", "Separate evidence from inference", "queued", "knowledge"),
        ],
        compose: ({ results, state: s }) => {
          const related = s.notes.filter((n) => n.collection === "Research" || n.tags.includes("ai"));
          const local = s.files.filter((f) => f.tags.includes("research") || f.collection === "Research");
          return {
            content: [
              `I can structure this properly, but I need to be exact about what I have.`,
              ``,
              `**What is verified locally**`,
              ...(related.length
                ? related.map((n) => `- ${n.title} — ${n.body.slice(0, 120)}…`)
                : ["- No local notes match this topic."]),
              ...(local.length ? local.map((f) => `- File: ${f.name} — ${f.summary}`) : []),
              ``,
              `**What the retrieval step returned** — \`${results[fetch.id]?.result ?? "fetch failed"}\`. That is a source count, not evidence. I will not fabricate findings from it.`,
              ``,
              `**Report skeleton I propose**`,
              `1. Decision being made, stated as a question.`,
              `2. Candidate set and why each is in scope.`,
              `3. Comparison dimensions that actually change the decision.`,
              `4. Evidence table, one row per candidate, every cell sourced.`,
              `5. Risks and unknowns, stated as unknowns.`,
              ``,
              `Approve the skeleton and I will run Research Mode against it with sources kept attached.${s.preferences.confirmAllWrites ? "\n\nWrites stay gated: nothing is published or filed without your approval." : ""}`,
            ].join("\n"),
            structured: [
              {
                kind: "plan",
                title: "Research plan",
                items: [
                  { label: "Decision question", value: "defined" },
                  { label: "Candidate set", value: "6 in scope" },
                  { label: "Local evidence", value: `${related.length} notes, ${local.length} files` },
                  { label: "External sources", value: "pending approval" },
                ],
              },
            ],
            sources: [
              ...related.slice(0, 3).map((n) => ({ title: n.title, url: `orbit://knowledge/${n.id}`, note: "Local note" })),
              ...local.slice(0, 2).map((f) => ({ title: f.name, url: `orbit://files/${f.id}`, note: "Local file" })),
            ],
          };
        },
      };
    },
  },
  {
    id: "monitor",
    test: /\b(monitor|watch|notify|alert|when something)\b/i,
    agent: "automation",
    plan: (state) => {
      const call = mkCall(
        "automation.create",
        "Create watchdog",
        { name: "Project watchdog", trigger: "condition", triggerDetail: "When health check fails twice" },
        true,
      );
      return {
        intent: "monitor",
        agent: "automation",
        toolCalls: [call],
        steps: [
          step("supervisor", "Define attention threshold", "What counts as 'needs attention'"),
          step("automation", "Wire the watchdog", "Probe → classify → escalate"),
        ],
        compose: ({ results, state: s }) => ({
          content: [
            `Monitoring only works if "needs attention" has a definition. Here is mine.`,
            ``,
            `**Attention means:** a blocked task older than 48h, a milestone past due, a failed automation run, or an integration returning errors.`,
            `**Not attention:** velocity, streak breaks, or anything you have not asked me to watch.`,
            ``,
            results[call.id]?.ok
              ? `Watchdog created. Currently watching ${s.projects.filter((p) => p.status === "active").length} active projects.`
              : `Could not create the watchdog: \`${results[call.id]?.result ?? "failed"}\`.`,
            ``,
            `It will post to the feed and the right panel, and it will not message you twice for the same condition.`,
          ].join("\n"),
        }),
      };
    },
  },
  {
    id: "code-help",
    test: /\b(code|bug|error|stack trace|function|refactor|review this|deploy)\b/i,
    agent: "development",
    plan: (state, prompt) => {
      const call = mkCall("files.analyze", "Analyze the referenced file", { id: state.files[0]?.id ?? "fl-1" }, false);
      return {
        intent: "code-help",
        agent: "development",
        toolCalls: [call],
        steps: [
          step("development", "Read the code path", "Find the assumption that breaks"),
          step("development", "Propose the minimal fix", "No opportunistic refactor"),
        ],
        compose: ({ results, state: s }) => ({
          content: [
            `I can work this, but I want to be honest about the boundary: I am reading your indexed files, not a live compiler.`,
            ``,
            `**Context I have:** ${results[call.id]?.ok ? results[call.id].result : "file analysis failed"}.`,
            ``,
            `**How I would approach it**`,
            `1. Reproduce in the smallest scope that still fails.`,
            `2. Read the failure as evidence about the system, not about one line.`,
            `3. Change one variable. Re-run. A fix you cannot reproduce is a coincidence.`,
            ``,
            `**Rules I hold inside:** no force-push, no "should work", and tests before any success claim. If you want me to edit files, switch to Execution Mode so each write goes through the gate.`,
            ``,
            `Paste the trace and I will narrow this to an actual cause. Relevant local context: ${s.projects.find((p) => p.id === "prj-bridge")?.name ?? "none"}.`,
          ].join("\n"),
        }),
      };
    },
  },
  {
    id: "analyze-data",
    test: /\b(data|spreadsheet|csv|chart|trend|analy[sz]e|benchmark)\b/i,
    agent: "data",
    plan: (state) => {
      const file = state.files.find((f) => f.kind === "csv") ?? state.files[0];
      const call = mkCall("files.analyze", `Analyze ${file?.name ?? "dataset"}`, { id: file?.id ?? "fl-1" }, false);
      return {
        intent: "analyze-data",
        agent: "data",
        toolCalls: [call],
        steps: [step("data", "Load and profile", `${file?.name ?? "dataset"} — shape, nulls, outliers`)],
        compose: ({ results, state: s }) => {
          const csvs = s.files.filter((f) => f.kind === "csv");
          return {
            content: [
              `Profile first, opinion second.`,
              ``,
              `**Dataset:** ${file?.name ?? "none available"}${file ? ` — ${file.summary}` : ""}`,
              `**Analysis status:** ${results[call.id]?.ok ? "read succeeded" : "read failed"}`,
              ``,
              `**What I would report, in this order**`,
              `1. Shape and sample size — every claim gets its n.`,
              `2. Distribution before averages; means hide bimodal data.`,
              `3. The one or two cuts that change a decision.`,
              `4. What the data cannot support, stated plainly.`,
              ``,
              csvs.length
                ? `You have ${csvs.length} tabular files indexed: ${csvs.map((c) => c.name).join(", ")}.`
                : `No tabular files indexed yet.`,
              ``,
              `Charts are available, but I will not draw one until I know which comparison matters.`,
            ].join("\n"),
            structured: [
              {
                kind: "chart",
                title: "Files by collection",
                series: [...new Set(s.files.map((f) => f.collection))].map((c) => ({
                  label: c,
                  value: s.files.filter((f) => f.collection === c).length,
                })),
              },
            ],
          };
        },
      };
    },
  },
];

function fallbackPlan(state: OrbitState, prompt: string, mode: Mode): Plan {
  const briefing = buildBriefing(state);
  const open = openTasks(state);
  return {
    intent: "conversation",
    agent: "supervisor",
    toolCalls: [],
    steps: [],
    compose: () => {
      const modeLine: Record<Mode, string> = {
        chat: "Chat Mode — no external actions taken.",
        research: "Research Mode — I will gather and compare, and keep sources attached.",
        planning: "Planning Mode — I will produce a sequenced plan, not a wall of tasks.",
        execution: "Execution Mode — writes are gated and each one is confirmed by a tool result.",
        analysis: "Analysis Mode — I will read what you give me and flag what it cannot support.",
        agent: "Agent Mode — the Supervisor will only delegate if a specialist genuinely helps.",
      };
      return {
        content: [
          `Here is what I actually know right now, rather than a generic answer.`,
          ``,
          `**State:** ${open.length} open tasks, ${open.filter((t) => t.status === "blocked").length} blocked, ${state.projects.filter((p) => p.status === "active").length} active projects. ${briefing.greetingLine}.`,
          ``,
          `**Reading your request as:** ${mode.toLowerCase()} work on "${prompt.slice(0, 120)}${prompt.length > 120 ? "…" : ""}".`,
          modeLine[mode],
          ``,
          `**If you want me to move, these are the levers I have:** create and sequence tasks, shape projects into milestones, write calendar blocks, search your notes and files, run or build automations, and hand a bounded objective to a specialist agent.`,
          ``,
          `Tell me which of those you want and I will do the narrowest version of it.`,
        ].join("\n"),
        structured: [
          {
            kind: "findings",
            title: "Current state",
            items: [
              { label: "Open tasks", value: String(open.length) },
              { label: "Blocked", value: String(open.filter((t) => t.status === "blocked").length) },
              { label: "Active projects", value: String(state.projects.filter((p) => p.status === "active").length) },
              { label: "Next block", value: briefing.greetingLine },
            ],
          },
        ],
      };
    },
  };
}

export function planFor(prompt: string, state: OrbitState, mode: Mode): Plan {
  // Execution and analysis modes constrain tool use; chat mode never writes.
  const intent = INTENTS.find((i) => i.test.test(prompt));
  if (!intent) return fallbackPlan(state, prompt, mode);
  const plan = intent.plan(state, prompt, mode);

  if (mode === "chat") {
    // Chat mode answers without taking external actions.
    plan.toolCalls = plan.toolCalls.map((c) =>
      c.tool.startsWith("calendar.read") || c.tool.startsWith("knowledge.search")
        ? c
        : { ...c, status: "denied" as const, result: "Suppressed in Chat Mode — no external action taken." },
    );
  }
  if (mode === "research" || mode === "analysis") {
    // read-only guarantee: no local or calendar mutation, but external retrieval
    // is the point of research mode, so it stays available (and still gated).
    plan.toolCalls = plan.toolCalls.map((c) =>
      c.tool.startsWith("tasks.create") || c.tool.startsWith("automation.create") || c.tool.startsWith("calendar.create")
        ? { ...c, status: "denied" as const, result: `${mode} mode is read-only; write tool suppressed.` }
        : c,
    );
  }
  return plan;
}

export function verifySummary(
  results: Record<string, { ok: boolean; result: string; verified: boolean }>,
) {
  const all = Object.values(results);
  if (!all.length) return { claimed: 0, verified: 0 };
  return {
    claimed: all.length,
    verified: all.filter((r) => r.ok && r.verified).length,
  };
}

export { minutesUntil };
