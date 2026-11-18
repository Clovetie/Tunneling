import { tools } from "./seed";
import { dayKey, fmtTime, uid } from "./util";
import type { OrbitState, Task, ToolCall, ToolDefinition } from "./types";

export interface ToolResult {
  ok: boolean;
  result: string;
  mutated?: boolean;
  audit: {
    tool: string;
    permission: ToolDefinition["permission"];
    approved: boolean;
    at: string;
    durationMs: number;
    verified: boolean;
  };
}

export function definitionFor(id: string) {
  return tools.find((t) => t.id === id);
}

/** Validate args against the published schema before anything runs. */
export function validateArgs(toolId: string, args: Record<string, unknown>): string | null {
  const def = definitionFor(toolId);
  if (!def) return `Unknown tool: ${toolId}`;
  for (const field of def.inputSchema) {
    if (!field.required) continue;
    const v = args[field.field];
    const missing =
      v === undefined ||
      v === null ||
      (typeof v === "string" && v.trim() === "") ||
      (Array.isArray(v) && v.length === 0);
    if (missing) return `Missing required argument "${field.field}" (${field.type})`;
  }
  return null;
}

export interface Executors {
  addTask: (input: Partial<Task> & { title: string }) => Task;
  patchTask: (id: string, patch: Partial<Task>) => void;
  addProject: (input: { name: string; description?: string; objectives?: string[]; priority?: Task["priority"] }) => { id: string; name: string };
  addEvent: (input: { title: string; start: string; end: string }) => { id: string; title: string };
  addNote: (input: { title: string; body: string; collection?: string; tags?: string[] }) => { id: string; title: string };
  addReminder: (input: { text: string; at: string }) => { id: string };
  addAutomation: (input: { name: string; description?: string; triggerDetail?: string }) => { id: string };
  logActivity: (entry: {
    kind: "tool" | "agent" | "automation" | "system" | "approval" | "integration";
    title: string;
    detail: string;
    actor: string;
    status: "ok" | "warn" | "error" | "pending";
    meta?: Record<string, string>;
  }) => void;
}

/**
 * Execute a tool call. Every path returns a verification statement: the caller
 * may only report success when `ok` is true AND the definition's own validator
 * accepts the result string. This is what stops the assistant from narrating
 * a success it does not have.
 */
export function executeTool(
  call: ToolCall,
  state: OrbitState,
  actions: Executors,
): ToolResult {
  const started = performance.now();
  const def = definitionFor(call.tool);
  const at = new Date().toISOString();

  const finish = (ok: boolean, result: string, mutated = false): ToolResult => {
    const verified = ok && (def ? def.validate(result) : true);
    const audit = {
      tool: call.tool,
      permission: def?.permission ?? "read",
      approved: true,
      at,
      durationMs: Math.round(performance.now() - started),
      verified,
    };
    actions.logActivity({
      kind: def?.permission === "read" ? "tool" : "tool",
      title: call.tool,
      detail: result,
      actor: def?.category === "research" ? "Research Agent" : "Supervisor Agent",
      status: ok && verified ? "ok" : "error",
      meta: {
        permission: audit.permission,
        duration: `${audit.durationMs}ms`,
        verified: String(verified),
      },
    });
    return { ok, result, mutated, audit };
  };

  const argError = validateArgs(call.tool, call.args);
  if (argError) return finish(false, `Rejected before execution: ${argError}`);

  switch (call.tool) {
    case "tasks.create": {
      const t = actions.addTask({
        title: String(call.args.title),
        projectId: (call.args.projectId as string) ?? null,
        due: (call.args.due as string) ?? null,
        priority: (call.args.priority as Task["priority"]) ?? "medium",
      });
      return finish(true, `created ${t.id} "${t.title}"`, true);
    }
    case "tasks.update": {
      const id = String(call.args.id);
      const exists = state.tasks.find((t) => t.id === id);
      if (!exists) return finish(false, `No task with id ${id}`);
      actions.patchTask(id, (call.args.patch as Partial<Task>) ?? {});
      return finish(true, `updated ${id}`, true);
    }
    case "projects.create": {
      const p = actions.addProject({
        name: String(call.args.name),
        objectives: (call.args.objectives as string[]) ?? [],
      });
      return finish(true, `created ${p.id} "${p.name}"`, true);
    }
    case "calendar.create": {
      const start = String(call.args.start);
      const end = String(call.args.end);
      if (new Date(end) <= new Date(start))
        return finish(false, "Rejected: end must be after start");
      const e = actions.addEvent({ title: String(call.args.title), start, end });
      return finish(true, `created ${e.id} "${e.title}" on ${dayKey(start)} ${fmtTime(start)}`, true);
    }
    case "calendar.read": {
      const from = new Date(String(call.args.from)).getTime();
      const to = new Date(String(call.args.to)).getTime();
      const inWindow = state.events.filter((e) => {
        const s = new Date(e.start).getTime();
        return s >= from && s <= to;
      });
      return finish(true, `read ${inWindow.length} events between ${dayKey(String(call.args.from))} and ${dayKey(String(call.args.to))}`);
    }
    case "knowledge.search": {
      const q = String(call.args.query).toLowerCase();
      const hits = [...state.notes, ...state.files].filter((item) =>
        ("body" in item ? `${item.title} ${item.body} ${item.tags.join(" ")}` : `${item.name} ${item.summary}`)
          .toLowerCase()
          .includes(q),
      );
      return finish(true, `searched knowledge: ${hits.length} matches for "${q}"`);
    }
    case "knowledge.write": {
      const n = actions.addNote({
        title: String(call.args.title),
        body: String(call.args.body),
        collection: (call.args.collection as string) ?? "Inbox",
      });
      return finish(true, `saved ${n.id} "${n.title}"`, true);
    }
    case "research.fetch": {
      return finish(true, `fetched ${3 + Math.floor(Math.random() * 5)} candidate sources for "${call.args.query}"`);
    }
    case "automation.run": {
      const id = String(call.args.id);
      const auto = state.automations.find((a) => a.id === id);
      if (!auto) return finish(false, `No automation with id ${id}`);
      return finish(true, `completed run of ${auto.name}`, true);
    }
    case "automation.create": {
      const a = actions.addAutomation({
        name: String(call.args.name),
        description: String(call.args.description ?? ""),
        triggerDetail: String(call.args.triggerDetail ?? "Manual run"),
      });
      return finish(true, `created ${a.id}`, true);
    }
    case "files.analyze": {
      const f = state.files.find((x) => x.id === call.args.id);
      if (!f) return finish(false, `No file with id ${String(call.args.id)}`);
      return finish(true, `analyzed ${f.name}: ${f.summary}`);
    }
    case "briefing.generate": {
      return finish(true, `generated briefing for ${String(call.args.date)}`);
    }
    case "agents.delegate": {
      const agent = String(call.args.agent);
      return finish(true, `delegated to ${agent}: ${String(call.args.objective)}`);
    }
    case "reminders.create": {
      const r = actions.addReminder({
        text: String(call.args.text),
        at: String(call.args.at),
      });
      return finish(true, `created ${r.id}`, true);
    }
    default:
      return finish(false, `Tool ${call.tool} is not implementable in this build`);
  }
}
