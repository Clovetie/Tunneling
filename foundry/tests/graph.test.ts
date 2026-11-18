import { describe, expect, it } from "vitest";
import { compileGraphToTasks, evaluateCondition, evaluateRouter, hasResourceConflict, isTerminalStatus, shouldRetry, taskDecision, topologicalOrder, validateWorkflowGraph } from "@/lib/orchestrator/graph";
import type { TaskStatus, WorkflowGraph } from "@/lib/types";

const graph: WorkflowGraph = {
  nodes: [
    { id: "start", type: "task", position: { x: 0, y: 0 }, data: { label: "Start", config: { title: "Start" } } },
    { id: "condition", type: "condition", position: { x: 200, y: 0 }, data: { label: "Cond", config: { path: "ok", equals: true } } },
    { id: "yes", type: "output", position: { x: 400, y: -80 }, data: { label: "Yes", config: { format: "json" } } },
    { id: "no", type: "output", position: { x: 400, y: 80 }, data: { label: "No", config: { format: "text" } } }
  ],
  edges: [
    { id: "e1", source: "start", target: "condition" },
    { id: "e2", source: "condition", target: "yes", sourceHandle: "true" },
    { id: "e3", source: "condition", target: "no", sourceHandle: "false" }
  ]
};

describe("workflow graph", () => {
  it("validates a DAG with conditional branches", () => {
    const result = validateWorkflowGraph(graph);
    expect(result.valid).toBe(true);
    expect(result.order[0]).toBe("start");
  });
  it("rejects cycles, duplicate ids and unsupported nodes", () => {
    const bad = { ...graph, edges: [...graph.edges, { id: "cycle", source: "yes", target: "start" }] } as WorkflowGraph;
    expect(validateWorkflowGraph(bad).valid).toBe(false);
    expect(validateWorkflowGraph({ nodes: [graph.nodes[0], graph.nodes[0]], edges: [] }).valid).toBe(false);
    expect(validateWorkflowGraph({ nodes: [{ ...graph.nodes[0], type: "unknown" }], edges: [] }).valid).toBe(false);
  });
  it("compiles durable task records with dependencies and retry policies", () => {
    const tasks = compileGraphToTasks(graph, { ownerId: "owner", runId: "run", input: { ok: true } });
    expect(tasks).toHaveLength(4);
    const condition = tasks.find((task) => task.key === "condition")!;
    expect(condition.depends_on).toEqual(["start"]);
    expect(condition.max_attempts).toBe(2);
    expect(tasks.find((task) => task.key === "yes")!.branch_conditions?.[0]?.sourceHandle).toBe("true");
  });
  it("topologically orders parallel branches", () => {
    const order = topologicalOrder(graph);
    expect(order.indexOf("start")).toBeLessThan(order.indexOf("condition"));
    expect(order.indexOf("condition")).toBeLessThan(order.indexOf("yes"));
    expect(order.indexOf("condition")).toBeLessThan(order.indexOf("no"));
  });
});

describe("task coordination", () => {
  it("evaluates conditions and routers without arbitrary code execution", () => {
    expect(evaluateCondition({ path: "ok", equals: true }, { ok: true }).selectedHandle).toBe("true");
    expect(evaluateCondition({ path: "ok", equals: true }, { ok: false }).selectedHandle).toBe("false");
    expect(evaluateRouter({ routes: [{ handle: "b", when: { path: "value", gt: 2 } }], defaultHandle: "fallback" }, { value: 3 }).selectedHandle).toBe("b");
  });
  it("skips unselected branches and cascades skipped dependencies", () => {
    const byKey = new Map<string, { key: string; status: TaskStatus; output?: unknown }>([
      ["start", { key: "start", status: "completed" as const }],
      ["condition", { key: "condition", status: "completed" as const, output: { selectedHandle: "true" } }]
    ]);
    const yes = compileGraphToTasks(graph, { ownerId: "o", runId: "r" }).find((task) => task.key === "yes")!;
    const no = compileGraphToTasks(graph, { ownerId: "o", runId: "r" }).find((task) => task.key === "no")!;
    expect(taskDecision(yes, byKey).ready).toBe(true);
    expect(taskDecision(no, byKey).skip).toBe(true);
    byKey.set("no", { key: "no", status: "skipped" });
    expect(taskDecision({ depends_on: ["no"], branch_conditions: [] }, byKey).skip).toBe(true);
  });
  it("detects resource conflicts and fatal retry boundaries", () => {
    expect(hasResourceConflict({ id: "a", resource_lock: "scripts" }, [{ id: "b", resource_lock: "scripts" }])).toBe(true);
    expect(hasResourceConflict({ id: "a", resource_lock: null }, [{ id: "b", resource_lock: null }])).toBe(false);
    expect(shouldRetry({ attempts: 1, max_attempts: 3 }, new Error("temporary timeout"))).toBe(true);
    expect(shouldRetry({ attempts: 1, max_attempts: 3 }, new Error("PERMISSION_DENIED"))).toBe(false);
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("todo")).toBe(false);
  });
});
