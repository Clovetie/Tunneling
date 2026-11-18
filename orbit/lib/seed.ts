import {
  atLocalHour,
  daysFromNow,
  hoursFromNow,
  iso,
  uid,
} from "./util";
import type {
  AgentDefinition,
  Automation,
  ActivityEntry,
  CalendarEvent,
  Conversation,
  FeedItem,
  Identity,
  Integration,
  KnowledgeNote,
  OrbitFile,
  OrbitState,
  Preferences,
  Project,
  Reminder,
  Task,
  ToolDefinition,
} from "./types";

export const SEED_VERSION = 7;

export const identity: Identity = {
  name: "Clove",
  handle: "@clovetie",
  role: "Builder · Roblox systems & AI tooling",
  timezone: "Asia/Bangkok",
  locale: "en-GB",
  avatarInitials: "CL",
};

export const defaultPreferences: Preferences = {
  displayName: "Clove",
  timezone: "Asia/Bangkok",
  autoApproveReads: true,
  confirmAllWrites: true,
  agentVerbosity: "balanced",
  reducedMotion: false,
  accent: "indigo",
  weekStart: "monday",
  sidebarCollapsed: false,
  rightPanelOpen: true,
  rightPanelWidth: 336,
};

const T = (n: number) => daysFromNow(n);

/* --------------------------------------------------------------- projects */

export const projects: Project[] = [
  {
    id: "prj-nightloop",
    name: "NightLoop",
    description:
      "Horror survival experience: eight entities, six composable night modifiers, and a tunnel-safe playtest loop.",
    objectives: [
      "Ship entity AI that stays readable under low light",
      "Prove modifiers compose without economy collapse",
      "Close the playtest loop to under 40s",
    ],
    status: "active",
    priority: "critical",
    color: "#a78bfa",
    start: T(-34),
    target: T(19),
    progress: 68,
    milestones: [
      { id: "ms-n1", title: "Entity behaviour pass", due: T(-4), done: true },
      { id: "ms-n2", title: "Modifier balance matrix", due: T(3), done: false },
      { id: "ms-n3", title: "Playtest telemetry", due: T(9), done: false },
      { id: "ms-n4", title: "Release candidate", due: T(19), done: false },
    ],
    pinned: true,
    tags: ["game", "release"],
    lead: "Development Agent",
    createdAt: T(-34),
    updatedAt: hoursFromNow(-3),
  },
  {
    id: "prj-bridge",
    name: "Arena Bridge v6 Ops",
    description:
      "Dual-era MCP bridge, supervised tunnels, multi-agent bus and the Studio plugin lifecycle. Currently hardening the operator surface.",
    objectives: [
      "Zero-ghost-peer playtest lifecycle",
      "Tunnel fallback under 2s to first health probe",
      "Every write tool behind the confirm gate",
    ],
    status: "active",
    priority: "high",
    color: "#7c9cff",
    start: T(-51),
    target: T(7),
    progress: 82,
    milestones: [
      { id: "ms-b1", title: "SEP-1649 discovery", due: T(-12), done: true },
      { id: "ms-b2", title: "MRTR confirm gate", due: T(-5), done: true },
      { id: "ms-b3", title: "Ghost-peer eviction audit", due: T(2), done: false },
    ],
    pinned: true,
    tags: ["infra", "mcp"],
    lead: "Development Agent",
    createdAt: T(-51),
    updatedAt: hoursFromNow(-1),
  },
  {
    id: "prj-kit",
    name: "Roblox Kit Vault",
    description:
      "4,320 hash-verified commercial-safe assets, 28 Luau modules and 22 drop-in systems with licence attestation.",
    objectives: [
      "Keep verification green on every commit",
      "Document pack-license vs content-hash provenance",
    ],
    status: "active",
    priority: "medium",
    color: "#4ade9b",
    start: T(-20),
    target: T(12),
    progress: 54,
    milestones: [
      { id: "ms-k1", title: "Registry re-hash", due: T(-2), done: true },
      { id: "ms-k2", title: "Contact sheet regen", due: T(5), done: false },
    ],
    pinned: false,
    tags: ["assets", "licensing"],
    lead: "Knowledge Agent",
    createdAt: T(-20),
    updatedAt: hoursFromNow(-26),
  },
  {
    id: "prj-chess",
    name: "Chess Rating Push",
    description:
      "Structured weekday study block plus daily tactics. Target +150 Elo before the next tournament.",
    objectives: ["Daily tactics streak", "Two annotated games per week", "Openings audit"],
    status: "active",
    priority: "medium",
    color: "#f5b556",
    start: T(-60),
    target: T(45),
    progress: 41,
    milestones: [
      { id: "ms-c1", title: "30-day streak", due: T(11), done: false },
      { id: "ms-c2", title: "Ruy Lopez repertoire", due: T(30), done: false },
    ],
    pinned: false,
    tags: ["personal", "practice"],
    lead: "Productivity Agent",
    createdAt: T(-60),
    updatedAt: hoursFromNow(-8),
  },
  {
    id: "prj-research",
    name: "Local AI Models Report",
    description:
      "Comparative research on locally runnable models: throughput, memory floor, licence risk, and tooling ergonomics.",
    objectives: [
      "Benchmark 6 candidates on the same rig",
      "Separate licence risk from quality",
      "Publish a decision matrix",
    ],
    status: "planning",
    priority: "high",
    color: "#56cfd8",
    start: T(-6),
    target: T(14),
    progress: 22,
    milestones: [
      { id: "ms-r1", title: "Source sweep", due: T(1), done: false },
      { id: "ms-r2", title: "Benchmark harness", due: T(6), done: false },
      { id: "ms-r3", title: "Report draft", due: T(12), done: false },
    ],
    pinned: false,
    tags: ["research", "ai"],
    lead: "Research Agent",
    createdAt: T(-6),
    updatedAt: hoursFromNow(-30),
  },
  {
    id: "prj-writing",
    name: "Field Notes",
    description:
      "Long-form writing about building agent tooling: what actually breaks, and what the fixes cost.",
    objectives: ["Ship one essay per month", "Keep notes searchable"],
    status: "paused",
    priority: "low",
    color: "#8b96a5",
    start: T(-80),
    target: null,
    progress: 35,
    milestones: [{ id: "ms-w1", title: "Essay 03 outline", due: T(20), done: false }],
    pinned: false,
    tags: ["writing"],
    lead: "Knowledge Agent",
    createdAt: T(-80),
    updatedAt: T(-11),
  },
];

/* ------------------------------------------------------------------ tasks */

const task = (
  id: string,
  title: string,
  projectId: string | null,
  status: Task["status"],
  priority: Task["priority"],
  due: string | null,
  estimateMin: number | null,
  tags: string[] = [],
  notes = "",
): Task => ({
  id,
  title,
  notes,
  projectId,
  status,
  priority,
  due,
  estimateMin,
  tags,
  assignee: null,
  createdAt: hoursFromNow(-72),
  completedAt: status === "done" ? hoursFromNow(-2) : null,
});

export const tasks: Task[] = [
  task("tsk-1", "Audit ghost-peer eviction on /api/poll?goodbye=1", "prj-bridge", "doing", "critical", atLocalHour(0, 22, 30), 120, ["bridge", "playtest"]),
  task("tsk-2", "Compose BloodMoon × GlassHouse balance matrix", "prj-nightloop", "doing", "critical", atLocalHour(1, 20, 45), 180, ["balance"]),
  task("tsk-3", "Re-hash registry after contact sheet regen", "prj-kit", "todo", "high", atLocalHour(1, 18, 0), 60, ["licensing"]),
  task("tsk-4", "Collect laundry before 17:00 block", null, "todo", "medium", atLocalHour(0, 17, 0), 15, ["home"]),
  task("tsk-5", "Annotated game #14 (Ruy Lopez, Black)", "prj-chess", "todo", "medium", atLocalHour(1, 15, 10), 45, ["chess"]),
  task("tsk-6", "Source sweep: 6 local model candidates", "prj-research", "blocked", "high", atLocalHour(1, 12, 0), 90, ["research"], "Blocked on benchmark harness skeleton."),
  task("tsk-7", "Confirm gate coverage report for write tools", "prj-bridge", "todo", "high", atLocalHour(2, 20, 45), 90, ["security"]),
  task("tsk-8", "Playtest telemetry schema (entity exposure)", "prj-nightloop", "todo", "medium", atLocalHour(3, 20, 45), 120, ["telemetry"]),
  task("tsk-9", "Write findings skeleton for model report", "prj-research", "todo", "medium", atLocalHour(4, 20, 45), 60, ["writing"]),
  task("tsk-10", "Nightly licence verification passed", "prj-kit", "done", "low", hoursFromNow(-9), 5, ["automation"]),
  task("tsk-11", "Tunnel latency baseline (mcp vs jobs)", "prj-bridge", "done", "medium", hoursFromNow(-20), 30, ["infra"]),
  task("tsk-12", "Tactics streak — 26 days", "prj-chess", "done", "low", hoursFromNow(-26), 15, ["chess"]),
  task("tsk-13", "Field Notes essay 03 outline", "prj-writing", "todo", "low", atLocalHour(9, 21, 0), 60, ["writing"]),
  task("tsk-14", "Rotate bridge token and reinstall plugin", "prj-bridge", "todo", "medium", atLocalHour(5, 11, 0), 20, ["security"]),
  task("tsk-15", "Entity audio pass: Whisperer proximity", "prj-nightloop", "blocked", "high", atLocalHour(2, 15, 30), 90, ["audio"], "Waiting on 16-bit PCM preset round-trip."),
  task("tsk-16", "Weekly review: 12 open tasks, 2 blocked", null, "todo", "medium", atLocalHour(3, 21, 0), 30, ["ritual"]),
];

/* --------------------------------------------------------------- calendar */

const ev = (
  id: string,
  title: string,
  start: string,
  end: string,
  kind: CalendarEvent["kind"],
  projectId: string | null = null,
  source: CalendarEvent["source"] = "google",
  location: string | null = null,
): CalendarEvent => ({ id, title, start, end, kind, location, projectId, source });

function routineDay(day: number, suffix: string): CalendarEvent[] {
  const base = [
    ev(`ev-school-${suffix}`, "School", atLocalHour(day, 8, 20), atLocalHour(day, 15, 10), "routine"),
    ev(`ev-chess-${suffix}`, "Chess and Chess Study", atLocalHour(day, 15, 10), atLocalHour(day, 16, 0), "routine", "prj-chess"),
    ev(`ev-snack-${suffix}`, "4pm snack", atLocalHour(day, 16, 0), atLocalHour(day, 16, 5), "reminder"),
    ev(`ev-play-${suffix}`, "4–6pm play", atLocalHour(day, 16, 0), atLocalHour(day, 18, 0), "routine"),
    ev(`ev-break-${suffix}`, "Break", atLocalHour(day, 18, 0), atLocalHour(day, 19, 0), "routine"),
    ev(`ev-class-${suffix}`, "Online class", atLocalHour(day, 19, 0), atLocalHour(day, 20, 30), "routine"),
    ev(`ev-break2-${suffix}`, "Break", atLocalHour(day, 20, 30), atLocalHour(day, 20, 45), "routine"),
    ev(`ev-work-${suffix}`, "Build block", atLocalHour(day, 20, 45), atLocalHour(day, 22, 15), "focus", "prj-nightloop", "orbit"),
    ev(`ev-break3-${suffix}`, "Wind down", atLocalHour(day, 22, 15), atLocalHour(day, 23, 0), "routine"),
    ev(`ev-sleep-${suffix}`, "Sleep", atLocalHour(day, 23, 0), atLocalHour(day + 1, 7, 0), "routine"),
  ];
  return base;
}

export const events: CalendarEvent[] = [
  ...routineDay(0, "d0"),
  ...routineDay(1, "d1"),
  ...routineDay(2, "d2"),
  // week-ahead extras
  ev("ev-capture", "Laundry pickup window", atLocalHour(0, 17, 0), atLocalHour(0, 17, 10), "deadline"),
  ev("ev-review", "Weekly review", atLocalHour(3, 21, 0), atLocalHour(3, 21, 30), "focus", null, "orbit"),
  ev("ev-deep", "Deep work: modifier matrix", atLocalHour(1, 20, 45), atLocalHour(1, 22, 30), "focus", "prj-nightloop", "orbit"),
  ev("ev-research", "Research block: local models", atLocalHour(2, 20, 45), atLocalHour(2, 22, 15), "focus", "prj-research", "orbit"),
  ev("ev-release", "NightLoop release candidate", atLocalHour(19, 12, 0), atLocalHour(19, 12, 30), "deadline", "prj-nightloop", "orbit"),
  ev("ev-bridge", "Bridge v6 hardening deadline", atLocalHour(7, 18, 0), atLocalHour(7, 18, 30), "deadline", "prj-bridge", "orbit"),
];

export const reminders: Reminder[] = [
  { id: "rem-1", text: "Laundry pickup window closes", at: atLocalHour(0, 17, 0), repeat: "none", done: false },
  { id: "rem-2", text: "Rotate bridge.token (30 days old)", at: atLocalHour(5, 11, 0), repeat: "none", done: false },
  { id: "rem-3", text: "Verify asset licences", at: atLocalHour(1, 9, 0), repeat: "daily", done: false },
  { id: "rem-4", text: "Backup snapshots to cold storage", at: atLocalHour(4, 10, 0), repeat: "weekly", done: false },
];

/* -------------------------------------------------------------- knowledge */

export const notes: KnowledgeNote[] = [
  {
    id: "kn-1",
    title: "Bridge architecture, as-built",
    body:
      "The plugin polls the local server, not the other way round. One job goes to one poller under a lock, so a stalled Studio never eats the queue. Tunnels only expose the bridge outward; nothing upstream is trusted. Writes are gated by a confirmation token minted per call, and the audit log is hash-chained so tampering is detectable after the fact.",
    collection: "Architecture",
    tags: ["mcp", "tunnel", "security"],
    source: "internal",
    linkedProjectId: "prj-bridge",
    updatedAt: hoursFromNow(-30),
    pinned: true,
  },
  {
    id: "kn-2",
    title: "Why ghost peers were hard to kill",
    body:
      "A closed Studio leaves the session row alive until the poll timeout. The fix is not a shorter timeout — that breaks slow playtests. It is an explicit goodbye: the plugin sends goodbye=1 on shutdown, the server evicts immediately, and the timeout stays generous as a backstop.",
    collection: "Architecture",
    tags: ["playtest", "debugging"],
    source: "internal",
    linkedProjectId: "prj-bridge",
    updatedAt: hoursFromNow(-52),
    pinned: false,
  },
  {
    id: "kn-3",
    title: "Horror pacing notes",
    body:
      "Fear decays with exposure. Eight entities is a lot; the roster only works when at most two are legible per night. Modifiers are the real difficulty dial, and they must not stack into an unwinnable state. BloodMoon raises aggression; pair it with GlassHouse only when the player has extra sight.",
    collection: "Design",
    tags: ["nightloop", "game-feel"],
    source: "internal",
    linkedProjectId: "prj-nightloop",
    updatedAt: hoursFromNow(-11),
    pinned: true,
  },
  {
    id: "kn-4",
    title: "Licence attestation, explained",
    body:
      "content-hash-match means we can prove the exact bytes came from an upstream Git blob. pack-license-attestation is weaker: a pack-level declaration. Never describe the second as a byte match. NC, ND and SA assets stay out entirely.",
    collection: "Process",
    tags: ["licensing", "kit"],
    source: "internal",
    linkedProjectId: "prj-kit",
    updatedAt: T(-3),
    pinned: false,
  },
  {
    id: "kn-5",
    title: "Local model shortlist (working)",
    body:
      "Candidates cluster into two shapes: small and fast enough for tool routing, and mid-size for drafting. The real constraint is memory floor on a 16GB machine while Studio is open. Quantisation buys headroom but distorts instruction following at the low end.",
    collection: "Research",
    tags: ["ai", "research"],
    source: "internal",
    linkedProjectId: "prj-research",
    updatedAt: hoursFromNow(-38),
    pinned: false,
  },
  {
    id: "kn-6",
    title: "Chess study routine that survives school",
    body:
      "One 50-minute block on weekdays, same time, no exceptions. Tactics before openings — openings feel productive and teach less. Annotate two games a week, and write the blunder reason, not the move.",
    collection: "Personal",
    tags: ["chess", "routine"],
    source: "internal",
    linkedProjectId: "prj-chess",
    updatedAt: T(-9),
    pinned: false,
  },
];

export const files: OrbitFile[] = [
  { id: "fl-1", name: "NightLoop-Balance-Matrix.csv", kind: "csv", sizeKb: 184, collection: "NightLoop", summary: "Modifier pairs × survivability across 120 simulated nights; 3 pairs land below 18% survival.", tags: ["balance", "data"], uploadedAt: hoursFromNow(-14), analyzed: true, projectId: "prj-nightloop" },
  { id: "fl-2", name: "bridged-ops-spec.md", kind: "md", sizeKb: 42, collection: "Bridge", summary: "Operator-facing spec for confirmation gates, scoped tokens and the audit chain.", tags: ["spec"], uploadedAt: T(-2), analyzed: true, projectId: "prj-bridge" },
  { id: "fl-3", name: "local-model-bench-prelim.csv", kind: "csv", sizeKb: 96, collection: "Research", summary: "Tokens/sec at three quantisations with Studio resident; memory floor per model.", tags: ["research", "benchmark"], uploadedAt: hoursFromNow(-21), analyzed: true, projectId: "prj-research" },
  { id: "fl-4", name: "Whisperer-proximity-pass.png", kind: "png", sizeKb: 512, collection: "NightLoop", summary: "Waveform overlay for the proximity cue; attack transient masks the knock.", tags: ["audio"], uploadedAt: hoursFromNow(-31), analyzed: false, projectId: "prj-nightloop" },
  { id: "fl-5", name: "asset-registry-audit.pdf", kind: "pdf", sizeKb: 1180, collection: "Kit", summary: "Per-file provenance export: hash match counts, attestation-only counts, zero unclear licences.", tags: ["licensing"], uploadedAt: T(-5), analyzed: true, projectId: "prj-kit" },
  { id: "fl-6", name: "tunnel-latency-runs.csv", kind: "csv", sizeKb: 64, collection: "Bridge", summary: "p50/p95 first-byte over QUIC and HTTP/2 fallback across 40 runs.", tags: ["infra"], uploadedAt: T(-8), analyzed: true, projectId: "prj-bridge" },
  { id: "fl-7", name: "entity-roster.md", kind: "md", sizeKb: 28, collection: "NightLoop", summary: "Eight entities with legibility rules and which two may co-occur per night.", tags: ["design"], uploadedAt: T(-12), analyzed: false, projectId: "prj-nightloop" },
  { id: "fl-8", name: "field-notes-03-draft.doc", kind: "doc", sizeKb: 74, collection: "Writing", summary: "Draft essay on what actually breaks when agents get write access.", tags: ["writing"], uploadedAt: T(-11), analyzed: false, projectId: "prj-writing" },
];

/* ------------------------------------------------------------ automations */

export const automations: Automation[] = [
  {
    id: "auto-1",
    name: "Nightly licence verification",
    description: "Re-hash the asset registry, flag drift, and file one task per mismatch.",
    trigger: "schedule",
    triggerDetail: "Daily · 02:00 Asia/Bangkok",
    enabled: true,
    requiresApproval: false,
    steps: [
      { id: "s1", kind: "trigger", title: "Schedule fires", detail: "02:00 daily", actor: "Automation Agent" },
      { id: "s2", kind: "action", title: "Run verify_licences", detail: "python3 -B tools/verify_licences.py", actor: "Automation Agent", tool: "shell.run" },
      { id: "s3", kind: "branch", title: "Mismatches > 0?", detail: "true → file tasks", actor: "Automation Agent" },
      { id: "s4", kind: "notify", title: "Post summary", detail: "Feed + activity log", actor: "Productivity Agent" },
    ],
    lastRunAt: hoursFromNow(-9),
    nextRunAt: hoursFromNow(15),
    successRate: 0.986,
    runs: [
      { id: "run-1a", at: hoursFromNow(-9), status: "success", durationMs: 8400, log: ["registry loaded: 4320 entries", "hash match: 4320", "attestation-only: 0", "drift: 0", "exit 0"] },
      { id: "run-1b", at: hoursFromNow(-33), status: "success", durationMs: 8100, log: ["registry loaded: 4320 entries", "hash match: 4318", "mismatch: 2 (contact sheets)", "filed 1 task", "exit 0"] },
      { id: "run-1c", at: hoursFromNow(-57), status: "failed", durationMs: 2200, log: ["registry loaded", "ERROR: ENOENT thumbnails/", "exit 1"] },
    ],
    tags: ["licensing", "safety"],
  },
  {
    id: "auto-2",
    name: "Tunnel health watchdog",
    description: "Probe health, restart the tunnel with QUIC→HTTP/2 fallback, alert on repeated failure.",
    trigger: "condition",
    triggerDetail: "When /api/health fails twice",
    enabled: true,
    requiresApproval: false,
    steps: [
      { id: "s1", kind: "trigger", title: "Two consecutive probe failures", detail: "strict JSON probe", actor: "Automation Agent" },
      { id: "s2", kind: "action", title: "Restart with fallback", detail: "protocol auto", actor: "Automation Agent", tool: "tunnel.restart" },
      { id: "s3", kind: "ai", title: "Classify failure", detail: "TLS vs 404 vs timeout", actor: "Development Agent" },
      { id: "s4", kind: "notify", title: "Escalate if still down", detail: "Feed item, priority high", actor: "Productivity Agent" },
    ],
    lastRunAt: hoursFromNow(-46),
    nextRunAt: null,
    successRate: 0.91,
    runs: [
      { id: "run-2a", at: hoursFromNow(-46), status: "success", durationMs: 4100, log: ["probe 1 fail (timeout)", "probe 2 fail (timeout)", "restart quic failed", "fallback http2 ok", "health ok"] },
      { id: "run-2b", at: T(-6), status: "skipped", durationMs: 0, log: ["probe ok", "probe ok", "no action"] },
    ],
    tags: ["infra"],
  },
  {
    id: "auto-3",
    name: "Morning briefing",
    description: "Assemble schedule, overdue tasks, deadlines and priorities into one briefing.",
    trigger: "schedule",
    triggerDetail: "Daily · 07:10 Asia/Bangkok",
    enabled: true,
    requiresApproval: false,
    steps: [
      { id: "s1", kind: "trigger", title: "Schedule fires", detail: "07:10 daily", actor: "Productivity Agent" },
      { id: "s2", kind: "action", title: "Pull calendar + tasks", detail: "read-only", actor: "Productivity Agent", tool: "calendar.list" },
      { id: "s3", kind: "ai", title: "Rank today's priorities", detail: "deadline × blocker × energy", actor: "Supervisor Agent" },
      { id: "s4", kind: "notify", title: "Deliver briefing", detail: "Home + right panel", actor: "Productivity Agent" },
    ],
    lastRunAt: hoursFromNow(-6),
    nextRunAt: hoursFromNow(18),
    successRate: 1,
    runs: [
      { id: "run-3a", at: hoursFromNow(-6), status: "success", durationMs: 3200, log: ["calendar: 11 events", "tasks: 12 open / 2 blocked", "ranked 4 priorities", "delivered"] },
      { id: "run-3b", at: hoursFromNow(-30), status: "success", durationMs: 2900, log: ["calendar: 10 events", "tasks: 14 open", "delivered"] },
    ],
    tags: ["ritual"],
  },
  {
    id: "auto-4",
    name: "Playtest capture digest",
    description: "When a playtest ends, collect console output and failures, then summarise.",
    trigger: "event",
    triggerDetail: "On playtest stop",
    enabled: true,
    requiresApproval: true,
    steps: [
      { id: "s1", kind: "trigger", title: "Playtest stops", detail: "server + edit routing", actor: "Automation Agent" },
      { id: "s2", kind: "action", title: "Pull console output", detail: "last 500 lines", actor: "Development Agent", tool: "studio.console" },
      { id: "s3", kind: "ai", title: "Cluster failures", detail: "dedupe by stack frame", actor: "Development Agent" },
      { id: "s4", kind: "action", title: "File tasks", detail: "requires approval", actor: "Supervisor Agent", tool: "task.create" },
    ],
    lastRunAt: hoursFromNow(-17),
    nextRunAt: null,
    successRate: 0.78,
    runs: [
      { id: "run-4a", at: hoursFromNow(-17), status: "success", durationMs: 12600, log: ["console: 143 lines", "clusters: 3", "filed 2 tasks", "1 suppressed (known)"] },
      { id: "run-4b", at: T(-1), status: "failed", durationMs: 900, log: ["console fetch 503", "peer evicted mid-capture", "retry scheduled"] },
    ],
    tags: ["playtest"],
  },
  {
    id: "auto-5",
    name: "Weekly review prep",
    description: "Fridays: gather shipped work, stalled projects and next-week risks.",
    trigger: "schedule",
    triggerDetail: "Fridays · 20:00 Asia/Bangkok",
    enabled: false,
    requiresApproval: false,
    steps: [
      { id: "s1", kind: "trigger", title: "Friday 20:00", detail: "weekly", actor: "Productivity Agent" },
      { id: "s2", kind: "action", title: "Diff project progress", detail: "7-day window", actor: "Planning Agent", tool: "project.diff" },
      { id: "s3", kind: "ai", title: "Draft review", detail: "shipped / stalled / risks", actor: "Supervisor Agent" },
    ],
    lastRunAt: T(-6),
    nextRunAt: T(2),
    successRate: 1,
    runs: [{ id: "run-5a", at: T(-6), status: "success", durationMs: 5400, log: ["4 active projects", "2 stalled milestones", "draft ready"] }],
    tags: ["ritual"],
  },
];

/* ----------------------------------------------------------- integrations */

export const integrations: Integration[] = [
  { id: "int-gmail", name: "Gmail", category: "comms", description: "Read threads, draft replies, label and file.", status: "connected", scopes: ["gmail.readonly", "gmail.compose"], connectedAt: T(-40), lastSyncAt: hoursFromNow(-0.2), throughput: "18 threads / hr", accent: "#f2626b", glyph: "M" },
  { id: "int-cal", name: "Google Calendar", category: "productivity", description: "Primary calendar, events, free/busy and reminders.", status: "connected", scopes: ["calendar.events", "calendar.readonly"], connectedAt: T(-44), lastSyncAt: hoursFromNow(-0.1), throughput: "live", accent: "#7c9cff", glyph: "31" },
  { id: "int-drive", name: "Google Drive", category: "storage", description: "Search and read documents, sheets and PDFs.", status: "connected", scopes: ["drive.readonly"], connectedAt: T(-38), lastSyncAt: hoursFromNow(-1), throughput: "142 files indexed", accent: "#4ade9b", glyph: "D" },
  { id: "int-docs", name: "Google Docs", category: "productivity", description: "Read and write long-form documents.", status: "connected", scopes: ["documents"], connectedAt: T(-38), lastSyncAt: hoursFromNow(-2), throughput: "9 docs", accent: "#56cfd8", glyph: "W" },
  { id: "int-slides", name: "Google Slides", category: "productivity", description: "Generate decks from reports and findings.", status: "connected", scopes: ["presentations"], connectedAt: T(-20), lastSyncAt: hoursFromNow(-30), throughput: "3 decks", accent: "#f5b556", glyph: "S" },
  { id: "int-vercel", name: "Vercel", category: "dev", description: "Deploy projects, read build logs, roll back.", status: "connected", scopes: ["deployments", "logs"], connectedAt: T(-16), lastSyncAt: hoursFromNow(-4), throughput: "2 projects", accent: "#e8ecf1", glyph: "▲" },
  { id: "int-github", name: "GitHub", category: "dev", description: "Issues, pull requests, checks and releases.", status: "syncing", scopes: ["repo:read", "pr:write"], connectedAt: T(-60), lastSyncAt: hoursFromNow(-0.05), throughput: "syncing…", accent: "#a78bfa", glyph: "G" },
  { id: "int-studio", name: "Roblox Studio", category: "dev", description: "Bridge peer: scripts, scenes, playtests.", status: "connected", scopes: ["bridge", "mcp"], connectedAt: T(-51), lastSyncAt: hoursFromNow(-0.05), throughput: "1 peer · edit", accent: "#4ade9b", glyph: "R" },
  { id: "int-obsidian", name: "Obsidian", category: "storage", description: "Two-way sync for the local vault.", status: "available", scopes: [], connectedAt: null, lastSyncAt: null, throughput: "not connected", accent: "#a78bfa", glyph: "O" },
  { id: "int-slack", name: "Slack", category: "comms", description: "Send summaries and receive approvals.", status: "available", scopes: [], connectedAt: null, lastSyncAt: null, throughput: "not connected", accent: "#56cfd8", glyph: "#" },
  { id: "int-notion", name: "Notion", category: "productivity", description: "Mirror notes into a workspace database.", status: "available", scopes: [], connectedAt: null, lastSyncAt: null, throughput: "not connected", accent: "#e8ecf1", glyph: "N" },
  { id: "int-stripe", name: "Stripe", category: "finance", description: "Revenue and payout summaries.", status: "error", scopes: ["read_only"], connectedAt: T(-10), lastSyncAt: T(-2), throughput: "auth expired", accent: "#f5b556", glyph: "$" },
];

/* ----------------------------------------------------------- tools & agents */

export const tools: ToolDefinition[] = [
  { id: "tasks.create", name: "Create task", description: "Create a task in the local task store.", category: "action", permission: "write", requiresApproval: true, inputSchema: [{ field: "title", type: "string", required: true, note: "Imperative, under 80 chars" }, { field: "projectId", type: "string", required: false, note: "Existing project" }, { field: "due", type: "iso8601", required: false, note: "None means backlog" }], validate: (r) => /tsk_|created/.test(r) },
  { id: "tasks.update", name: "Update task", description: "Change status, priority, due date or owner.", category: "action", permission: "write", requiresApproval: true, inputSchema: [{ field: "id", type: "string", required: true, note: "Task id" }, { field: "patch", type: "object", required: true, note: "Partial task" }], validate: (r) => /updated/.test(r) },
  { id: "projects.create", name: "Create project", description: "Create a project with objectives and milestones.", category: "action", permission: "write", requiresApproval: true, inputSchema: [{ field: "name", type: "string", required: true, note: "Unique" }, { field: "objectives", type: "string[]", required: true, note: "3 max" }], validate: (r) => /prj_|created/.test(r) },
  { id: "calendar.create", name: "Create event", description: "Write an event to the calendar.", category: "action", permission: "external", requiresApproval: true, inputSchema: [{ field: "title", type: "string", required: true, note: "Short" }, { field: "start", type: "iso8601", required: true, note: "With offset" }, { field: "end", type: "iso8601", required: true, note: "After start" }], validate: (r) => /ev_|created/.test(r) },
  { id: "calendar.read", name: "Read calendar", description: "List events in a window.", category: "data", permission: "read", requiresApproval: false, inputSchema: [{ field: "from", type: "iso8601", required: true, note: "" }, { field: "to", type: "iso8601", required: true, note: "" }], validate: () => true },
  { id: "knowledge.search", name: "Search knowledge", description: "Full-text search across notes and files.", category: "data", permission: "read", requiresApproval: false, inputSchema: [{ field: "query", type: "string", required: true, note: "Terms" }], validate: () => true },
  { id: "knowledge.write", name: "Write knowledge note", description: "Create or update a note.", category: "action", permission: "write", requiresApproval: true, inputSchema: [{ field: "title", type: "string", required: true, note: "" }, { field: "body", type: "string", required: true, note: "Markdown" }], validate: (r) => /kn_|saved/.test(r) },
  { id: "research.fetch", name: "Fetch sources", description: "Retrieve and compare external sources. Leaves the machine, so it is gated once per conversation rather than per source.", category: "research", permission: "external", requiresApproval: true, inputSchema: [{ field: "query", type: "string", required: true, note: "" }], validate: () => true },
  { id: "automation.run", name: "Run automation", description: "Execute an automation now.", category: "action", permission: "execute", requiresApproval: true, inputSchema: [{ field: "id", type: "string", required: true, note: "Automation id" }], validate: (r) => /run_|completed/.test(r) },
  { id: "automation.create", name: "Create automation", description: "Create a workflow with a trigger and steps.", category: "action", permission: "write", requiresApproval: true, inputSchema: [{ field: "name", type: "string", required: true, note: "" }, { field: "trigger", type: "enum", required: true, note: "schedule|event|condition|manual" }], validate: (r) => /auto_|created/.test(r) },
  { id: "files.analyze", name: "Analyze file", description: "Extract structure and findings from a file.", category: "data", permission: "read", requiresApproval: false, inputSchema: [{ field: "id", type: "string", required: true, note: "File id" }], validate: () => true },
  { id: "briefing.generate", name: "Generate briefing", description: "Assemble a grounded daily briefing.", category: "data", permission: "read", requiresApproval: false, inputSchema: [{ field: "date", type: "date", required: true, note: "Local date" }], validate: () => true },
  // Internal orchestration: delegation moves work between agents, it does not
  // touch user data. Any write a delegate performs is gated individually, so
  // this one is the single execute-class tool that is not approval-gated.
  { id: "agents.delegate", name: "Delegate to agent", description: "Hand a scoped objective to a specialist agent. Internal orchestration; delegates' own writes stay gated.", category: "system", permission: "execute", requiresApproval: false, inputSchema: [{ field: "agent", type: "enum", required: true, note: "Agent id" }, { field: "objective", type: "string", required: true, note: "Bounded" }], validate: () => true },
];

export const agents: AgentDefinition[] = [
  { id: "supervisor", name: "Supervisor", role: "Coordinates, reviews, refuses", description: "Interprets objectives, decides whether delegation is worth it, tracks dependencies and reviews every result before it reaches you.", tools: ["agents.delegate", "tasks.update", "projects.create"], boundaries: ["Never executes a write itself", "Must state why delegation helps", "Blocks on unsatisfied dependencies"], accent: "#7c9cff" },
  { id: "research", name: "Research", role: "Gathers and compares sources", description: "Searches, evaluates, and compares findings with source references kept intact.", tools: ["research.fetch", "knowledge.search", "files.analyze"], boundaries: ["No writes", "Every claim carries a source or is marked unverified"], accent: "#56cfd8" },
  { id: "planning", name: "Planning", role: "Turns goals into sequenced work", description: "Builds task graphs, dependencies, milestones and honest complexity estimates.", tools: ["tasks.create", "projects.create"], boundaries: ["Estimate ranges, not point values", "Flag unknown dependencies instead of guessing"], accent: "#a78bfa" },
  { id: "productivity", name: "Productivity", role: "Guardian of the schedule", description: "Manages tasks and calendar, spots overdue work, writes daily and weekly summaries.", tools: ["calendar.read", "calendar.create", "tasks.update", "briefing.generate"], boundaries: ["Never double-books", "Surfaces overload rather than silently rescheduling"], accent: "#4ade9b" },
  { id: "knowledge", name: "Knowledge", role: "Retrieves your own context", description: "Finds notes, documents and prior decisions, and shows how they connect.", tools: ["knowledge.search", "knowledge.write", "files.analyze"], boundaries: ["Quotes rather than paraphrases when precision matters", "Cites note ids"], accent: "#f5b556" },
  { id: "development", name: "Development", role: "Writes and reviews code", description: "Implements, reviews, debugs and explains technical trade-offs inside your repos.", tools: ["files.analyze", "shell.run", "studio.console"], boundaries: ["No force-push", "Tests before claiming success"], accent: "#7c9cff" },
  { id: "data", name: "Data Analyst", role: "Finds the shape in data", description: "Reads spreadsheets, produces charts and structured findings with caveats attached.", tools: ["files.analyze", "knowledge.write"], boundaries: ["States sample size", "Never extrapolates past the data"], accent: "#56cfd8" },
  { id: "automation", name: "Automation", role: "Builds and guards workflows", description: "Designs automations, connects services, runs approved routines and watches for failures.", tools: ["automation.create", "automation.run", "shell.run"], boundaries: ["Irreversible steps need approval", "Disables a workflow after three consecutive failures"], accent: "#f2626b" },
];

/* ------------------------------------------------------------ conversations */

export const conversations: Conversation[] = [
  {
    id: "cnv-1",
    title: "What should I focus on tonight?",
    folder: "Daily",
    projectId: null,
    pinned: true,
    archived: false,
    updatedAt: hoursFromNow(-2),
    contextIds: ["prj-nightloop", "prj-bridge"],
    messages: [
      {
        id: "m1",
        role: "user",
        content: "What should I focus on tonight?",
        mode: "chat",
        createdAt: hoursFromNow(-2.1),
      },
      {
        id: "m2",
        role: "orbit",
        agent: "productivity",
        mode: "chat",
        createdAt: hoursFromNow(-2),
        content:
          "Your build block tonight is 20:45–22:15, ninety minutes. Two things are actually urgent and one is noise.\n\n**Do:** the modifier matrix (NightLoop). It is the only item blocking the release candidate, and the matrix is data work you can finish in one block.\n\n**Then:** the ghost-peer audit at 22:15 if you still have attention — it is verification, not discovery, so it survives a tired brain.\n\n**Skip:** the Ruy Lopez annotation. It is due tomorrow afternoon, not tonight, and chess study has its own block at 15:10.\n\nOne flag: laundry pickup closes at 17:00 and it is the only hard window in your day.",
        toolCalls: [
          { id: "tc1", tool: "calendar.read", label: "Read today's calendar", args: { from: "today 00:00", to: "today 23:59" }, status: "success", requiresApproval: false, result: "11 events; next block 20:45", durationMs: 210, auditAt: hoursFromNow(-2) },
          { id: "tc2", tool: "knowledge.search", label: "Search NightLoop balance notes", args: { query: "modifier balance" }, status: "success", requiresApproval: false, result: "kn-3 (Horror pacing notes)", durationMs: 96, auditAt: hoursFromNow(-2) },
        ],
        structured: [
          {
            kind: "checklist",
            title: "Tonight",
            items: [
              { label: "Collect laundry (hard window)", value: "by 17:00" },
              { label: "Modifier matrix", value: "20:45–22:15" },
              { label: "Ghost-peer audit", value: "22:15+" },
            ],
          },
        ],
      },
    ],
  },
  {
    id: "cnv-2",
    title: "Local AI models — research plan",
    folder: "Research",
    projectId: "prj-research",
    pinned: true,
    archived: false,
    updatedAt: hoursFromNow(-30),
    contextIds: ["prj-research", "fl-3"],
    messages: [
      { id: "m3", role: "user", content: "Start a research plan for local AI models I could actually run alongside Studio.", mode: "research", createdAt: hoursFromNow(-31) },
      {
        id: "m4",
        role: "orbit",
        agent: "research",
        mode: "research",
        createdAt: hoursFromNow(-30),
        content:
          "Scoped to what your machine can hold with Studio resident — that constraint kills half the usual shortlist, so the plan starts there.\n\nI split candidates by job rather than by parameter count, because that is the distinction that changes your workflow.",
        structured: [
          {
            kind: "table",
            title: "Candidate split",
            headers: ["Job", "Shape", "Constraint"],
            rows: [
              ["Tool routing", "small, fast", "must hold under 4GB resident"],
              ["Drafting / analysis", "mid-size", "quality floor on instruction following"],
              ["Code assist", "mid-size, code-tuned", "context window ≥ 32k"],
            ],
          },
        ],
        toolCalls: [
          { id: "tc3", tool: "research.fetch", label: "Sweep model cards", args: { query: "local llm 2026 quantisation" }, status: "success", requiresApproval: false, result: "6 candidates, 2 licence flags", durationMs: 1840, auditAt: hoursFromNow(-30) },
        ],
        sources: [
          { title: "Preliminary bench (your file)", url: "orbit://files/fl-3", note: "Local tokens/sec at three quantisations" },
          { title: "Model licence matrix", url: "orbit://knowledge/kn-5", note: "Licence risk separated from quality" },
        ],
      },
    ],
  },
  {
    id: "cnv-3",
    title: "Bridge hardening — plan and execute",
    folder: "Engineering",
    projectId: "prj-bridge",
    pinned: false,
    archived: false,
    updatedAt: T(-2),
    contextIds: ["prj-bridge", "kn-1", "kn-2"],
    messages: [],
  },
  {
    id: "cnv-4",
    title: "Summarise everything I finished today",
    folder: "Daily",
    projectId: null,
    pinned: false,
    archived: false,
    updatedAt: T(-7),
    contextIds: [],
    messages: [],
  },
  {
    id: "cnv-5",
    title: "Old: v5 tunnel debugging thread",
    folder: "Archive",
    projectId: "prj-bridge",
    pinned: false,
    archived: true,
    updatedAt: T(-40),
    contextIds: [],
    messages: [],
  },
];

/* ----------------------------------------------------------------- feed */

export const feed: FeedItem[] = [
  { id: "fd-1", at: hoursFromNow(-0.4), kind: "attention", title: "Laundry pickup closes at 17:00", detail: "Only hard time window today. The 16:00–18:00 play block overlaps it.", actionLabel: "Open calendar", actionTarget: { section: "calendar" }, priority: "high" },
  { id: "fd-2", at: hoursFromNow(-1), kind: "project", title: "NightLoop slipped 3 points", detail: "Modifier matrix is the only blocker on the release candidate.", actionLabel: "Open project", actionTarget: { section: "projects", id: "prj-nightloop" }, priority: "critical" },
  { id: "fd-3", at: hoursFromNow(-2), kind: "automation", title: "Nightly licence verification passed", detail: "4,320 entries hashed, zero drift.", actionLabel: "View run", actionTarget: { section: "automations", id: "auto-1" }, priority: "low" },
  { id: "fd-4", at: hoursFromNow(-4), kind: "integration", title: "Stripe auth expired", detail: "Read-only key rejected 2 days ago. Revenue summaries are stale.", actionLabel: "Reconnect", actionTarget: { section: "integrations" }, priority: "medium" },
  { id: "fd-5", at: hoursFromNow(-6), kind: "summary", title: "Morning briefing assembled", detail: "11 events, 12 open tasks, 2 blocked, 4 ranked priorities.", actionLabel: "Open home", actionTarget: { section: "home" }, priority: "low" },
  { id: "fd-6", at: hoursFromNow(-17), kind: "automation", title: "Playtest digest filed 2 tasks", detail: "3 failure clusters found; 1 suppressed as known.", actionLabel: "View tasks", actionTarget: { section: "tasks" }, priority: "medium" },
  { id: "fd-7", at: T(-1), kind: "task", title: "Whisperer audio pass is blocked", detail: "Waiting on the PCM preset round-trip. Two days idle.", actionLabel: "Unblock", actionTarget: { section: "tasks" }, priority: "high" },
  { id: "fd-8", at: T(-2), kind: "reminder", title: "Bridge token is 28 days old", detail: "Rotation scheduled in 2 days by policy.", actionLabel: "Rotate now", actionTarget: { section: "settings" }, priority: "medium" },
];

/* -------------------------------------------------------------- activity */

export const activity: ActivityEntry[] = [
  { id: "ac-1", at: hoursFromNow(-0.4), kind: "tool", title: "calendar.read", detail: "Read 11 events for today.", actor: "Productivity Agent", status: "ok", meta: { permission: "read", duration: "210ms" } },
  { id: "ac-2", at: hoursFromNow(-2), kind: "agent", title: "Delegated to Productivity", detail: "Objective: rank tonight's focus by blocker weight.", actor: "Supervisor Agent", status: "ok" },
  { id: "ac-3", at: hoursFromNow(-4), kind: "integration", title: "Stripe sync failed", detail: "OAuth token expired. No writes attempted.", actor: "System", status: "error" },
  { id: "ac-4", at: hoursFromNow(-6), kind: "automation", title: "Morning briefing delivered", detail: "3.2s · 11 events · 12 tasks.", actor: "Automation Agent", status: "ok", meta: { run: "run-3a" } },
  { id: "ac-5", at: hoursFromNow(-9), kind: "automation", title: "Licence verification passed", detail: "4,320 entries · 0 drift.", actor: "Automation Agent", status: "ok" },
  { id: "ac-6", at: hoursFromNow(-17), kind: "approval", title: "Approved: file playtest tasks", detail: "Confirm gate satisfied by operator.", actor: "You", status: "ok" },
  { id: "ac-7", at: T(-1), kind: "tool", title: "studio.console (failed)", detail: "Peer evicted mid-capture (503).", actor: "Development Agent", status: "warn" },
  { id: "ac-8", at: T(-2), kind: "system", title: "Snapshot written", detail: "bridge_state snapshot 41 · 2.1MB.", actor: "System", status: "ok" },
];

export const runtimes: OrbitState["runtimes"] = [];

export function buildSeed(): OrbitState {
  return {
    identity,
    preferences: defaultPreferences,
    projects,
    tasks,
    events,
    reminders,
    notes,
    files,
    automations,
    integrations,
    conversations,
    activeConversationId: "cnv-1",
    runtimes,
    activity,
    seedVersion: SEED_VERSION,
  };
}
