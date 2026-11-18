import { NODE_TYPES, type FoundryNodeType, type TaskRecord, type TaskStatus, type WorkflowEdge, type WorkflowGraph, type WorkflowNode } from "@/lib/types";
import { asObject } from "@/lib/utils";

export type ValidationResult = { valid: boolean; errors: string[]; order: string[] };

function isNodeType(value: unknown): value is FoundryNodeType {
  return typeof value === "string" && (NODE_TYPES as readonly string[]).includes(value);
}

export function validateWorkflowGraph(graph: unknown): ValidationResult {
  const errors: string[] = [];
  const value = asObject(graph);
  const nodes = Array.isArray(value.nodes) ? value.nodes as unknown[] : null;
  const edges = Array.isArray(value.edges) ? value.edges as unknown[] : null;
  if (!nodes) errors.push("Graph nodes must be an array.");
  if (!edges) errors.push("Graph edges must be an array.");
  if (!nodes || !edges) return { valid: false, errors, order: [] };
  if (!nodes.length) errors.push("Graph needs at least one node.");
  const ids = new Set<string>();
  const typedNodes: WorkflowNode[] = [];
  nodes.forEach((raw, index) => {
    const node = asObject(raw);
    const id = String(node.id ?? "");
    const type = node.type;
    const position = asObject(node.position);
    const data = asObject(node.data);
    if (!id) errors.push(`Node ${index} has no id.`);
    if (ids.has(id)) errors.push(`Duplicate node id ${id}.`);
    ids.add(id);
    if (!isNodeType(type)) errors.push(`Node ${id || index} has unsupported type ${String(type)}.`);
    if (!Number.isFinite(Number(position.x)) || !Number.isFinite(Number(position.y))) errors.push(`Node ${id || index} needs a numeric position.`);
    if (typeof data.label !== "string" || !data.label.trim()) errors.push(`Node ${id || index} needs a label.`);
    const config = asObject(data.config);
    if (isNodeType(type)) {
      const required = requiredConfigError(type, config);
      if (required) errors.push(`Node ${id || index}: ${required}`);
    }
    typedNodes.push({ id, type: type as FoundryNodeType, position: { x: Number(position.x), y: Number(position.y) }, data: { label: String(data.label ?? id), config, status: data.status ? String(data.status) : undefined } });
  });
  const typedEdges: WorkflowEdge[] = [];
  const edgeIds = new Set<string>();
  edges.forEach((raw, index) => {
    const edge = asObject(raw);
    const id = String(edge.id ?? `edge-${index}`);
    const source = String(edge.source ?? "");
    const target = String(edge.target ?? "");
    if (edgeIds.has(id)) errors.push(`Duplicate edge id ${id}.`);
    edgeIds.add(id);
    if (!ids.has(source)) errors.push(`Edge ${id} has unknown source ${source}.`);
    if (!ids.has(target)) errors.push(`Edge ${id} has unknown target ${target}.`);
    if (source === target) errors.push(`Edge ${id} is a self-loop; use the Loop node for iteration.`);
    typedEdges.push({ id, source, target, sourceHandle: edge.sourceHandle == null ? null : String(edge.sourceHandle), targetHandle: edge.targetHandle == null ? null : String(edge.targetHandle), label: edge.label ? String(edge.label) : undefined });
  });
  try {
    const order = topologicalOrder({ nodes: typedNodes, edges: typedEdges });
    return { valid: errors.length === 0, errors, order };
  } catch (error) {
    errors.push(error instanceof Error ? error.message : String(error));
    return { valid: false, errors, order: [] };
  }
}

function requiredConfigError(type: FoundryNodeType, config: Record<string, unknown>): string | null {
  if (type === "agent" && !config.assigneeAgentId && !config.agentId) return "Agent node requires assigneeAgentId.";
  if (type === "tool" || type === "mcp_server") {
    if (!config.toolId && !config.toolName) return "Tool node requires toolId or toolName.";
  }
  if (type === "http_request" && !config.url && !config.toolId) return "HTTP node requires a URL or allowlisted toolId.";
  if (type === "condition" && !config.path && !config.expression) return "Condition node requires path or expression.";
  if (type === "router" && !Array.isArray(config.routes) && !config.expression) return "Router node requires routes or expression.";
  if (type === "loop" && !Number.isFinite(Number(config.iterations ?? 1))) return "Loop node requires a numeric iterations value.";
  if (type === "knowledge_retrieval" && !config.query && !config.queryPath) return "Knowledge node requires query or queryPath.";
  if (type === "memory" && !config.operation) return "Memory node requires an operation.";
  if (type === "human_approval" && !config.reason) return "Human Approval node requires a reason.";
  return null;
}

export function topologicalOrder(graph: WorkflowGraph): string[] {
  const indegree = new Map<string, number>();
  const adjacency = new Map<string, string[]>();
  for (const node of graph.nodes) { indegree.set(node.id, 0); adjacency.set(node.id, []); }
  for (const edge of graph.edges) {
    if (!indegree.has(edge.source) || !indegree.has(edge.target)) continue;
    adjacency.get(edge.source)!.push(edge.target);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  }
  const queue = graph.nodes.filter((node) => (indegree.get(node.id) ?? 0) === 0).map((node) => node.id);
  const order: string[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    order.push(id);
    for (const target of adjacency.get(id) ?? []) {
      const next = (indegree.get(target) ?? 0) - 1;
      indegree.set(target, next);
      if (next === 0) queue.push(target);
    }
  }
  if (order.length !== graph.nodes.length) throw new Error("Workflow graph contains a cycle. Use a Loop node instead of an edge cycle.");
  return order;
}

export function compileGraphToTasks(graph: WorkflowGraph, context: { ownerId: string; runId: string; projectId?: string | null; input?: unknown }): TaskRecord[] {
  const validation = validateWorkflowGraph(graph);
  if (!validation.valid) throw new Error(`WORKFLOW_GRAPH_INVALID: ${validation.errors.join("; ")}`);
  const incoming = new Map<string, Array<{ edgeId: string; source: string; sourceHandle?: string | null }>>();
  for (const edge of graph.edges) {
    const list = incoming.get(edge.target) ?? [];
    list.push({ edgeId: edge.id, source: edge.source, sourceHandle: edge.sourceHandle });
    incoming.set(edge.target, list);
  }
  return graph.nodes.map((node) => {
    const config = asObject(node.data.config);
    const conditions = incoming.get(node.id) ?? [];
    const dependsOn = conditions.map((condition) => condition.source);
    const retry = asObject(config.retry);
    return {
      run_id: context.runId,
      key: node.id,
      node_id: node.id,
      kind: node.type,
      status: "todo",
      depends_on: [...new Set(dependsOn)],
      branch_conditions: conditions,
      assignee_agent_id: (config.assigneeAgentId ?? config.agentId ?? null) as string | null,
      input: { ...config, graphInput: context.input ?? {}, label: node.data.label },
      attempts: 0,
      max_attempts: Math.min(Math.max(Number(retry.maxAttempts ?? config.maxAttempts ?? 2), 1), 8),
      timeout_ms: Math.min(Math.max(Number(config.timeoutMs ?? 120_000), 1000), 900_000),
      resource_lock: config.resourceLock ? String(config.resourceLock) : null
    };
  });
}

export function getPath(value: unknown, path: string): unknown {
  if (!path || path === "$") return value;
  return path.split(".").reduce<unknown>((current, key) => {
    if (current == null || typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[key];
  }, value);
}

export function evaluateCondition(config: Record<string, unknown>, input: unknown): { value: boolean; selectedHandle: "true" | "false"; path: unknown; details: Record<string, unknown> } {
  const path = String(config.path ?? "");
  const actual = path ? getPath(input, path) : input;
  const expected = config.equals ?? config.value;
  let value: boolean;
  if (config.expression && typeof config.expression === "object") value = evaluateCondition(asObject(config.expression), actual).value;
  else if (config.exists !== undefined) value = (actual !== undefined && actual !== null) === Boolean(config.exists);
  else if (config.contains !== undefined) value = String(actual ?? "").includes(String(config.contains));
  else if (config.gt !== undefined) value = Number(actual) > Number(config.gt);
  else if (config.lt !== undefined) value = Number(actual) < Number(config.lt);
  else if (expected !== undefined) value = actual === expected || String(actual ?? "") === String(expected);
  else value = Boolean(actual);
  return { value, selectedHandle: value ? "true" : "false", path: actual, details: { path, actual, operator: config.operator ?? "truthy" } };
}

export function evaluateRouter(config: Record<string, unknown>, input: unknown): { selectedHandle: string; route: string | null; result: unknown } {
  const routes = Array.isArray(config.routes) ? config.routes.map(asObject) : [];
  for (const route of routes) {
    const condition = asObject(route.when ?? route.condition);
    if (evaluateCondition(condition, input).value) return { selectedHandle: String(route.handle ?? route.name ?? "default"), route: String(route.name ?? route.handle ?? "default"), result: route.result ?? null };
  }
  return { selectedHandle: String(config.defaultHandle ?? "default"), route: null, result: config.defaultResult ?? null };
}

export function evaluateLoop(config: Record<string, unknown>, input: unknown): { iterations: number; items: unknown[]; completed: boolean; outputs: unknown[] } {
  const iterations = Math.min(Math.max(Math.floor(Number(config.iterations ?? 1)), 1), 100);
  const source = config.itemsPath ? getPath(input, String(config.itemsPath)) : undefined;
  const items = Array.isArray(source) ? source.slice(0, 100) : Array.from({ length: iterations }, (_, index) => ({ index, input }));
  const transform = asObject(config.transform);
  return {
    iterations: items.length,
    items,
    completed: true,
    outputs: items.map((item, index) => evaluateTransform(transform, { item, index, input }))
  };
}

export function evaluateTransform(config: Record<string, unknown>, input: unknown): unknown {
  if (config.pick && Array.isArray(config.pick)) {
    const out: Record<string, unknown> = {};
    for (const key of config.pick.map(String)) out[key] = getPath(input, key);
    return out;
  }
  if (config.mapping && typeof config.mapping === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, path] of Object.entries(asObject(config.mapping))) out[key] = getPath(input, String(path));
    return out;
  }
  if (config.constant !== undefined) return config.constant;
  return input;
}

export type TaskState = { key: string; status: TaskStatus; output?: unknown };

export function taskDecision(task: Pick<TaskRecord, "depends_on" | "branch_conditions">, byKey: Map<string, TaskState>): { ready: boolean; skip: boolean; blocked: boolean; reason: string } {
  for (const dependency of task.depends_on ?? []) {
    const state = byKey.get(dependency);
    if (!state) return { ready: false, skip: false, blocked: true, reason: `missing_dependency:${dependency}` };
    if (state.status === "skipped" || state.status === "cancelled") return { ready: false, skip: true, blocked: false, reason: `upstream_${state.status}:${dependency}` };
    if (state.status === "failed" || state.status === "blocked") return { ready: false, skip: false, blocked: true, reason: `upstream_${state.status}:${dependency}` };
    if (state.status === "waiting_approval") return { ready: false, skip: false, blocked: false, reason: `upstream_waiting_approval:${dependency}` };
    if (state.status !== "completed") return { ready: false, skip: false, blocked: false, reason: `dependency_not_ready:${dependency}` };
  }
  const conditions = (task.branch_conditions ?? []).filter((condition) => Boolean(condition.sourceHandle));
  if (conditions.length) {
    const matches = conditions.filter((condition) => {
      const source = byKey.get(condition.source);
      if (!source || source.status !== "completed") return false;
      const output = asObject(source.output);
      const selected = output.selectedHandle ?? output.route ?? output.handle;
      return selected != null && String(selected) === String(condition.sourceHandle);
    });
    if (!matches.length) return { ready: false, skip: true, blocked: false, reason: "branch_not_selected" };
  }
  return { ready: true, skip: false, blocked: false, reason: "ready" };
}

export function hasResourceConflict(task: Pick<TaskRecord, "id" | "resource_lock">, running: Array<Pick<TaskRecord, "id" | "resource_lock">>): boolean {
  if (!task.resource_lock) return false;
  return running.some((other) => other.id !== task.id && other.resource_lock === task.resource_lock);
}

const FATAL_PATTERNS = [
  "PERMISSION_DENIED", "APPROVAL_DENIED", "APPROVAL_REQUIRED", "TOOL_ROLE_DENIED", "TOOL_DENIED", "TOOL_NOT_GRANTED",
  "MODEL_PROVIDER_UNAVAILABLE", "MCP_SCHEMA_INVALID", "TOOL_SCHEMA_INVALID", "STUDIO_DISCONNECTED", "NO_PEER",
  "PROJECT_ACCESS_DENIED", "BRIDGE_CONFIRMATION_REQUIRED"
];

export function shouldRetry(task: Pick<TaskRecord, "attempts" | "max_attempts">, error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  if (FATAL_PATTERNS.some((pattern) => message.includes(pattern))) return false;
  return task.attempts < task.max_attempts;
}

export function retryDelayMs(attempts: number): number {
  return Math.min(60_000, 1000 * 2 ** Math.max(0, attempts - 1));
}

export function isTerminalStatus(status: TaskStatus): boolean {
  return status === "completed" || status === "skipped" || status === "failed" || status === "blocked" || status === "cancelled";
}
