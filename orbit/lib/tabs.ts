/**
 * Tab and workspace algebra, kept pure so it can be asserted directly by the
 * smoke suite instead of only observed through the UI.
 */

import type {
  OrbitState,
  SectionId,
  Workspace,
  WorkspaceTab,
} from "./types";

export const SECTION_LABEL: Record<SectionId, string> = {
  home: "Home",
  assistant: "Assistant",
  projects: "Projects",
  tasks: "Tasks",
  calendar: "Calendar",
  knowledge: "Knowledge",
  files: "Files",
  automations: "Automations",
  integrations: "Integrations",
  activity: "Activity",
  settings: "Settings",
};

export function tabId(section: SectionId, entityId: string | null) {
  return `${section}:${entityId ?? ""}`;
}

/** Human label for a tab, resolved against live state. */
export function resolveTabLabel(
  state: Pick<OrbitState, "projects" | "notes" | "files" | "automations" | "conversations" | "tasks">,
  section: SectionId,
  entityId: string | null,
): string {
  if (!entityId) return SECTION_LABEL[section];
  switch (section) {
    case "projects":
      return state.projects.find((p) => p.id === entityId)?.name ?? SECTION_LABEL[section];
    case "knowledge":
      return state.notes.find((n) => n.id === entityId)?.title ?? SECTION_LABEL[section];
    case "files":
      return state.files.find((f) => f.id === entityId)?.name ?? SECTION_LABEL[section];
    case "automations":
      return state.automations.find((a) => a.id === entityId)?.name ?? SECTION_LABEL[section];
    case "tasks":
      return state.tasks.find((t) => t.id === entityId)?.title ?? SECTION_LABEL[section];
    case "assistant":
      return state.conversations.find((c) => c.id === entityId)?.title ?? SECTION_LABEL[section];
    default:
      return SECTION_LABEL[section];
  }
}

/** Opening an existing tab must focus it, never duplicate it. */
export function openTab(
  tabs: WorkspaceTab[],
  tab: WorkspaceTab,
  maxTabs = 12,
): WorkspaceTab[] {
  const existing = tabs.findIndex((t) => t.id === tab.id);
  if (existing >= 0) {
    const next = [...tabs];
    next[existing] = { ...next[existing], label: tab.label };
    return next;
  }
  const next = [...tabs, tab];
  // Keep pinned tabs, drop the oldest unpinned tab past the cap.
  const unpinned = next.filter((t) => !t.pinned);
  if (next.length > maxTabs && unpinned.length) {
    const oldest = unpinned[0];
    return next.filter((t) => t.id !== oldest.id);
  }
  return next;
}

export function closeTab(
  tabs: WorkspaceTab[],
  id: string,
  activeTabId: string | null,
): { tabs: WorkspaceTab[]; activeTabId: string | null } {
  const index = tabs.findIndex((t) => t.id === id);
  const next = tabs.filter((t) => t.id !== id);
  if (activeTabId !== id) return { tabs: next, activeTabId };
  const fallback = next[Math.min(index, next.length - 1)];
  return { tabs: next, activeTabId: fallback?.id ?? null };
}

export function sectionForTab(section: SectionId) {
  return section;
}

export function createWorkspace(id: string, name: string, section: SectionId = "home"): Workspace {
  return {
    id,
    name,
    tabs: [
      {
        id: tabId(section, null),
        section,
        entityId: null,
        label: SECTION_LABEL[section],
        pinned: false,
      },
    ],
    activeTabId: tabId(section, null),
    section,
    selection: {},
  };
}

/** The active tab, or a synthesised one so the UI never renders into a void. */
export function activeTab(workspace: Workspace): WorkspaceTab {
  return (
    workspace.tabs.find((t) => t.id === workspace.activeTabId) ??
    workspace.tabs[0] ?? {
      id: tabId(workspace.section, null),
      section: workspace.section,
      entityId: null,
      label: SECTION_LABEL[workspace.section],
      pinned: false,
    }
  );
}

/** Cycle through tabs, wrapping. Used by ctrl-tab. */
export function stepTab(tabs: WorkspaceTab[], activeId: string | null, dir: 1 | -1) {
  if (tabs.length < 2) return activeId;
  const i = tabs.findIndex((t) => t.id === activeId);
  const next = (i + dir + tabs.length) % tabs.length;
  return tabs[next].id;
}
