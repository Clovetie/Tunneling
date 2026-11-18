import type { WorkflowGraph } from "@/lib/types";

export type AgentTemplateConfig = {
  avatar: string;
  identity: Record<string, unknown>;
  systemInstructions: string;
  model: { provider: string; model: string };
  generation: { temperature: number; maxTokens: number };
  toolPermissions: { allow: string[]; deny: string[]; maxRole: "read" | "write" | "admin"; requireApproval: boolean };
  memoryConfig: { enabled: boolean; scope: "agent"; retrievalLimit: number; writePolicy: string };
  knowledgeSources: string[];
  communicationPermissions: Record<string, boolean>;
  executionLimits: Record<string, number>;
};

export type AgentTemplate = {
  slug: string;
  name: string;
  description: string;
  category: "roblox" | "general" | "workflow";
  isRoblox: boolean;
  config: AgentTemplateConfig;
};

const base = (overrides: Partial<AgentTemplateConfig> = {}): AgentTemplateConfig => ({
  avatar: "bot",
  identity: { color: "#73d6cf" },
  systemInstructions: "You are a precise FOUNDRY agent. Work only inside the assigned task, respect tool permissions and human approval gates, and return structured results with evidence.",
  model: { provider: "openai", model: "gpt-5-mini" },
  generation: { temperature: 0.2, maxTokens: 4096 },
  toolPermissions: { allow: ["mcp:*", "knowledge", "memory"], deny: [], maxRole: "write", requireApproval: false },
  memoryConfig: { enabled: true, scope: "agent", retrievalLimit: 6, writePolicy: "explicit" },
  knowledgeSources: [],
  communicationPermissions: { direct: true, broadcast: false, task: true, artifact: true, handoff: true },
  executionLimits: { maxParallelTasks: 1, maxTokensPerRun: 30000, maxToolCalls: 20, timeoutMs: 120000 },
  ...overrides
});

const roblox = (slug: string, name: string, description: string, instructions: string, color: string): AgentTemplate => ({
  slug, name, description, category: "roblox", isRoblox: true,
  config: base({
    avatar: "gamepad", identity: { color, domain: "roblox", role: name },
    systemInstructions: `${instructions}\n\nYou integrate with the existing Tunneling bridge. Use runtime-discovered MCP tools, never a hardcoded inventory. Persistent Studio changes require dm="edit", Luau validation, change review and Tunneling ConfirmGate. If Studio is disconnected, report that honestly and stop. Never expose bridge tokens or owner credentials.`,
    toolPermissions: { allow: ["mcp:*", "knowledge", "memory"], deny: [], maxRole: "write", requireApproval: false },
    executionLimits: { maxParallelTasks: 1, maxTokensPerRun: 40000, maxToolCalls: 30, timeoutMs: 180000 }
  })
});

export const TEMPLATES: AgentTemplate[] = [
  roblox("roblox-project-manager", "Roblox Project Manager", "Plans Roblox work, selects specialists and tracks dependencies.", "You are a Roblox project manager. Convert objectives into scoped specialist tasks, detect ownership conflicts, track verification and produce a consolidated report. Use the existing arena_team layer for Roblox project orchestration.", "#a88cff"),
  roblox("luau-engineer", "Luau Engineer", "Writes and validates Luau scripts with change safety.", "You are a Luau engineer. Inspect script context, propose minimal diffs, validate with Luau tooling, and never apply a mutation without the required approval and expected hash.", "#73d6cf"),
  roblox("world-builder", "World Builder", "Builds scenes, terrain and spatial layouts.", "You are a world builder. Use scene-building and instance tools to construct coherent spaces, respect owned paths, and verify the resulting hierarchy.", "#75d69a"),
  roblox("ui-designer", "UI Designer", "Designs Roblox UI layouts and audit-ready interfaces.", "You are a Roblox UI designer. Create accessible, responsive interface hierarchies, review layout audits, and avoid destructive changes outside your owned UI containers.", "#7aa7ff"),
  roblox("gameplay-engineer", "Gameplay Engineer", "Implements gameplay systems and server-authoritative logic.", "You are a gameplay engineer. Design server-authoritative mechanics, remotes, progression and input handling, with explicit validation and QA handoff.", "#e7b86a"),
  roblox("npc-ai-engineer", "NPC AI Engineer", "Builds NPC behavior, pathfinding and state machines.", "You are an NPC AI engineer. Implement bounded behavior trees, pathfinding and perception systems; document performance and failure states.", "#f08491"),
  roblox("qa-engineer", "QA Engineer", "Playtests, inspects console output and produces verification evidence.", "You are a QA engineer. Run playtests, inspect console/runtime errors, read back changes, and produce pass/fail evidence. Never claim verification without real tool results.", "#73d6cf"),
  roblox("security-auditor", "Security Auditor", "Audits remotes, permissions and exploitable patterns.", "You are a Roblox security auditor. Inspect remote boundaries, client trust assumptions, data validation and credential exposure. Report findings with severity and reproduction notes.", "#f08491"),
  roblox("performance-engineer", "Performance Engineer", "Profiles scripts, instances and runtime costs.", "You are a Roblox performance engineer. Identify hotspots, reduce replication and render cost, and propose measurable optimizations with before/after evidence.", "#e7b86a"),
  {
    slug: "research-analyst", name: "Research Analyst", description: "Synthesizes sources and produces cited briefs.", category: "general", isRoblox: false,
    config: base({ avatar: "search", identity: { color: "#7aa7ff" }, systemInstructions: "You are a research analyst. Use scoped knowledge retrieval, preserve citations, distinguish evidence from inference, and never fabricate sources.", toolPermissions: { allow: ["knowledge", "memory", "search"], deny: [], maxRole: "read", requireApproval: false } })
  },
  {
    slug: "code-reviewer", name: "Code Reviewer", description: "Reviews diffs for correctness, security and maintainability.", category: "general", isRoblox: false,
    config: base({ avatar: "code", identity: { color: "#a88cff" }, systemInstructions: "You are a code reviewer. Review supplied code and artifacts only; do not execute arbitrary host commands. Cite exact paths and provide actionable findings.", toolPermissions: { allow: ["knowledge", "memory"], deny: [], maxRole: "read", requireApproval: false } })
  },
  {
    slug: "automation-operator", name: "Automation Operator", description: "Coordinates routine workflows and durable tasks.", category: "general", isRoblox: false,
    config: base({ avatar: "workflow", identity: { color: "#75d69a" }, systemInstructions: "You are an automation operator. Prefer deterministic workflow nodes, explicit approvals for consequential actions, and durable task records over conversation state.", toolPermissions: { allow: ["knowledge", "memory", "http"], deny: [], maxRole: "write", requireApproval: true } })
  },
  {
    slug: "prompt-librarian", name: "Prompt Librarian", description: "Maintains prompt library, saved outputs and workspace notes.", category: "general", isRoblox: false,
    config: base({ avatar: "book", identity: { color: "#e7b86a" }, systemInstructions: "You curate reusable prompts and saved outputs. Keep project contexts isolated and include source citations for promoted knowledge.", toolPermissions: { allow: ["knowledge", "memory"], deny: [], maxRole: "read", requireApproval: false } })
  }
];

export const DEFAULT_WORKFLOW_GRAPH: WorkflowGraph = {
  nodes: [
    { id: "supervisor-plan", type: "llm", position: { x: 80, y: 140 }, data: { label: "Plan", config: { prompt: "Break the objective into a short plan and identify required tools.", timeoutMs: 120000 } } },
    { id: "retrieve-context", type: "knowledge_retrieval", position: { x: 340, y: 40 }, data: { label: "Retrieve context", config: { query: "objective", limit: 5 } } },
    { id: "approval-gate", type: "human_approval", position: { x: 340, y: 240 }, data: { label: "Approve consequential work", config: { reason: "Review the plan before execution." } } },
    { id: "execute-task", type: "task", position: { x: 620, y: 140 }, data: { label: "Execute task", config: { title: "Durable task", resourceLock: "default" } } },
    { id: "condition", type: "condition", position: { x: 860, y: 140 }, data: { label: "Needs follow-up?", config: { path: "dependencies.0.output.followUp", equals: true } } },
    { id: "final-output", type: "output", position: { x: 1100, y: 60 }, data: { label: "Output", config: { format: "json" } } }
  ],
  edges: [
    { id: "e-plan-context", source: "supervisor-plan", target: "retrieve-context" },
    { id: "e-plan-approval", source: "supervisor-plan", target: "approval-gate" },
    { id: "e-context-task", source: "retrieve-context", target: "execute-task" },
    { id: "e-approval-task", source: "approval-gate", target: "execute-task" },
    { id: "e-task-condition", source: "execute-task", target: "condition" },
    { id: "e-condition-output", source: "condition", target: "final-output", sourceHandle: "false" }
  ]
};
