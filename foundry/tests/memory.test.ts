import { describe, expect, it } from "vitest";
import { buildMemoryScopeSql, validateMemoryScope } from "@/lib/memory";
import { chunkText } from "@/lib/knowledge";

describe("memory isolation", () => {
  it("requires the identifiers that define each scope", () => {
    expect(() => validateMemoryScope({ scope: "agent" }, true)).toThrow(/agentId/);
    expect(() => validateMemoryScope({ scope: "project" }, true)).toThrow(/projectId/);
    expect(() => validateMemoryScope({ scope: "team", projectId: "p" }, true)).toThrow(/teamId/);
    expect(() => validateMemoryScope({ scope: "workflow" }, true)).toThrow(/runId/);
    expect(validateMemoryScope({ scope: "agent", agentId: "a" }, true)).toMatchObject({ scope: "agent" });
  });
  it("builds SQL that never mixes unrelated project or private agent contexts", () => {
    const agent = buildMemoryScopeSql("owner", { scope: "agent", agentId: "a" });
    expect(agent.sql).toContain("agent_id = $3");
    expect(agent.sql).toContain("explicitShare");
    expect(agent.params).toEqual(["owner", "agent", "a"]);
    const project = buildMemoryScopeSql("owner", { scope: "project", projectId: "p" });
    expect(project.sql).toContain("project_id = $3");
    expect(project.params).toEqual(["owner", "project", "p"]);
    const workflow = buildMemoryScopeSql("owner", { scope: "workflow", runId: "r", projectId: "p" });
    expect(workflow.sql).toContain("run_id = $3");
    expect(workflow.sql).toContain("project_id = $4");
    expect(workflow.params).toEqual(["owner", "workflow", "r", "p"]);
  });
  it("chunks knowledge deterministically without losing content", () => {
    const text = Array.from({ length: 200 }, (_, i) => `line ${i}: ${"x".repeat(20)}`).join("\n");
    const chunks = chunkText(text, 600, 80);
    expect(chunks.length).toBeGreaterThan(4);
    expect(chunks.join("\n").length).toBeGreaterThan(3000);
    expect(chunkText("short", 600, 80)).toEqual(["short"]);
  });
});
