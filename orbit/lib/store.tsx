"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import { SEED_VERSION, buildSeed } from "./seed";
import { uid } from "./util";
import type {
  ActivityEntry,
  AgentRuntime,
  Automation,
  AutomationRun,
  CalendarEvent,
  Conversation,
  KnowledgeNote,
  Message,
  OrbitFile,
  OrbitState,
  Preferences,
  Project,
  Reminder,
  SectionId,
  Task,
  ToolCall,
} from "./types";

const STORAGE_KEY = "orbit.state.v1";

type Action =
  | { type: "hydrate"; state: OrbitState }
  | { type: "prefs"; patch: Partial<Preferences> }
  | { type: "task.add"; task: Task }
  | { type: "task.patch"; id: string; patch: Partial<Task> }
  | { type: "task.remove"; id: string }
  | { type: "project.add"; project: Project }
  | { type: "project.patch"; id: string; patch: Partial<Project> }
  | { type: "project.remove"; id: string }
  | { type: "event.add"; event: CalendarEvent }
  | { type: "event.remove"; id: string }
  | { type: "reminder.add"; reminder: Reminder }
  | { type: "reminder.patch"; id: string; patch: Partial<Reminder> }
  | { type: "note.add"; note: KnowledgeNote }
  | { type: "note.patch"; id: string; patch: Partial<KnowledgeNote> }
  | { type: "file.add"; file: OrbitFile }
  | { type: "file.patch"; id: string; patch: Partial<OrbitFile> }
  | { type: "automation.add"; automation: Automation }
  | { type: "automation.patch"; id: string; patch: Partial<Automation> }
  | { type: "automation.run"; id: string; run: AutomationRun }
  | {
      type: "integration.patch";
      id: string;
      patch: Partial<OrbitState["integrations"][number]>;
    }
  | { type: "conv.add"; conversation: Conversation }
  | { type: "conv.patch"; id: string; patch: Partial<Conversation> }
  | { type: "conv.active"; id: string | null }
  | { type: "msg.add"; conversationId: string; message: Message }
  | { type: "msg.patch"; conversationId: string; messageId: string; patch: Partial<Message> }
  | { type: "tool.patch"; conversationId: string; messageId: string; toolId: string; patch: Partial<ToolCall> }
  | { type: "runtime.add"; runtime: AgentRuntime }
  | { type: "runtime.patch"; id: string; patch: Partial<AgentRuntime> }
  | { type: "activity.add"; entry: ActivityEntry }
  | { type: "reset" }
  | { type: "wizard"; patch: Partial<OrbitState> };

function touch(state: OrbitState): OrbitState {
  return { ...state };
}

function reducer(state: OrbitState, action: Action): OrbitState {
  switch (action.type) {
    case "hydrate":
      return action.state;
    case "prefs":
      return touch({ ...state, preferences: { ...state.preferences, ...action.patch } });
    case "task.add":
      return touch({ ...state, tasks: [action.task, ...state.tasks] });
    case "task.patch":
      return touch({
        ...state,
        tasks: state.tasks.map((t) =>
          t.id === action.id
            ? {
                ...t,
                ...action.patch,
                completedAt:
                  action.patch.status === "done"
                    ? new Date().toISOString()
                    : action.patch.status
                      ? null
                      : t.completedAt,
              }
            : t,
        ),
      });
    case "task.remove":
      return touch({ ...state, tasks: state.tasks.filter((t) => t.id !== action.id) });
    case "project.add":
      return touch({ ...state, projects: [action.project, ...state.projects] });
    case "project.patch":
      return touch({
        ...state,
        projects: state.projects.map((p) =>
          p.id === action.id
            ? { ...p, ...action.patch, updatedAt: new Date().toISOString() }
            : p,
        ),
      });
    case "project.remove":
      return touch({
        ...state,
        projects: state.projects.filter((p) => p.id !== action.id),
        tasks: state.tasks.map((t) =>
          t.projectId === action.id ? { ...t, projectId: null } : t,
        ),
      });
    case "event.add":
      return touch({ ...state, events: [...state.events, action.event] });
    case "event.remove":
      return touch({ ...state, events: state.events.filter((e) => e.id !== action.id) });
    case "reminder.add":
      return touch({ ...state, reminders: [action.reminder, ...state.reminders] });
    case "reminder.patch":
      return touch({
        ...state,
        reminders: state.reminders.map((r) =>
          r.id === action.id ? { ...r, ...action.patch } : r,
        ),
      });
    case "note.add":
      return touch({ ...state, notes: [action.note, ...state.notes] });
    case "note.patch":
      return touch({
        ...state,
        notes: state.notes.map((n) =>
          n.id === action.id
            ? { ...n, ...action.patch, updatedAt: new Date().toISOString() }
            : n,
        ),
      });
    case "file.add":
      return touch({ ...state, files: [action.file, ...state.files] });
    case "file.patch":
      return touch({
        ...state,
        files: state.files.map((f) => (f.id === action.id ? { ...f, ...action.patch } : f)),
      });
    case "automation.add":
      return touch({ ...state, automations: [action.automation, ...state.automations] });
    case "automation.patch":
      return touch({
        ...state,
        automations: state.automations.map((a) =>
          a.id === action.id ? { ...a, ...action.patch } : a,
        ),
      });
    case "automation.run":
      return touch({
        ...state,
        automations: state.automations.map((a) =>
          a.id === action.id
            ? {
                ...a,
                lastRunAt: action.run.at,
                runs: [action.run, ...a.runs].slice(0, 8),
              }
            : a,
        ),
      });
    case "integration.patch":
      return touch({
        ...state,
        integrations: state.integrations.map((i) =>
          i.id === action.id ? { ...i, ...action.patch } : i,
        ),
      });
    case "conv.add":
      return touch({
        ...state,
        conversations: [action.conversation, ...state.conversations],
        activeConversationId: action.conversation.id,
      });
    case "conv.patch":
      return touch({
        ...state,
        conversations: state.conversations.map((c) =>
          c.id === action.id ? { ...c, ...action.patch } : c,
        ),
      });
    case "conv.active":
      return touch({ ...state, activeConversationId: action.id });
    case "msg.add":
      return touch({
        ...state,
        conversations: state.conversations.map((c) =>
          c.id === action.conversationId
            ? {
                ...c,
                messages: [...c.messages, action.message],
                updatedAt: new Date().toISOString(),
                title:
                  c.messages.length === 0 && action.message.role === "user"
                    ? action.message.content.slice(0, 64)
                    : c.title,
              }
            : c,
        ),
      });
    case "msg.patch":
      return touch({
        ...state,
        conversations: state.conversations.map((c) =>
          c.id === action.conversationId
            ? {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === action.messageId ? { ...m, ...action.patch } : m,
                ),
              }
            : c,
        ),
      });
    case "tool.patch":
      return touch({
        ...state,
        conversations: state.conversations.map((c) =>
          c.id === action.conversationId
            ? {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === action.messageId
                    ? {
                        ...m,
                        toolCalls: (m.toolCalls ?? []).map((t) =>
                          t.id === action.toolId ? { ...t, ...action.patch } : t,
                        ),
                      }
                    : m,
                ),
              }
            : c,
        ),
      });
    case "runtime.add":
      return touch({ ...state, runtimes: [action.runtime, ...state.runtimes].slice(0, 12) });
    case "runtime.patch":
      return touch({
        ...state,
        runtimes: state.runtimes.map((r) =>
          r.id === action.id ? { ...r, ...action.patch } : r,
        ),
      });
    case "activity.add":
      return touch({ ...state, activity: [action.entry, ...state.activity].slice(0, 200) });
    case "wizard":
      return touch({ ...state, ...action.patch });
    case "reset":
      return buildSeed();
    default:
      return state;
  }
}

interface Ctx {
  state: OrbitState;
  dispatch: React.Dispatch<Action>;
  section: SectionId;
  setSection: (s: SectionId) => void;
  selection: { projectId?: string | null; taskId?: string | null; noteId?: string | null; fileId?: string | null; automationId?: string | null };
  select: (patch: Ctx["selection"]) => void;
  commandOpen: boolean;
  setCommandOpen: (v: boolean) => void;
  hydrated: boolean;
}

const StoreContext = createContext<Ctx | null>(null);

export function StoreProvider({
  children,
  initialSection = "home",
}: {
  children: React.ReactNode;
  /** Test/deep-link seam: lets the shell boot straight into a section. */
  initialSection?: SectionId;
}) {
  const [state, dispatch] = useReducer(reducer, undefined, buildSeed);
  const [section, setSectionRaw] = useState<SectionId>(initialSection);
  const [selection, setSelection] = useState<Ctx["selection"]>({});
  const [commandOpen, setCommandOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const persisted = useRef(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as OrbitState & { seedVersion?: number };
        if (parsed.seedVersion === SEED_VERSION) {
          dispatch({ type: "hydrate", state: { ...buildSeed(), ...parsed } });
        }
      }
      const hash = window.location.hash.replace("#", "");
      if (hash) setSectionRaw(hash as SectionId);
    } catch {
      /* a corrupt blob must never lock the operator out */
    }
    setHydrated(true);
    persisted.current = true;
  }, []);

  useEffect(() => {
    if (!hydrated || !persisted.current) return;
    const id = window.setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      } catch {
        /* quota: state stays in memory */
      }
    }, 240);
    return () => window.clearTimeout(id);
  }, [state, hydrated]);

  // Accent preference is a real theme variable, not a decorative setting.
  useEffect(() => {
    const map: Record<string, { accent: string; dim: string }> = {
      indigo: { accent: "#7c9cff", dim: "#2d3d6b" },
      mint: { accent: "#4ade9b", dim: "#1d5340" },
      violet: { accent: "#a78bfa", dim: "#3f3168" },
      amber: { accent: "#f5b556", dim: "#5a4420" },
    };
    const entry = map[state.preferences.accent] ?? map.indigo;
    document.documentElement.style.setProperty("--color-accent", entry.accent);
    document.documentElement.style.setProperty("--color-accent-dim", entry.dim);
    document.body.style.background = "var(--color-void)";
  }, [state.preferences.accent]);

  // Reduced motion preference feeds the existing media query as a class.
  useEffect(() => {
    document.documentElement.classList.toggle(
      "motion-reduce",
      state.preferences.reducedMotion,
    );
  }, [state.preferences.reducedMotion]);

  const setSection = useCallback((s: SectionId) => {
    setSectionRaw(s);
    window.history.replaceState(null, "", `#${s}`);
  }, []);

  const select = useCallback((patch: Ctx["selection"]) => {
    setSelection((prev) => ({ ...prev, ...patch }));
  }, []);

  useEffect(() => {
    const onHash = () => {
      const hash = window.location.hash.replace("#", "") as SectionId;
      if (hash) setSectionRaw(hash);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const value = useMemo(
    () => ({
      state,
      dispatch,
      section,
      setSection,
      selection,
      select,
      commandOpen,
      setCommandOpen,
      hydrated,
    }),
    [state, section, setSection, selection, select, commandOpen, hydrated],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useOrbit() {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useOrbit must be used inside StoreProvider");
  return ctx;
}

/* ------------------------------------------------------------- action kit */
/** Typed convenience mutators shared by every screen and the agent runtime. */
export function useActions() {
  const { dispatch } = useOrbit();

  return useMemo(
    () => ({
      addTask(input: Partial<Task> & { title: string }) {
        const task: Task = {
          id: uid("tsk"),
          title: input.title,
          notes: input.notes ?? "",
          projectId: input.projectId ?? null,
          status: input.status ?? "todo",
          priority: input.priority ?? "medium",
          due: input.due ?? null,
          estimateMin: input.estimateMin ?? null,
          tags: input.tags ?? [],
          assignee: input.assignee ?? null,
          createdAt: new Date().toISOString(),
          completedAt: null,
        };
        dispatch({ type: "task.add", task });
        return task;
      },
      patchTask(id: string, patch: Partial<Task>) {
        dispatch({ type: "task.patch", id, patch });
      },
      addProject(input: Partial<Project> & { name: string }) {
        const project: Project = {
          id: uid("prj"),
          name: input.name,
          description: input.description ?? "",
          objectives: input.objectives ?? [],
          status: input.status ?? "planning",
          priority: input.priority ?? "medium",
          color: input.color ?? "#7c9cff",
          start: input.start ?? new Date().toISOString(),
          target: input.target ?? null,
          progress: 0,
          milestones: input.milestones ?? [],
          pinned: input.pinned ?? false,
          tags: input.tags ?? [],
          lead: input.lead ?? "You",
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        };
        dispatch({ type: "project.add", project });
        return project;
      },
      patchProject(id: string, patch: Partial<Project>) {
        dispatch({ type: "project.patch", id, patch });
      },
      addEvent(input: Partial<CalendarEvent> & { title: string; start: string; end: string }) {
        const event: CalendarEvent = {
          id: uid("ev"),
          title: input.title,
          start: input.start,
          end: input.end,
          kind: input.kind ?? "event",
          location: input.location ?? null,
          projectId: input.projectId ?? null,
          source: input.source ?? "orbit",
        };
        dispatch({ type: "event.add", event });
        return event;
      },
      addReminder(input: Partial<Reminder> & { text: string; at: string }) {
        const reminder: Reminder = {
          id: uid("rem"),
          text: input.text,
          at: input.at,
          repeat: input.repeat ?? "none",
          done: false,
        };
        dispatch({ type: "reminder.add", reminder });
        return reminder;
      },
      addNote(input: Partial<KnowledgeNote> & { title: string; body: string }) {
        const note: KnowledgeNote = {
          id: uid("kn"),
          title: input.title,
          body: input.body,
          collection: input.collection ?? "Inbox",
          tags: input.tags ?? [],
          source: input.source ?? "internal",
          linkedProjectId: input.linkedProjectId ?? null,
          updatedAt: new Date().toISOString(),
          pinned: input.pinned ?? false,
        };
        dispatch({ type: "note.add", note });
        return note;
      },
      addFile(input: Partial<OrbitFile> & { name: string }) {
        const file: OrbitFile = {
          id: uid("fl"),
          name: input.name,
          kind: input.kind ?? "md",
          sizeKb: input.sizeKb ?? 24,
          collection: input.collection ?? "Inbox",
          summary: input.summary ?? "Awaiting analysis.",
          tags: input.tags ?? [],
          uploadedAt: new Date().toISOString(),
          analyzed: false,
          projectId: input.projectId ?? null,
        };
        dispatch({ type: "file.add", file });
        return file;
      },
      addAutomation(input: Partial<Automation> & { name: string }) {
        const automation: Automation = {
          id: uid("auto"),
          name: input.name,
          description: input.description ?? "",
          trigger: input.trigger ?? "manual",
          triggerDetail: input.triggerDetail ?? "Manual run",
          enabled: input.enabled ?? true,
          requiresApproval: input.requiresApproval ?? false,
          steps: input.steps ?? [
            { id: uid("st"), kind: "trigger", title: input.triggerDetail ?? "Manual", detail: "Fired by operator", actor: "Automation Agent" },
            { id: uid("st"), kind: "action", title: "Run workflow", detail: "Execute configured steps", actor: "Automation Agent" },
          ],
          lastRunAt: null,
          nextRunAt: null,
          successRate: 0,
          runs: [],
          tags: input.tags ?? [],
        };
        dispatch({ type: "automation.add", automation });
        return automation;
      },
      patchFile(id: string, patch: Partial<OrbitFile>) {
        dispatch({ type: "file.patch", id, patch });
      },
      patchReminder(id: string, patch: Partial<Reminder>) {
        dispatch({ type: "reminder.patch", id, patch });
      },
      removeTask(id: string) {
        dispatch({ type: "task.remove", id });
      },
      patchAutomation(id: string, patch: Partial<Automation>) {
        dispatch({ type: "automation.patch", id, patch });
      },
      dispatchRun(id: string, run: AutomationRun) {
        dispatch({ type: "automation.run", id, run });
        dispatch({
          type: "activity.add",
          entry: {
            id: `ac_${Date.now()}`,
            at: run.at,
            kind: "automation",
            title: "Automation run",
            detail: `${run.status} · ${run.durationMs}ms`,
            actor: "Automation Agent",
            status: run.status === "success" ? "ok" : "error",
          },
        });
      },
      logActivity(entry: Omit<ActivityEntry, "id" | "at">) {
        dispatch({
          type: "activity.add",
          entry: { ...entry, id: uid("ac"), at: new Date().toISOString() },
        });
      },
      resetAll() {
        dispatch({ type: "reset" });
      },
      setPrefs(patch: Partial<Preferences>) {
        dispatch({ type: "prefs", patch });
      },
    }),
    [dispatch],
  );
}
