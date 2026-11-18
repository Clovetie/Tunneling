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
import { activeTab, closeTab, createWorkspace, openTab, resolveTabLabel, stepTab, tabId } from "./tabs";
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
  Workspace,
  WorkspaceTab,
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
  | { type: "tab.open"; section: SectionId; entityId: string | null; label: string }
  | { type: "tab.close"; id: string }
  | { type: "tab.activate"; id: string }
  | { type: "tab.pin"; id: string }
  | { type: "tab.reopen" }
  | { type: "tab.step"; dir: 1 | -1 }
  | { type: "tab.clear" }
  | { type: "workspace.add"; name: string }
  | { type: "workspace.switch"; id: string }
  | { type: "workspace.remove"; id: string }
  | { type: "workspace.rename"; id: string; name: string }
  | { type: "navigate"; section: SectionId; selection?: Ctx["selection"] }
  | { type: "reset" }
  | { type: "wizard"; patch: Partial<OrbitState> };

function touch(state: OrbitState): OrbitState {
  return { ...state };
}

type WorkspaceAction = Extract<
  Action,
  {
    type:
      | "tab.open"
      | "tab.activate"
      | "tab.close"
      | "tab.pin"
      | "tab.reopen"
      | "tab.step"
      | "tab.clear"
      | "workspace.add"
      | "workspace.switch"
      | "workspace.remove"
      | "workspace.rename"
      | "navigate";
  }
>;

function withWorkspace(state: OrbitState, workspace: Workspace): OrbitState {
  return {
    ...state,
    workspaces: state.workspaces.map((w) => (w.id === workspace.id ? workspace : w)),
  };
}

function currentWorkspace(state: OrbitState): Workspace {
  return (
    state.workspaces.find((w) => w.id === state.activeWorkspaceId) ??
    state.workspaces[0] ??
    createWorkspace("ws_default", "Main")
  );
}

/**
 * Tabs and workspaces are a projection concern: they decide *what is on screen*
 * and never mutate domain data. Kept separate from the domain reducer so the
 * two cannot entangle.
 */
function workspaceReducer(state: OrbitState, action: WorkspaceAction): OrbitState {
  const ws = currentWorkspace(state);

  switch (action.type) {
    case "navigate": {
      const tab: WorkspaceTab = {
        id: tabId(action.section, null),
        section: action.section,
        entityId: null,
        label: resolveTabLabel(state, action.section, null),
        pinned: false,
      };
      const tabs = openTab(ws.tabs, tab);
      return withWorkspace(state, {
        ...ws,
        tabs,
        activeTabId: tab.id,
        section: action.section,
        selection: action.selection ?? {},
      });
    }
    case "tab.open": {
      const tab: WorkspaceTab = {
        id: tabId(action.section, action.entityId),
        section: action.section,
        entityId: action.entityId,
        label: action.label,
        pinned: false,
      };
      const tabs = openTab(ws.tabs, tab);
      const selection: Workspace["selection"] = {
        projectId: action.section === "projects" ? action.entityId : ws.selection.projectId,
        noteId: action.section === "knowledge" ? action.entityId : ws.selection.noteId,
        fileId: action.section === "files" ? action.entityId : ws.selection.fileId,
        automationId:
          action.section === "automations" ? action.entityId : ws.selection.automationId,
        taskId: action.section === "tasks" ? action.entityId : ws.selection.taskId,
      };
      return withWorkspace(state, {
        ...ws,
        tabs,
        activeTabId: tab.id,
        section: action.section,
        selection,
      });
    }
    case "tab.activate": {
      const tab = ws.tabs.find((t) => t.id === action.id);
      if (!tab) return state;
      return withWorkspace(state, {
        ...ws,
        activeTabId: tab.id,
        section: tab.section,
        selection: {
          projectId: tab.section === "projects" ? tab.entityId : null,
          noteId: tab.section === "knowledge" ? tab.entityId : null,
          fileId: tab.section === "files" ? tab.entityId : null,
          automationId: tab.section === "automations" ? tab.entityId : null,
          taskId: tab.section === "tasks" ? tab.entityId : null,
        },
      });
    }
    case "tab.close": {
      const tab = ws.tabs.find((t) => t.id === action.id);
      const { tabs, activeTabId } = closeTab(ws.tabs, action.id, ws.activeTabId);
      const nextActive = tabs.find((t) => t.id === activeTabId);
      return {
        ...withWorkspace(state, {
          ...ws,
          tabs,
          activeTabId,
          section: nextActive?.section ?? ws.section,
          selection: {
            projectId: nextActive?.section === "projects" ? nextActive.entityId : null,
            noteId: nextActive?.section === "knowledge" ? nextActive.entityId : null,
            fileId: nextActive?.section === "files" ? nextActive.entityId : null,
            automationId: nextActive?.section === "automations" ? nextActive.entityId : null,
            taskId: nextActive?.section === "tasks" ? nextActive.entityId : null,
          },
        }),
        closedTabs: tab ? [tab, ...state.closedTabs].slice(0, 12) : state.closedTabs,
      };
    }
    case "tab.pin": {
      return withWorkspace(state, {
        ...ws,
        tabs: ws.tabs.map((t) => (t.id === action.id ? { ...t, pinned: !t.pinned } : t)),
      });
    }
    case "tab.reopen": {
      const [tab, ...rest] = state.closedTabs;
      if (!tab) return state;
      return {
        ...withWorkspace(state, {
          ...ws,
          tabs: openTab(ws.tabs, tab),
          activeTabId: tab.id,
          section: tab.section,
        }),
        closedTabs: rest,
      };
    }
    case "tab.step": {
      const activeTabId = stepTab(ws.tabs, ws.activeTabId, action.dir);
      const tab = ws.tabs.find((t) => t.id === activeTabId);
      if (!tab) return state;
      return withWorkspace(state, { ...ws, activeTabId, section: tab.section });
    }
    case "tab.clear": {
      const first = ws.tabs[0];
      return withWorkspace(state, {
        ...ws,
        tabs: first ? [first] : [],
        activeTabId: first?.id ?? null,
        section: first?.section ?? ws.section,
      });
    }
    case "workspace.add": {
      const workspace = createWorkspace(uid("ws"), action.name, "home");
      return {
        ...state,
        workspaces: [...state.workspaces, workspace],
        activeWorkspaceId: workspace.id,
      };
    }
    case "workspace.switch": {
      if (!state.workspaces.some((w) => w.id === action.id)) return state;
      return { ...state, activeWorkspaceId: action.id };
    }
    case "workspace.remove": {
      if (state.workspaces.length <= 1) return state;
      const remaining = state.workspaces.filter((w) => w.id !== action.id);
      return {
        ...state,
        workspaces: remaining,
        activeWorkspaceId:
          state.activeWorkspaceId === action.id
            ? remaining[0].id
            : state.activeWorkspaceId,
      };
    }
    case "workspace.rename": {
      return {
        ...state,
        workspaces: state.workspaces.map((w) =>
          w.id === action.id ? { ...w, name: action.name } : w,
        ),
      };
    }
    default:
      return state;
  }
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
    case "tab.open":
    case "tab.activate":
    case "tab.close":
    case "tab.pin":
    case "tab.reopen":
    case "tab.step":
    case "tab.clear":
    case "workspace.add":
    case "workspace.switch":
    case "workspace.remove":
    case "workspace.rename":
    case "navigate":
      return workspaceReducer(state, action);
    case "wizard":
      return touch({ ...state, ...action.patch });
    case "reset":
      return buildSeed();
    default:
      return state;
  }
}

export interface Selection {
  projectId?: string | null;
  taskId?: string | null;
  noteId?: string | null;
  fileId?: string | null;
  automationId?: string | null;
}

interface Ctx {
  state: OrbitState;
  dispatch: React.Dispatch<Action>;
  section: SectionId;
  setSection: (s: SectionId) => void;
  selection: Selection;
  select: (patch: Selection) => void;
  /** Open a specific entity as its own tab. */
  openEntity: (section: SectionId, entityId: string, label?: string) => void;
  workspace: Workspace;
  tabs: WorkspaceTab[];
  activeTabId: string | null;
  closeTab: (id: string) => void;
  reopenTab: () => void;
  commandOpen: boolean;
  setCommandOpen: (v: boolean) => void;
  hydrated: boolean;
}

const StoreContext = createContext<Ctx | null>(null);

export function StoreProvider({
  children,
  initialSection,
}: {
  children: React.ReactNode;
  /** Test/deep-link seam: lets the shell boot straight into a section. */
  initialSection?: SectionId;
}) {
  const [state, dispatch] = useReducer(reducer, undefined, buildSeed);
  const [commandOpen, setCommandOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const persisted = useRef(false);
  const forcedSection = useRef<SectionId | null>(initialSection ?? null);
  const stateRef = useRef(state);
  stateRef.current = state;

  const workspace =
    state.workspaces.find((w) => w.id === state.activeWorkspaceId) ??
    state.workspaces[0];
  const currentTab = workspace ? activeTab(workspace) : null;
  const section: SectionId = forcedSection.current ?? currentTab?.section ?? "home";
  const selection: Selection = workspace?.selection ?? {};

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
      if (hash) dispatch({ type: "navigate", section: hash as SectionId });
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
    forcedSection.current = null;
    dispatch({ type: "navigate", section: s });
    window.history.replaceState(null, "", `#${s}`);
  }, []);

  /**
   * Selecting an entity opens (or focuses) a tab for it, so the workspace keeps
   * the views you were actually using.
   */
  const select = useCallback(
    (patch: Selection) => {
      const entries = Object.entries(patch).filter(([, v]) => v !== undefined) as [
        keyof Selection,
        string | null,
      ][];
      if (!entries.length) return;
      const [key, value] = entries[0];
      const map: Record<keyof Selection, SectionId> = {
        projectId: "projects",
        taskId: "tasks",
        noteId: "knowledge",
        fileId: "files",
        automationId: "automations",
      };
      const target = map[key];
      if (value) {
        dispatch({
          type: "tab.open",
          section: target,
          entityId: value,
          label: resolveTabLabel(stateRef.current, target, value),
        });
        window.history.replaceState(null, "", `#${target}`);
      } else {
        dispatch({ type: "navigate", section: target, selection: patch });
      }
    },
    [],
  );

  const openEntity = useCallback((target: SectionId, entityId: string, label?: string) => {
    dispatch({
      type: "tab.open",
      section: target,
      entityId,
      label: label ?? resolveTabLabel(stateRef.current, target, entityId),
    });
    window.history.replaceState(null, "", `#${target}`);
  }, []);

  const closeTabAction = useCallback((id: string) => dispatch({ type: "tab.close", id }), []);
  const reopenTab = useCallback(() => dispatch({ type: "tab.reopen" }), []);

  useEffect(() => {
    const onHash = () => {
      const hash = window.location.hash.replace("#", "") as SectionId;
      if (hash) {
        forcedSection.current = null;
        dispatch({ type: "navigate", section: hash });
      }
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
      openEntity,
      workspace,
      tabs: workspace?.tabs ?? [],
      activeTabId: workspace?.activeTabId ?? null,
      closeTab: closeTabAction,
      reopenTab,
      commandOpen,
      setCommandOpen,
      hydrated,
    }),
    [
      state,
      section,
      setSection,
      selection,
      select,
      openEntity,
      workspace,
      closeTabAction,
      reopenTab,
      commandOpen,
      hydrated,
    ],
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
