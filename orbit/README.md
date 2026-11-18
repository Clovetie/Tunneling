# ORBIT — Personal AI Command Center

> One interface. Multiple capabilities. Persistent context. Controlled autonomy.

ORBIT is a desktop-first personal operating system for your digital life: a natural-language
assistant that routes real work through a permissioned tool layer, a project and task
environment, a calendar, a local knowledge vault, a workflow engine, and a multi-agent
orchestrator — in one persistent shell.

It is **not** a chat window with mock buttons. Requests are classified by intent against live
state, executed through typed tools, and reported only when a tool confirms the result.

```bash
cd orbit
npm install
npm run dev        # http://localhost:3000
npm test           # typecheck + headless render/invariant suite
npm run build      # production build
```

---

## 1. What is actually implemented

| Area | State | Where |
|---|---|---|
| Persistent app shell (sidebar, top bar, resizable right panel, command palette, shortcuts) | working | `components/shell/` |
| Home: overview, intelligence feed, quick actions, grounded daily briefing | working, derived from state | `components/sections/HomeSection.tsx`, `lib/briefing.ts` |
| AI Assistant: 6 modes, streaming, conversation history, folders, pin/rename/archive, context controls, attachments | working | `components/sections/AssistantSection.tsx`, `lib/engine.ts` |
| Tool system: id, schema, permission, approval gate, validation, audit log | working, enforced | `lib/tools.ts`, `lib/seed.ts` (`tools`) |
| Agent system: Supervisor + 7 specialists, handoffs, shared task context, review | working | `lib/seed.ts` (`agents`), `components/sections/assistant/AgentDock.tsx` |
| Projects: objectives, status, priority, milestones, task breakdown, archive/delete | working | `components/sections/ProjectsSection.tsx` |
| Tasks: kanban with drag-and-drop, list view, detail drawer, filters | working | `components/sections/TasksSection.tsx` |
| Calendar: week grid, event kinds, deadlines, reminders | working | `components/sections/CalendarSection.tsx` |
| Knowledge: collections, tagging, full-text search, note detail | working | `components/sections/KnowledgeSection.tsx` |
| Files: index, collections, analysis, per-file summaries | working | `components/sections/FilesSection.tsx` |
| Automations: triggers, step graphs, run history, run logs, enable/disable | working | `components/sections/AutomationsSection.tsx` |
| Integrations: connection state, scopes, error surfacing, connect/disconnect | working | `components/sections/IntegrationsSection.tsx` |
| Activity: audit trail with filters and JSON export | working | `components/sections/ActivitySection.tsx` |
| Settings: profile, autonomy switches, agent roster, tool registry, appearance, data | working | `components/sections/SettingsSection.tsx` |
| Persistence | `localStorage`, versioned seed, export/reset | `lib/store.tsx` |

### Deliberately honest boundaries

- **No model provider is wired in.** The reasoning layer (`lib/engine.ts`) is a deterministic,
  grounded planner: it classifies intent, reads real state, emits real tool calls, and writes prose
  around *verified* results. Swapping in an LLM means replacing `planFor()` and `compose()` — the
  tool layer, permission gate, validation and audit do not change.
- **Files are indexed by metadata, not bytes.** `files.analyze` records structure and tags; it does
  not pretend to read a PDF's contents.
- **No provider credentials exist in the client.** Integration state is a local registry; connecting
  an app flips its state and logs the scopes granted.

---

## 2. The four rules the app enforces

1. **Grounded briefings.** Every line in the briefing and the intelligence feed is computed from
   state. If the calendar integration is not connected, ORBIT says it cannot confirm the schedule
   and marks the briefing `partial data`. It never invents an event.
2. **Verified success only.** A tool call returns `{ok, result, verified}`. `verified` requires the
   tool's own validator to accept its result string. Prose is composed *after* that, and a failed
   call is reported as a failure with the raw result attached (`lib/tools.ts`).
3. **Controlled autonomy.** `permission` is `read | write | execute | external`; every
   user-facing mutating tool is `requiresApproval: true`. With **Confirm all writes** on (default),
   the call parks at the gate, shows you the exact tool and arguments, and applies nothing until you
   approve. Denying writes an audit entry. The one ungated `execute` tool is internal agent
   delegation, and it is documented as such.
4. **Mode boundaries are real.** Chat Mode suppresses writes; Research and Analysis are read-only
   against local state; Execution offers gated writes. This is enforced in `planFor()`, not by
   asking the user nicely.

---

## 3. Reasoning & execution flow

```
prompt + mode
   │
   ├─ planFor(prompt, state, mode)          lib/engine.ts
   │     ├─ intent classification (12 intents + grounded fallback)
   │     ├─ agent assignment (Supervisor → specialist)
   │     ├─ proposed ToolCall[] (schema-checked, permission-tagged)
   │     └─ compose(ctx) → prose + StructuredBlock[] + sources
   │
   ├─ agent runtime: steps animate queued → working → done
   │     Supervisor reviews results before the report is written
   │
   ├─ executeTool(call, state, actions)     lib/tools.ts
   │     ├─ validateArgs  → reject before execution
   │     ├─ approval gate → park if requiresApproval && confirmAllWrites
   │     ├─ mutate state  → real store write
   │     ├─ validate(result) → verified true/false
   │     └─ audit entry   → Activity feed
   │
   └─ compose(results) → prose written only around confirmed outcomes
```

Structured output blocks (`table`, `plan`, `checklist`, `findings`, `chart`, `code`) are rendered as
real components, not markdown tables.

---

## 4. Design language

Dark-first, near-black surfaces (`#07080a` → `#0e1116`), a single restrained accent (switchable:
indigo / mint / violet / amber), hairline borders, tabular numerals for all metrics, and motion
limited to 150–280ms easing with a full `prefers-reduced-motion` and in-app override. No gradients
on text, no glow, no "AI dashboard" chrome. Dense by default: 11–13px UI type, 4px spacing rhythm,
information-forward cards.

Keyboard: `⌘K` palette, `/` palette, `\` sidebar, `]` right panel, `?` help, `⌘1–6` mode switch,
`⌘↵` send, `g h/a/p/t/c` navigation.

---

## 5. Testing

`npm test` runs two layers:

- `tsc --noEmit` across the app.
- `scripts/smoke.tsx` — headless `react-dom/server` render of **every** section and shell inside
  jsdom (a blank panel fails the build), then hard invariants:
  - briefing is grounded and timezone-correct (`Asia/Bangkok`), and never fabricates events
  - feed ids are derived, never invented
  - every tool declares a schema; every user-facing mutating tool is gated
  - 8 prompt → intent routing assertions
  - all 8 intents compose non-empty output across all 6 modes
  - Chat suppresses writes, Research is read-only, Execution offers the gated write
  - tool execution: valid write verifies **and** mutates state; missing args, inverted time ranges
    and unknown ids all fail loudly instead of claiming success

Sample output:

```
  section renders
  ok   render home           42069 bytes
  ...
  intent routing
  ok   route "Organize everything I need to work on "  plan-week (want plan-week)
  tool execution + verification
  ok   valid write succeeds and verifies           created tsk_smoke "Rotate the bridge token"
  ok   unknown id fails instead of claiming success No task with id tsk_nope
  ALL CHECKS PASSED
```

---

## 6. Layout

```
orbit/
├── app/
│   ├── globals.css        design tokens, motion primitives, tailwind v4 theme
│   ├── layout.tsx         metadata, viewport
│   └── page.tsx           → AppShell
├── components/
│   ├── shell/             AppShell, Sidebar, TopBar, RightPanel, CommandPalette, QuickCreate, KeyboardHelp
│   ├── sections/          one file per section; Section roster in Sections.tsx
│   │   └── assistant/     MessageBody, ToolTrace, AgentTrace, AgentDock
│   └── ui/kit.tsx         Button, Badge, Field, Modal, Progress, Ring, StatusPill, SectionHeader, …
├── lib/
│   ├── types.ts           domain model
│   ├── seed.ts            grounded starting dataset + tool & agent registries
│   ├── store.tsx          reducer, persistence, actions, theming
│   ├── briefing.ts        derived briefing + intelligence feed
│   ├── engine.ts          intent routing, agent planning, composition
│   ├── tools.ts           validation, permissions, execution, audit
│   └── util.ts            timezone-correct time math, formatting
└── scripts/smoke.tsx      headless render + invariant suite
```

Time handling is timezone-correct on purpose: `atLocalHour()` resolves wall-clock times in
`Asia/Bangkok` with a two-pass DST-safe offset, so a sandbox running in UTC still schedules
"20:45 build block" at 20:45 local. This was a real bug caught by the invariant suite.
