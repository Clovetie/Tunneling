export const NODE_TYPES = [
  "agent", "llm", "tool", "mcp_server", "http_request", "condition", "router",
  "loop", "memory", "knowledge_retrieval", "human_approval", "task", "output"
] as const;

export type FoundryNodeType = (typeof NODE_TYPES)[number];

export const NODE_LABELS: Record<FoundryNodeType, string> = {
  agent: "Agent", llm: "LLM", tool: "Tool", mcp_server: "MCP Server",
  http_request: "HTTP Request", condition: "Condition", router: "Router",
  loop: "Loop", memory: "Memory", knowledge_retrieval: "Knowledge Retrieval",
  human_approval: "Human Approval", task: "Task", output: "Output"
};

export const AGENT_STATUSES = [
  "idle", "thinking", "executing", "waiting_approval", "blocked", "failed", "completed", "disabled"
] as const;
export type AgentStatus = (typeof AGENT_STATUSES)[number];

export const TASK_STATUSES = [
  "todo", "running", "waiting_approval", "blocked", "failed", "completed", "skipped", "cancelled"
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

export const MEMORY_SCOPES = ["agent", "project", "team", "workflow"] as const;
export type MemoryScope = (typeof MEMORY_SCOPES)[number];

export type WorkflowNode = {
  id: string;
  type: FoundryNodeType;
  position: { x: number; y: number };
  data: { label: string; config: Record<string, unknown>; status?: string };
};

export type WorkflowEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  label?: string;
};

export type WorkflowGraph = { nodes: WorkflowNode[]; edges: WorkflowEdge[] };

export type TaskRecord = {
  id?: string;
  run_id: string;
  key: string;
  node_id: string;
  kind: FoundryNodeType | "supervisor" | "tool";
  status: TaskStatus;
  depends_on: string[];
  assignee_agent_id?: string | null;
  input: unknown;
  output?: unknown;
  artifacts?: unknown;
  attempts: number;
  max_attempts: number;
  timeout_ms: number;
  resource_lock?: string | null;
  lease_owner?: string | null;
  lease_expires_at?: string | null;
  branch_conditions?: Array<{ edgeId: string; source: string; sourceHandle?: string | null }>;
};

export type DurableJob = {
  id: string;
  type: "run.workflow" | "run.coordinate" | "task.execute" | "tool.call" | "approval.continue" | "mcp.refresh";
  payload: Record<string, unknown>;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  attempts: number;
  max_attempts: number;
  lease_owner?: string | null;
  lease_expires_at?: string | null;
};

export type ApiError = { error: string; code?: string; details?: unknown };
