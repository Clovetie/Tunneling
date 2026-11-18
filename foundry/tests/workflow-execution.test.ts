import { describe, expect, it } from "vitest";
import { compileGraphToTasks, evaluateCondition, evaluateLoop, evaluateTransform, retryDelayMs, shouldRetry, taskDecision, isTerminalStatus } from "@/lib/orchestrator/graph";
import { DEFAULT_WORKFLOW_GRAPH } from "@/lib/templates";

describe("workflow execution and recovery", () => {
  it("simulates a validated graph through terminal task states", () => {
    const tasks = compileGraphToTasks(DEFAULT_WORKFLOW_GRAPH, { ownerId: "o", runId: "r", input: { objective: "test" } });
    const states = new Map<string, { key: string; status: "todo" | "running" | "waiting_approval" | "blocked" | "failed" | "completed" | "skipped" | "cancelled"; output?: unknown }>();
    for (const task of tasks) states.set(task.key, { key: task.key, status: "todo" });
    for (const task of tasks) {
      const decision = taskDecision(task, states);
      if (decision.ready || decision.skip) states.set(task.key, { key: task.key, status: decision.skip ? "skipped" : "completed", output: decision.skip ? { skipped: true } : { ok: true, selectedHandle: task.kind === "condition" ? "false" : undefined } });
    }
    expect([...states.values()].every((state) => isTerminalStatus(state.status))).toBe(true);
    expect(states.get("final-output")?.status).toBe("completed");
  });
  it("supports deterministic loop and transform nodes", () => {
    expect(evaluateLoop({ itemsPath: "items", transform: { mapping: { index: "index" } } }, { items: [1, 2, 3] }).outputs).toEqual([{ index: 0 }, { index: 1 }, { index: 2 }]);
    expect(evaluateTransform({ pick: ["user.name"] }, { user: { name: "Ada" } })).toEqual({ "user.name": "Ada" });
    expect(evaluateCondition({ path: "count", gt: 2 }, { count: 3 }).value).toBe(true);
  });
  it("recovers transient failures but not fatal permission or approval failures", () => {
    expect(shouldRetry({ attempts: 1, max_attempts: 3 }, new Error("ECONNRESET"))).toBe(true);
    expect(shouldRetry({ attempts: 3, max_attempts: 3 }, new Error("ECONNRESET"))).toBe(false);
    expect(shouldRetry({ attempts: 1, max_attempts: 3 }, new Error("APPROVAL_DENIED"))).toBe(false);
    expect(shouldRetry({ attempts: 1, max_attempts: 3 }, new Error("STUDIO_DISCONNECTED"))).toBe(false);
    expect(retryDelayMs(1)).toBe(1000);
    expect(retryDelayMs(3)).toBe(4000);
  });
});
