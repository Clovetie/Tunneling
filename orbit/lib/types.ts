/**
 * ORBIT core domain model.
 * Every entity is persisted locally and every mutation flows through the store,
 * so the UI is a projection of state rather than a set of mock screens.
 */

export type Priority = "low" | "medium" | "high" | "critical";

export type SectionId =
  | "home"
  | "assistant"
  | "projects"
  | "tasks"
  | "calendar"
  | "knowledge"
  | "files"
  | "automations"
  | "integrations"
  | "activity"
  | "settings";

/* ------------------------------------------------------------------ people */

export interface Identity {
  name: string;
  handle: string;
  role: string;
  timezone: string;
  locale: string;
  avatarInitials: string;
}

/* ---------------------------------------------------------------- projects */

export interface Milestone {
  id: string;
  title: string;
  due: string | null;
  done: boolean;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  objectives: string[];
  status: "planning" | "active" | "paused" | "shipped" | "archived";
  priority: Priority;
  color: string;
  start: string | null;
  target: string | null;
  progress: number;
  milestones: Milestone[];
  pinned: boolean;
  tags: string[];
  lead: string;
  createdAt: string;
  updatedAt: string;
}

/* ------------------------------------------------------------------- tasks */

export interface Task {
  id: string;
  title: string;
  notes: string;
  projectId: string | null;
  status: "todo" | "doing" | "blocked" | "done";
  priority: Priority;
  due: string | null;
  estimateMin: number | null;
  tags: string[];
  assignee: string | null;
  createdAt: string;
  completedAt: string | null;
}

/* ---------------------------------------------------------------- calendar */

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  kind: "event" | "focus" | "routine" | "deadline" | "reminder";
  location: string | null;
  projectId: string | null;
  source: "orbit" | "google";
}

export interface Reminder {
  id: string;
  text: string;
  at: string;
  repeat: "none" | "daily" | "weekly";
  done: boolean;
}

/* --------------------------------------------------------------- knowledge */

export interface KnowledgeNote {
  id: string;
  title: string;
  body: string;
  collection: string;
  tags: string[];
  source: string;
  linkedProjectId: string | null;
  updatedAt: string;
  pinned: boolean;
}

/* ------------------------------------------------------------------- files */

export interface OrbitFile {
  id: string;
  name: string;
  kind: "pdf" | "md" | "csv" | "png" | "doc" | "code" | "sheet";
  sizeKb: number;
  collection: string;
  summary: string;
  tags: string[];
  uploadedAt: string;
  analyzed: boolean;
  projectId: string | null;
}

/* ------------------------------------------------------------- automations */

export type TriggerKind = "schedule" | "event" | "condition" | "manual";
export type RunStatus = "success" | "failed" | "running" | "skipped";

export interface AutomationStep {
  id: string;
  kind: "trigger" | "ai" | "action" | "branch" | "notify";
  title: string;
  detail: string;
  actor: string;
  tool?: string;
}

export interface AutomationRun {
  id: string;
  at: string;
  status: RunStatus;
  durationMs: number;
  log: string[];
}

export interface Automation {
  id: string;
  name: string;
  description: string;
  trigger: TriggerKind;
  triggerDetail: string;
  enabled: boolean;
  requiresApproval: boolean;
  steps: AutomationStep[];
  lastRunAt: string | null;
  nextRunAt: string | null;
  successRate: number;
  runs: AutomationRun[];
  tags: string[];
}

/* ------------------------------------------------------------ integrations */

export interface Integration {
  id: string;
  name: string;
  category: "productivity" | "comms" | "storage" | "dev" | "ai" | "finance";
  description: string;
  status: "connected" | "available" | "error" | "syncing";
  scopes: string[];
  connectedAt: string | null;
  lastSyncAt: string | null;
  throughput: string;
  accent: string;
  glyph: string;
}

/* ------------------------------------------------------------- assistant */

export type Mode =
  | "chat"
  | "research"
  | "planning"
  | "execution"
  | "analysis"
  | "agent";

export interface ToolCall {
  id: string;
  tool: string;
  label: string;
  args: Record<string, unknown>;
  status: "pending" | "approved" | "denied" | "success" | "error";
  result?: string;
  requiresApproval: boolean;
  durationMs?: number;
  auditAt?: string;
}

export interface AgentStep {
  id: string;
  agent: AgentId;
  action: string;
  detail: string;
  status: "queued" | "working" | "done" | "failed";
  startedAt: string;
  finishedAt?: string;
  handoffTo?: AgentId;
  artifact?: string;
}

export interface Message {
  id: string;
  role: "user" | "orbit";
  content: string;
  mode: Mode;
  agent?: AgentId;
  createdAt: string;
  toolCalls?: ToolCall[];
  steps?: AgentStep[];
  attachments?: string[];
  structured?: StructuredBlock[];
  streaming?: boolean;
  sources?: { title: string; url: string; note?: string }[];
}

export interface StructuredBlock {
  kind: "plan" | "table" | "findings" | "code" | "checklist" | "chart";
  title: string;
  rows?: string[][];
  headers?: string[];
  items?: { label: string; value?: string; done?: boolean }[];
  code?: { lang: string; body: string };
  series?: { label: string; value: number }[];
}

export interface Conversation {
  id: string;
  title: string;
  folder: string;
  projectId: string | null;
  pinned: boolean;
  archived: boolean;
  updatedAt: string;
  contextIds: string[];
  messages: Message[];
}

/* ----------------------------------------------------------------- agents */

export type AgentId =
  | "supervisor"
  | "research"
  | "planning"
  | "productivity"
  | "knowledge"
  | "development"
  | "data"
  | "automation";

export interface AgentDefinition {
  id: AgentId;
  name: string;
  role: string;
  description: string;
  tools: string[];
  boundaries: string[];
  accent: string;
}

export interface AgentRuntime {
  id: string;
  objective: string;
  supervisorNote: string;
  status: "running" | "review" | "complete" | "failed" | "awaiting-approval";
  startedAt: string;
  steps: AgentStep[];
  mode: Mode;
}

/* ------------------------------------------------------------------ tools */

export interface ToolDefinition {
  id: string;
  name: string;
  description: string;
  category: "data" | "action" | "research" | "system";
  permission: "read" | "write" | "execute" | "external";
  requiresApproval: boolean;
  inputSchema: { field: string; type: string; required: boolean; note: string }[];
  validate: (result: string) => boolean;
}

/* ------------------------------------------------------------------ meta */

export interface ActivityEntry {
  id: string;
  at: string;
  kind:
    | "tool"
    | "agent"
    | "automation"
    | "system"
    | "approval"
    | "integration";
  title: string;
  detail: string;
  actor: string;
  status: "ok" | "warn" | "error" | "pending";
  meta?: Record<string, string>;
}

export interface FeedItem {
  id: string;
  at: string;
  kind:
    | "attention"
    | "reminder"
    | "task"
    | "project"
    | "automation"
    | "summary"
    | "integration";
  title: string;
  detail: string;
  actionLabel?: string;
  actionTarget?: { section: SectionId; id?: string };
  priority: Priority;
}

export interface Preferences {
  displayName: string;
  timezone: string;
  autoApproveReads: boolean;
  confirmAllWrites: boolean;
  agentVerbosity: "concise" | "balanced" | "detailed";
  reducedMotion: boolean;
  accent: "indigo" | "mint" | "violet" | "amber";
  weekStart: "monday" | "sunday";
  sidebarCollapsed: boolean;
  rightPanelOpen: boolean;
  rightPanelWidth: number;
}

export interface OrbitState {
  identity: Identity;
  preferences: Preferences;
  projects: Project[];
  tasks: Task[];
  events: CalendarEvent[];
  reminders: Reminder[];
  notes: KnowledgeNote[];
  files: OrbitFile[];
  automations: Automation[];
  integrations: Integration[];
  conversations: Conversation[];
  activeConversationId: string | null;
  runtimes: AgentRuntime[];
  activity: ActivityEntry[];
  seedVersion: number;
}
